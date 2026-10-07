import unittest
from dataclasses import replace
from decimal import Decimal as D
from arcus_mm.config import Config
from arcus_mm.quality import Markouts
from arcus_mm.features import EWMAVolatility
from arcus_mm.strategy import QuotePolicy,targets,safe_resting
from arcus_mm.models import Order
from tests.test_mm import market,book

def frame(t,mid=100):
    b=book(str(D(str(mid))-D(".01")),str(D(str(mid))+D(".01")))
    b.timestamp_us=int(t*1000000);b.received=float(t)
    return b

def fill(side="BUY",stamp=0,price="99.99",trade="t"):
    return dict(trade_id=trade,side=side,timestamp_us=int(stamp*1000000),price=price,quantity="1",fee=".01",maker=True,spread_bps="1.2")

class QualityTests(unittest.TestCase):
    def setUp(self):
        self.events=[];self.c=replace(Config(),toxicity_min_samples=3)
        self.q=Markouts(self.c,lambda event,**values:self.events.append(dict(event=event,**values)))
    def test_signs_fees_horizons_and_actual_delay(self):
        self.q.observe(frame(0));self.q.add_fill(fill());self.q.add_fill(fill("SELL",price="100.01",trade="s"))
        self.q.observe(frame(1.25,99.98))
        rows=[e for e in self.events if e["event"]=="markout"]
        self.assertEqual(len(rows),2)
        buy,sell=rows
        self.assertLess(buy["mid_move_bps"],0);self.assertGreater(sell["mid_move_bps"],0)
        self.assertLess(buy["net_markout_bps"],buy["markout_bps"])
        self.assertEqual(buy["sample_lag_seconds"],.25)
        self.q.observe(frame(3));self.q.observe(frame(5));self.q.observe(frame(10));self.q.observe(frame(30))
        self.assertEqual(self.q.completed,10);self.assertEqual(len(self.q.pending),0)
    def test_missing_is_not_zero_and_late_fill_uses_causal_buffer(self):
        self.q.observe(frame(0));self.q.observe(frame(1.2,99.9));self.q.observe(frame(3.1,99.8))
        self.q.add_fill(fill())
        self.assertEqual(self.q.completed,2)
        self.q.observe(frame(8,99.7))
        self.assertEqual(self.q.missing,1)
        self.q.finish();self.assertEqual(self.q.missing,3)
    def test_no_prefill_frame_is_skipped_not_future_reference(self):
        self.q.observe(frame(2));self.q.add_fill(fill())
        self.assertEqual(self.q.skipped,1);self.assertEqual(self.q.completed,0)
    def test_toxicity_requires_samples_expires_and_uses_mid_move(self):
        self.q.observe(frame(0))
        for i in range(3):self.q.add_fill(fill(price="99",trade=str(i)))
        self.q.observe(frame(5,99.98))
        self.assertGreater([r for r in self.q.recent][0]["markout_bps"],0)
        score=self.q.toxicity(5)["BUY"]
        self.assertTrue(score["ready"]);self.assertGreater(score["adverse_bps"],0)
        self.assertFalse(self.q.toxicity(5)["SELL"]["ready"])
        self.assertFalse(self.q.toxicity(306)["BUY"]["ready"])
    def test_longer_horizons_do_not_evict_toxicity_samples(self):
        self.q.observe(frame(0))
        for i in range(1000):self.q.add_fill(fill(trade=str(i)))
        for t in (1,3,5,10,30):self.q.observe(frame(t,99.98))
        self.assertEqual(self.q.toxicity(30)["BUY"]["samples"],1000)
        self.assertTrue(self.q.toxicity(30)["BUY"]["ready"])

    def test_taker_exit_is_not_a_maker_markout(self):
        self.q.observe(frame(0));self.q.add_fill(dict(fill(),maker=False));self.assertEqual(len(self.q.pending),0)

