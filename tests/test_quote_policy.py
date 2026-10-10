import unittest
from dataclasses import replace
from datetime import datetime
from decimal import Decimal as D
from zoneinfo import ZoneInfo
from arcus_mm.config import Config, load
from arcus_mm.strategy import QuotePolicy, targets, safe_resting, fair_value, inventory_shift
from arcus_mm.models import Order
from tests.test_mm import market, book

class QuotePolicyTests(unittest.TestCase):
    def test_spy_session_spread_handles_open_regular_close_and_dst(self):
        ny=ZoneInfo("America/New_York")
        c=replace(Config(),market="SPY-USD",adaptive_spread=True,session_spread_enabled=True,
                  spread_bps="1.3",max_spread_bps="5")
        cases=[
            (datetime(2026,7,6,3,0,tzinfo=ny),"1.3","OFF_HOURS"),
            (datetime(2026,7,6,8,0,tzinfo=ny),"1.5","EXTENDED"),
            (datetime(2026,7,6,9,25,tzinfo=ny),"2.0","OPEN"),
            (datetime(2026,7,6,10,30,tzinfo=ny),"1.5","REGULAR"),
            (datetime(2026,7,6,15,50,tzinfo=ny),"1.8","CLOSE"),
            (datetime(2026,7,6,17,0,tzinfo=ny),"1.5","EXTENDED"),
            (datetime(2026,7,5,10,0,tzinfo=ny),"1.3","OFF_HOURS"),
            (datetime(2026,12,7,9,30,tzinfo=ny),"2.0","OPEN"),
        ]
        for now,spread,session in cases:
            with self.subTest(now=now):
                policy=QuotePolicy(clock=lambda now=now:now)
                effective=policy.effective(c,book())
                self.assertEqual(D(effective.spread_bps),D(spread))
                self.assertEqual(policy.state["market_session"],session)

    def test_session_spread_is_opt_in_and_spy_only(self):
        now=datetime(2026,7,6,9,30,tzinfo=ZoneInfo("America/New_York"))
        policy=QuotePolicy(clock=lambda:now)
        effective=policy.effective(replace(Config(),adaptive_spread=True),book())
        self.assertEqual(D(effective.spread_bps),D(2))
        self.assertEqual(policy.state["market_session"],"DISABLED")

    def test_open_session_tightens_momentum_filter(self):
        now=datetime(2026,7,6,9,30,tzinfo=ZoneInfo("America/New_York"))
        c=replace(Config(),market="SPY-USD",adaptive_spread=True,session_spread_enabled=True,
                  max_spread_bps="5",momentum_filter_enabled=True,momentum_threshold_bps="1.5",
                  session_open_momentum_threshold_bps="0.8")
        policy=QuotePolicy(clock=lambda:now)
        for stamp,mid in ((1,"100000"),(2,"100000"),(3,"100000"),(4,"100009")):
            b=book(str(D(mid)-1),str(D(mid)+1));b.received=stamp
            policy.effective(c,b)
        self.assertEqual(policy.state["momentum_threshold_bps"],D("0.8"))
        self.assertEqual(policy.state["blocked_sides"],["SELL"])

    def test_inventory_moves_both_quotes_towards_reduction(self):
        c=replace(Config(),inventory_skew_bps="2",order_equity_fraction="0.2")
        neutral=targets(c,market(),book(),D(100),D(0))
        for position,direction in [(D("0.0002"),-1),(D("-0.0002"),1)]:
            quotes=targets(c,market(),book(),D(100),position)
            self.assertEqual(len(quotes),2)
            for old,new in zip(neutral,quotes): self.assertGreater((new.price-old.price)*direction,0)
            self.assertTrue(safe_resting(c,D(100),position,book(),[Order(q,"test",q.qty,0) for q in quotes]))

    def test_high_volatility_widens_and_removes_grid_outer_level(self):
        c=replace(Config(),adaptive_spread=True,strategy="grid")
        policy=QuotePolicy()
        for i in range(6):
            b=book(str(99999+i*100),str(100001+i*100));b.received=float(i)
            effective=policy.effective(c,b)
        self.assertEqual(D(effective.spread_bps),D(8))
        self.assertEqual(D(effective.reprice_bps),D(2))
        self.assertEqual(effective.strategy,"mid")
        b.received=40
        effective=policy.effective(c,b)
        self.assertEqual(D(effective.spread_bps),D(2))
        self.assertEqual(effective.strategy,"grid")
        self.assertEqual(policy.state["samples"],1)

    def test_wide_book_and_fee_floor(self):
        c=replace(Config(),adaptive_spread=True,exit_fee_reserve_fraction="1")
        policy=QuotePolicy();effective=policy.effective(c,book("99900","100100"))
        self.assertEqual(D(effective.spread_bps),D(8))
        effective=policy.effective(c,book(),D("0.0001"),D("0.000225"))
        self.assertEqual(D(effective.spread_bps),D("3.25"))
        effective=QuotePolicy().effective(c,book(),D("-0.0001"),D(0))
        self.assertEqual(D(effective.spread_bps),D(2))

    def test_aggressive_skew_keeps_passive_reducing_side(self):
        c=replace(Config(),spread_bps="1.2",inventory_skew_bps="2",order_equity_fraction=".2")
        for position,side in [(D(".0004"),"SELL"),(D("-.0004"),"BUY")]:
            qs=targets(c,market(),book(),D(100),position)
            reducing=[q for q in qs if q.side==side]
            self.assertTrue(reducing)
            self.assertTrue(all(q.price>book().bid if side=="SELL" else q.price<book().ask for q in reducing))
            self.assertTrue(safe_resting(c,D(100),position,book(),[Order(q,"test",q.qty,0) for q in qs]))

    def test_microprice_direction_bound_and_zero_weight_baseline(self):
        c=replace(Config(),microprice_weight="1",max_fair_shift_bps="1")
        b=replace(book("99900","100100"),bid_size=D(9),ask_size=D(1))
        f=fair_value(c,b)
        self.assertEqual(f["microprice"],D(100080))
        self.assertEqual(f["l1_imbalance"],D(".8"))
        self.assertEqual(f["fair_shift_bps"],D(1))
        self.assertEqual(fair_value(replace(c,microprice_weight="0"),b)["fair_price"],b.mid)
        reverse=fair_value(c,replace(b,bid_size=D(1),ask_size=D(9)))
        self.assertEqual(reverse["fair_shift_bps"],D(-1))

    def test_microprice_requires_two_seconds_of_one_way_confirmation(self):
        c=replace(Config(),microprice_weight="1",max_fair_shift_bps=".4",microprice_confirm_seconds=2)
        policy=QuotePolicy()
        positive=replace(book("99900","100100"),bid_size=D(9),ask_size=D(1),received=10)
        negative=replace(positive,bid_size=D(1),ask_size=D(9),received=11)
        self.assertEqual(D(policy.effective(c,positive).microprice_weight),D(0))
        self.assertEqual(D(policy.effective(c,replace(positive,received=11.99)).microprice_weight),D(0))
        self.assertEqual(D(policy.effective(c,negative).microprice_weight),D(0))
        self.assertEqual(D(policy.effective(c,replace(negative,received=13)).microprice_weight),D(1))
        effective=policy.effective(c,replace(negative,received=13.1))
        self.assertEqual(fair_value(effective,negative)["fair_shift_bps"],D("-.4"))

    def test_cubic_skew_is_small_near_zero_and_stronger_near_cap(self):
        c=replace(Config(),inventory_skew_bps="2",inventory_cubic_bps="1")
        self.assertEqual(inventory_shift(c,D(".1")),D("-.201"))
        self.assertEqual(inventory_shift(c,D(1)),D(-3))
        self.assertEqual(inventory_shift(c,D(-1)),D(3))
        self.assertEqual(inventory_shift(c,D(2)),D(-3))

    def test_duplicate_frames_do_not_build_volatility_history(self):
        policy=QuotePolicy();b=book();b.received=1
        for _ in range(20): policy.effective(Config(),b)
        self.assertEqual(len(policy.samples),1)

    def test_invalid_controls_and_live_leverage(self):
        for name,value in [("inventory_skew_bps","4.1"),("max_spread_bps","1"),("exit_fee_reserve_fraction","2"),("adaptive_spread",1),("volatility_multiplier","NaN")]:
            with self.subTest(name=name),self.assertRaises(ValueError): replace(Config(),**{name:value}).validate()
        self.assertEqual(load("mm_live_btc_100.json").leverage_cap,2)

    def test_live_wear_first_configuration(self):
        c=load("mm_live_btc_100.json")
        expected={"max_reprice_bps":"2","max_fair_shift_bps":".4","inventory_skew_bps":"4",
                  "inventory_cubic_bps":"2","order_notional_min":"50","order_notional_max":"100",
                  "toxicity_max_premium_bps":"4","spread_decay_bps_per_second":".02"}
        for name,value in expected.items(): self.assertEqual(D(getattr(c,name)),D(value),name)
        self.assertEqual(c.microprice_confirm_seconds,2)
        self.assertTrue(c.momentum_filter_enabled)
        self.assertEqual(c.max_order_age_seconds,10)
        self.assertEqual(D(c.max_position_equity_fraction),D(".25"))

    def test_momentum_filter_blocks_adverse_opening_side_and_keeps_reduction(self):
        c=replace(Config(),momentum_filter_enabled=True,momentum_window_seconds=3,momentum_threshold_bps="2",
                  max_position_equity_fraction=".15",order_notional_min="20",order_notional_max="20")
        policy=QuotePolicy()
        for stamp,mid in ((1,"100000"),(2,"100000"),(3,"100000"),(4,"100030")):
            b=book(str(D(mid)-1),str(D(mid)+1));b.received=stamp
            effective=policy.effective(c,b)
        self.assertEqual(policy.state["blocked_sides"],["SELL"])
        neutral=targets(effective,market(),b,D(500),D(0),blocked_sides=policy.state["blocked_sides"])
        self.assertEqual({q.side for q in neutral},{"BUY"})
        reducing=targets(effective,market(),b,D(500),D(".0004"),blocked_sides=policy.state["blocked_sides"])
        self.assertIn("SELL",{q.side for q in reducing})

    def test_half_cap_inventory_uses_only_passive_reduce_quote_at_touch(self):
        c=replace(Config(),max_position_equity_fraction=".15",order_notional_min="40",order_notional_max="80")
        b=book()
        cap=D(500)*D(".15")/b.mid
        for position,side in ((cap*D(".5"),"SELL"),(-cap*D(".5"),"BUY")):
            qs=targets(c,market(),b,D(500),position)
            self.assertEqual(len(qs),1)
            self.assertEqual(qs[0].side,side)
            self.assertTrue(qs[0].reduce)
            self.assertLess(qs[0].qty,abs(position)+market().step)
            self.assertLess(qs[0].price,b.ask)
            self.assertGreater(qs[0].price,b.bid)

    def test_inventory_becomes_one_sided_at_configured_ratio(self):
        c=replace(Config(),inventory_one_sided_ratio=".3",max_position_equity_fraction=".15",
                  order_notional_min="20",order_notional_max="20")
        b=book();cap=D(500)*D(".15")/b.mid
        below=targets(c,market(),b,D(500),cap*D(".29"))
        self.assertEqual({q.side for q in below},{"BUY","SELL"})
        for position,side in ((cap*D(".3"),"SELL"),(-cap*D(".3"),"BUY")):
            qs=targets(c,market(),b,D(500),position)
            self.assertEqual({q.side for q in qs},{side})
            self.assertTrue(all(q.reduce for q in qs))
            self.assertLessEqual(sum(q.qty for q in qs),abs(position))

    def test_competitive_quotes_join_bbo_only_in_calm_market(self):
        now=datetime(2026,7,6,10,30,tzinfo=ZoneInfo("America/New_York"))
        c=replace(Config(),market="SPY-USD",adaptive_spread=True,session_spread_enabled=True,
                  competitive_quotes_enabled=True,competitive_max_volatility_bps="0.15",
                  max_spread_bps="5",spread_bps="1")
        policy=QuotePolicy(clock=lambda:now)
        b=book("99.9","100.1")
        effective=policy.effective(c,b)
        self.assertTrue(effective.competitive_quote_active)
        qs=targets(effective,market(),b,D(500),D(0))
        self.assertEqual({q.side:q.price for q in qs},{"BUY":b.bid,"SELL":b.ask})
        open_policy=QuotePolicy(clock=lambda:datetime(2026,7,6,9,30,tzinfo=ZoneInfo("America/New_York")))
        self.assertFalse(open_policy.effective(c,b).competitive_quote_active)

if __name__=="__main__": unittest.main()