class EWMAAndSpreadTests(unittest.TestCase):
    def test_half_lives_fast_response_slow_decay_and_gap_reset(self):
        f=EWMAVolatility();f.observe(frame(0),10);f.observe(frame(1,100.1),10)
        values=f.snapshot();self.assertGreater(values["2"],values["10"]);self.assertGreater(values["10"],values["60"])
        initial=dict(values);f.observe(frame(2,100.1),10)
        self.assertAlmostEqual(float(f.snapshot()["2"]/initial["2"]),2**(-.25))
        f.observe(frame(20,100.1),10);self.assertEqual(max(f.snapshot().values()),0)
    def test_toxicity_is_side_specific_and_slow_narrowing(self):
        c=replace(Config(),adaptive_spread=True,volatility_mode="ewma",toxicity_enabled=True,spread_decay_bps_per_second=".1",order_equity_fraction=".2")
        p=QuotePolicy();b=frame(0,100000)
        toxicity={"BUY":dict(ready=True,samples=10,adverse_bps=D(1)),"SELL":dict(ready=False,samples=0)}
        effective=p.effective(c,b,toxicity=toxicity)
        self.assertEqual(p.side_spreads,{"BUY":D(3),"SELL":D(2)})
        qs=targets(effective,market(),b,D(100),D(0),p.side_spreads)
        self.assertTrue(safe_resting(c,D(100),D(0),b,[Order(q,"test",q.qty,0) for q in qs]))
        p.effective(c,frame(1,100000))
        self.assertEqual(p.side_spreads["BUY"],D("2.9"))
        self.assertEqual(p.side_spreads["SELL"],D(2))
    def test_fee_floor_is_never_narrowed_by_spread_cap(self):
        c=replace(Config(),volatility_mode="ewma",adaptive_spread=True,max_spread_bps="3",exit_fee_reserve_fraction="1")
        p=QuotePolicy();effective=p.effective(c,frame(0),D(".0002"),D(".000225"))
        self.assertGreaterEqual(D(effective.spread_bps),D("4.25"))

class ObserverIntegration(unittest.IsolatedAsyncioTestCase):
    async def test_live_replayed_fills_do_not_enter_new_session_quality(self):
        from arcus_mm.api import PublicAPI
        from arcus_mm.engine import Engine
        from types import SimpleNamespace
        api=PublicAPI(Config(),lambda *a,**k:None)
        e=Engine(api.c,api,api.log,live=True);e.venue=SimpleNamespace()
        api.stamp=lambda:10000000000
        try:
            e.attach_observers();e.observe_book(frame(10))
            e.trading_enabled=True
            e.venue.on_fill(fill(stamp=9))
            self.assertEqual(e.quality.skipped,0);self.assertEqual(len(e.quality.pending),0)
            e.venue.on_fill(fill(stamp=10))
            self.assertEqual(len(e.quality.pending),1)
            e.trading_enabled=False;e.venue.on_fill(fill(stamp=10,trade="stopped"))
            self.assertEqual(len(e.quality.pending),1)
        finally:await api.http.aclose()

    async def test_paper_fill_keeps_timestamp_fee_and_spread_context(self):
        from arcus_mm.paper import Paper
        from arcus_mm.models import Ledger,Quote
        observed=[];c=Config();p=Paper(c,market(),Ledger(100),lambda *a,**k:None,D(".0001"),D(".000225"))
        p.on_fill=observed.append
        q=Quote("BUY-1","BUY",D(100),D(".01"),False,D("1.2"))
        p.fill(q,q.qty,q.price,True,timestamp_us=123,trade_id="public")
        self.assertEqual(observed[0]["timestamp_us"],123)
        self.assertEqual(observed[0]["fee"],D(".0001"))
        self.assertEqual(observed[0]["spread_bps"],D("1.2"))
        p.fill(q,q.qty,q.price,False,timestamp_us=124,trade_id="exit")
        self.assertEqual(len(observed),1)
