import asyncio
import json
import os
import time
import unittest
from dataclasses import replace
from decimal import Decimal as D
from types import SimpleNamespace
from unittest.mock import patch, AsyncMock
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, NoEncryption
from arcus_mm.config import Config
from arcus_mm.models import Market, Book, Quote, Order, Ledger
from arcus_mm.signing import typed, canonical, Signer
from arcus_mm.strategy import targets, replace_needed, safe_resting
from arcus_mm.paper import Paper
from arcus_mm.api import Live, PublicAPI, ExchangeError, UncertainOrder, ClockUnavailable
from arcus_mm.engine import Engine

ADDRESS="0x"+"Ab"*20

def market(): return Market(1,D("0.1"),D("0.00000001"),D("0.0001"),D(5),D(100),[])
def book(bid="99999",ask="100001",stamp=None):
    return Book(D(bid),D(ask),D(1),D(1),stamp or time.time_ns()//1000,time.monotonic())
def log(*args,**kwargs): pass

def credentials():
    # Ephemeral test-only seed, never a hardcoded credential.
    key=Ed25519PrivateKey.generate()
    seed=key.private_bytes(Encoding.Raw,PrivateFormat.Raw,NoEncryption()).hex()
    return patch.dict(os.environ,{"ARCUS_ADDRESS":ADDRESS,"ARCUS_API_PRIVATE_KEY":seed,"ARCUS_ACCOUNT_INDEX":"0"})

class Configuration(unittest.TestCase):
    def test_defaults_paper_and_three(self):
        c=Config(); c.validate(); self.assertTrue(c.paper); self.assertEqual(c.leverage_cap,3)
    def test_five_allowed_six_rejected(self):
        replace(Config(),leverage_cap=5).validate()
        with self.assertRaises(ValueError): replace(Config(),leverage_cap=6).validate()
    def test_high_leverage_rejected(self):
        with self.assertRaises(ValueError): replace(Config(),leverage_cap=40).validate()
    def test_invalid_values(self):
        for name,value in [("strategy","smart"),("reprice_bps","NaN"),("reprice_bps","3"),("market","SOL-USD"),("bias_bps","4"),("paper",1),
                           ("order_equity_fraction","NaN"),("disconnect_seconds",0),("max_position_equity_fraction","4")]:
            with self.subTest(name=name),self.assertRaises((ValueError,ArithmeticError)):
                replace(Config(),**{name:value}).validate()

class Signing(unittest.TestCase):
    def payload(self):
        return dict(address=ADDRESS,accountIndex=0,marketId=1,clientId="MiXeD",price="50000.1",
                    quantity="0.00012345",goodTilTime="2000000000000000",orderSide="BUY",timeInForce="ALO",reduceOnly=False,timestamp=123)
    def test_exact_canonical_official_vector(self):
        expected={"ad":ADDRESS.lower(),"ai":0,"c":"MiXeD","ct":123,"g":2000000000000000000,
                  "m":1,"op":1,"p":500001,"q":12345,"r":0,"s":0,"t":3,"v":1}
        self.assertEqual(typed("placeOrder",self.payload(),123,market()),canonical(expected))
    def test_ioc_reduce_enum(self):
        b=self.payload(); b.update(orderSide="SELL",timeInForce="IOC",reduceOnly=True)
        p=json.loads(typed("placeOrder",b,123,market())); self.assertEqual((p["s"],p["t"],p["r"]),(1,2,1))
    def test_empty_client_omitted(self):
        b=self.payload(); b["clientId"]=""; self.assertNotIn("c",json.loads(typed("placeOrder",b,123,market())))
    def test_cancel_exactly_one_and_preserve_case(self):
        b=dict(address=ADDRESS,accountIndex=0,marketId=1,orderId="AbCd")
        self.assertEqual(json.loads(typed("cancelOrder",b,123,market()))["id"],"AbCd")
        b["clientId"]="MIX"
        with self.assertRaises(ValueError): typed("cancelOrder",b,123,market())
        del b["orderId"]; self.assertEqual(json.loads(typed("cancelOrder",b,123,market()))["c"],"MIX")
    def test_alignment_and_timestamp(self):
        b=self.payload(); b["price"]="50000.11"
        with self.assertRaises(ValueError): typed("placeOrder",b,123,market())
        b=self.payload(); b["timestamp"]=122
        with self.assertRaises(ValueError): typed("placeOrder",b,123,market())
    def test_signature_verifies_scheme1_and_legacy(self):
        with credentials(): signer=Signer()
        for action,body in [("placeOrder",self.payload()),("setLeverage",{"address":ADDRESS.lower(),"marketId":1,"leverage":3}),
                            ("scheduleCancel",{"address":ADDRESS.lower(),"accountIndex":0,"time":2000000000000000})]:
            h=signer.headers(action,body,123,market())
            msg=typed(action,body,123,market()) if action=="placeOrder" else b"123"+action.encode()+canonical(body)
            signer.key.public_key().verify(bytes.fromhex(h["X-Signature"]),msg)
            self.assertEqual(len(h["X-Signature"]),128)

class Strategy(unittest.TestCase):
    def test_mid_two_grid_four(self):
        for strategy,count in [("mid",2),("grid",4)]:
            qs=targets(replace(Config(),strategy=strategy,order_equity_fraction="0.2"),market(),book(),D(100),D(0))
            self.assertEqual(len(qs),count)
            self.assertTrue(all(q.price<book().ask if q.side=="BUY" else q.price>book().bid for q in qs))
    def test_absolute_order_notional_is_randomized_within_bounds(self):
        c=replace(Config(),order_notional_min="20",order_notional_max="30",max_position_equity_fraction=".3")
        with patch("arcus_mm.strategy.secrets.randbelow",side_effect=[0,1000]):
            qs=targets(c,market(),book(),D(100),D(0))
        notionals={q.side:q.qty*q.price for q in qs}
        self.assertGreaterEqual(notionals["BUY"],D("19.99"))
        self.assertLessEqual(notionals["BUY"],D("20"))
        self.assertGreaterEqual(notionals["SELL"],D("29.99"))
        self.assertLessEqual(notionals["SELL"],D("30"))
    def test_order_notional_bounds_validate_together(self):
        with self.assertRaises(ValueError):replace(Config(),order_notional_min="20").validate()
        with self.assertRaises(ValueError):replace(Config(),order_notional_min="30",order_notional_max="20").validate()
    def test_only_reduce_when_over_cap(self):
        for pos,side in [(D("0.001"),"SELL"),(D("-0.001"),"BUY")]:
            qs=targets(replace(Config(),order_equity_fraction="0.2"),market(),book(),D(100),pos)
            self.assertTrue(qs); self.assertTrue(all(q.side==side and q.reduce for q in qs))
            self.assertLessEqual(sum(q.qty for q in qs),abs(pos))
    def test_grid_worst_case_reserved(self):
        c=replace(Config(),strategy="grid",order_equity_fraction="0.5")
        qs=targets(c,market(),book(),D(100),D(0))
        orders=[Order(q,"test",q.qty,0) for q in qs]
        self.assertTrue(safe_resting(c,D(100),D(0),book(),orders))
        for side in ("BUY","SELL"):
            self.assertLessEqual(sum(q.qty for q in qs if q.side==side),D("0.0005"))
    def test_one_tick_does_not_replace(self):
        q=Quote("x","BUY",D(100),D(1))
        self.assertFalse(replace_needed(q,replace(q,price=D("100.1")),D("0.1")))
        self.assertTrue(replace_needed(q,replace(q,price=D("100.2")),D("0.1")))
    def test_reprice_threshold_preserves_small_moves_and_reduce_changes(self):
        q=Quote("x","BUY",D(85000),D(1))
        self.assertFalse(replace_needed(q,replace(q,price=D("85000.2")),D("0.1"),"0.5"))
        self.assertFalse(replace_needed(q,replace(q,price=D("85004.25")),D("0.1"),"0.5"))
        self.assertTrue(replace_needed(q,replace(q,price=D("85004.3")),D("0.1"),"0.5"))
        self.assertTrue(replace_needed(q,replace(q,reduce=True),D("0.1"),"0.5"))

    def test_below_minimum_not_upsized(self):
        self.assertEqual(targets(Config(),market(),book(),D(5),D(0)),[])
    def test_tier_boundary_exclusive(self):
        m=market(); m.tiers=[{"upToPrice":"100","tick":"0.1"},{"tick":"1"}]
        self.assertEqual(m.price(D("100.2"),"BUY"),D(100))
        self.assertEqual(m.price(D("99.99"),"SELL"),D(100))

class PaperExecution(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.c=replace(Config(),paper_latency_ms=0)
        self.l=Ledger(100); self.p=Paper(self.c,market(),self.l,log,D(0),D("0.000225"))
        self.b=book("99","101")
    async def test_crossing_post_only_rejected(self):
        with self.assertRaises(ValueError): await self.p.place(Quote("x","BUY",D(101),D("0.01")),self.b)
    async def test_fill_never_better_than_opponent_and_dedup(self):
        await self.p.place(Quote("x","BUY",D(100),D("0.01")),self.b)
        b=book("98","99",self.b.timestamp_us+1)
        row=dict(tradeId="1",price="99",size="0.004",timestamp=b.timestamp_us)
        self.p.trades([row],b); self.assertEqual(self.l.qty,D("0.004")); self.assertEqual(self.l.entry,D(100))
        self.p.trades([row],b); self.assertEqual(self.l.qty,D("0.004"))
    async def test_no_trade_touch_or_optimistic_bbo_fill(self):
        await self.p.place(Quote("x","BUY",D(100),D("0.01")),self.b)
        for px in ["100","99"]:
            self.p.trades([dict(tradeId=px,price=px,size="0.01",timestamp=self.b.timestamp_us)],self.b)
        self.assertEqual(self.l.qty,0)
    async def test_shared_print_volume_not_reused(self):
        await self.p.place(Quote("x","BUY",D(100),D("0.01")),self.b)
        await self.p.place(Quote("y","BUY",D("99.5"),D("0.01")),self.b)
        b=book("98","99",self.b.timestamp_us+1)
        self.p.trades([dict(tradeId="one",price="98",size="0.005",timestamp=b.timestamp_us)],b)
        self.assertEqual(self.l.qty,D("0.005"))
    async def test_ioc_partial_opponent_price_and_limit(self):
        self.l.fill("BUY",D("0.02"),D(100),D(0))
        b=book("99","101"); b.bid_size=D("0.005")
        await self.p.place(Quote("exit","SELL",D(98),D("0.02"),True),b,True)
        self.assertEqual(self.l.qty,D("0.015")); self.assertLess(self.l.realized,0)
    async def test_reduce_only_cannot_flip(self):
        self.l.fill("BUY",D("0.003"),D(100),D(0))
        await self.p.place(Quote("x","SELL",D(102),D("0.01"),True),self.b)
        b=book("103","104",self.b.timestamp_us+1)
        self.p.trades([dict(tradeId="r",price="103",size="0.01",timestamp=b.timestamp_us)],b)
        self.assertEqual(self.l.qty,0)

class LedgerTests(unittest.TestCase):
    def test_partial_close_and_flip(self):
        l=Ledger(100); l.fill("BUY",D(2),D(10),D("0.1")); l.fill("SELL",D(3),D(12),D("0.2"))
        self.assertEqual((l.qty,l.entry,l.realized),(-1,12,4)); self.assertEqual(l.equity(D(11)),D("104.7"))

class LiveLifecycle(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.c=replace(Config(),confirmation_seconds=.02)
        self.api=PublicAPI(self.c,log); self.api.market=market()
        with credentials(): self.v=Live(self.api,Ledger(100),log)
    async def asyncTearDown(self): await self.api.http.aclose()
    async def test_five_second_disconnect_arm_has_network_headroom(self):
        self.v.c=replace(self.c,disconnect_seconds=5)
        self.v.post=AsyncMock(return_value={})
        with patch.object(self.api,"stamp",return_value=100000000000):
            await self.v.arm()
        action,body=self.v.post.await_args.args
        self.assertEqual(action,"scheduleCancel")
        self.assertEqual(body["time"],280000000)
        self.assertEqual(self.v.c.disconnect_seconds,5)
    async def test_live_budget_does_not_use_entire_wallet(self):
        self.v.c=replace(self.c,capital_cap="100")
        account=dict(positions={},equity="1000",freeCollateral="1000",sequenceNumber=0)
        async def get(path,params=None):
            if path=="/v1/apiKeys":
                return {"apiKeys":[dict(apiKey=self.v.signer.public,status="ACTIVE",accountIndex=0,validUntil=0)]}
            if path=="/v1/account": return account
            if path=="/v1/openOrders": return {"orders":[]}
            if path=="/v1/fills": return {"fills":[]}
            raise AssertionError(path)
        self.api.get=get
        await self.v.preflight()
        self.assertEqual(self.v.ledger.initial,D(100))
        self.assertEqual(self.v.equity,D(1000))

    async def test_ack_cannot_release_exposure(self):
        o=Order(Quote("BUY-1","BUY",D(100),D("0.01")),"Mixed",D("0.01"),0,order_id="server",status="ACK")
        self.v.orders[o.quote.slot]=o; self.v.history[o.client_id]=o
        self.api.connected=True
        with self.assertRaises(UncertainOrder): await self.v.confirmed(o)
        self.assertIn("BUY-1",self.v.orders)
    async def test_partial_remains_cancel_terminal_releases(self):
        o=Order(Quote("BUY-1","BUY",D(100),D("0.01")),"Mixed",D("0.01"),0)
        self.v.orders[o.quote.slot]=o; self.v.history[o.client_id]=o
        self.v.update_order(dict(orderId="server",clientId="Mixed",status="OPEN",remainingSize="0.005",sequenceNumber=2))
        self.assertEqual(o.remaining,D("0.005")); self.assertIn(o.quote.slot,self.v.orders)
        self.v.update_order(dict(orderId="server",clientId="Mixed",status="CANCELED",remainingSize="0.005",sequenceNumber=3))
        self.assertNotIn(o.quote.slot,self.v.orders)
    async def test_fill_rest_ws_dedup(self):
        f=dict(tradeId="1",orderId="server",marketId=1,side="BUY",size="0.01",price="100",fee="0",role="MAKER",createdAt=self.v.started_us+1)
        self.v.apply_fill(f); self.v.apply_fill(f); self.assertEqual(self.v.ledger.qty,D("0.01"))
    async def test_live_fill_quality_observer_runs_once_with_quote_context(self):
        observed=[];self.v.on_fill=observed.append
        q=Quote("BUY-1","BUY",D(100),D(".01"),False,D("1.2"))
        self.v.history["owned"]=Order(q,"owned",q.qty,0,order_id="server")
        f=dict(tradeId="quality",orderId="server",marketId=1,side="BUY",size=".01",price="100",fee="0",role="MAKER",createdAt=self.v.started_us+1)
        self.v.apply_fill(f);self.v.apply_fill(f)
        self.assertEqual(len(observed),1);self.assertEqual(observed[0]["spread_bps"],D("1.2"))
        self.assertEqual(observed[0]["timestamp_us"],f["createdAt"])
        self.v.apply_fill(dict(f,tradeId="taker",role="TAKER"))
        self.assertEqual(len(observed),1)

    async def test_local_self_cross_blocked_before_request(self):
        q=Quote("SELL-1","SELL",D(100),D("0.01"))
        self.v.orders[q.slot]=Order(q,"existing",q.qty,0)
        self.api.fresh=lambda: True
        self.v.post=AsyncMock()
        with self.assertRaises(ExchangeError):
            await self.v.place(Quote("BUY-1","BUY",D(100),D("0.01")),book())
        self.v.post.assert_not_awaited()

    async def test_post_only_rejection_requotes_without_failure_or_resend(self):
        self.api.fresh=lambda: True
        self.v.failures=1
        async def post(action,body):
            self.v.update_order(dict(orderId="server",clientId=body["clientId"],status="REJECTED",rejectionReason="POST_ONLY_WOULD_CROSS"))
            return {"orderId":"server"}
        self.v.post=AsyncMock(side_effect=post)
        result=await self.v.place(Quote("BUY-1","BUY",D(99990),D(".0001")),book())
        self.assertIsNone(result)
        self.assertEqual(self.v.failures,1)
        self.assertEqual(self.v.orders,{})
        self.assertIsNone(self.v.fatal)
        self.v.post.assert_awaited_once()
    async def test_unknown_rejection_counts_once(self):
        o=Order(Quote("x","BUY",D(100),D(".01")),"Mixed",D(".01"),0)
        self.v.history[o.client_id]=o
        row=dict(orderId="server",clientId="Mixed",status="REJECTED",rejectionReason="UNKNOWN")
        self.v.update_order(row); self.v.update_order(row)
        self.assertEqual(self.v.failures,1)
    async def test_self_trade_rejection_halts(self):
        o=Order(Quote("x","BUY",D(100),D("0.01")),"Mixed",D("0.01"),0)
        self.v.history[o.client_id]=o
        self.v.update_order(dict(orderId="1",clientId="Mixed",status="REJECTED",rejectionReason="SELF_TRADE"))
        self.assertIsNotNone(self.v.fatal)

class RiskEngine(unittest.IsolatedAsyncioTestCase):
    async def test_floating_loss_trips_before_quoting(self):
        c=Config(); api=PublicAPI(c,log); api.market=market(); api.book=book("89999","90001")
        api.connected=True; api.ready={"bbo","trades"}; api.clock_checked=time.monotonic()
        e=Engine(c,api,log); e.last_metadata=time.monotonic(); e.ledger.fill("BUY",D("0.001"),D(100000),D(0))
        e.venue=Paper(c,market(),e.ledger,log,D(0),D(0)); e.taker_fee=D(0)
        try:
            await e.cycle(); self.assertTrue(e.flatten); self.assertIsNotNone(e.halt); self.assertEqual(e.venue.orders,{})
        finally: await api.http.aclose()
    async def test_disconnect_watchdog_latches(self):
        c=replace(Config(),disconnect_seconds=.01); api=PublicAPI(c,log); e=Engine(c,api,log)
        try:
            await asyncio.wait_for(e.watchdog(),1); self.assertIsNotNone(e.halt)
        finally: await api.http.aclose()
    async def test_stop_loss_paper_ioc_reaches_flat(self):
        c=Config(); api=PublicAPI(c,log); api.market=market(); api.book=book("89999","90001")
        api.connected=True; api.ready={"bbo","trades"}; api.clock_checked=time.monotonic()
        e=Engine(c,api,log); e.ledger.fill("BUY",D("0.001"),D(100000),D(0))
        e.venue=Paper(c,market(),e.ledger,log,D(0),D("0.000225"))
        try:
            await e.cancel_all(); await e.close_position()
            self.assertEqual(e.ledger.qty,0); self.assertTrue(await e.close_position())
        finally: await api.http.aclose()

    async def test_failure_threshold_latches(self):
        c=Config(); api=PublicAPI(c,log); e=Engine(c,api,log,True)
        e.venue=SimpleNamespace(fatal=None,failures=c.max_order_failures)
        try:
            await asyncio.wait_for(e.watchdog(),1)
            self.assertEqual(e.halt,"consecutive order failures")
        finally: await api.http.aclose()

    async def test_clock_failure_rejects(self):
        api=PublicAPI(Config(),log); api.get=AsyncMock(return_value={"timeNs":time.time_ns()+40000000000})
        try:
            with self.assertRaises(ExchangeError): await api.clock()
        finally: await api.http.aclose()

class ClockRegression(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self): self.api=PublicAPI(Config(),log)
    async def asyncTearDown(self): await self.api.http.aclose()

    async def sample(self,rtts,offsets):
        walls=[]; monos=[]; replies=[]
        for i,(rtt,offset) in enumerate(zip(rtts,offsets)):
            base=(100+i*10)*1000000000
            walls.extend([base,base+rtt])
            monos.extend([base,base+rtt])
            replies.append({"timeNs":base+rtt//2+offset})
        self.api.get=AsyncMock(side_effect=replies)
        with patch("arcus_mm.api.time.time_ns",side_effect=walls),patch("arcus_mm.api.time.monotonic_ns",side_effect=monos):
            await self.api.clock()

    async def test_slow_response_does_not_trigger_false_skew(self):
        # Reproduces the previous 3.79s offset + 1.98s uncertainty false stop.
        await self.sample([3959624000,100000000,200000000],[3790353895,2000000000,2100000000])
        self.assertFalse(self.api.clock_fault); self.assertFalse(self.api.clock_pending)
        self.assertEqual(self.api.offset_ns,2000000000)

    async def test_real_skew_still_latches(self):
        with self.assertRaises(ExchangeError):
            await self.sample([100000000]*3,[6000000000]*3)
        self.assertTrue(self.api.clock_fault)

    async def test_no_reliable_sample_pauses_without_overwriting_offset(self):
        self.api.offset_ns=2000000000
        self.api.get=AsyncMock(side_effect=asyncio.TimeoutError)
        with self.assertRaises(ClockUnavailable): await self.api.clock()
        self.assertTrue(self.api.clock_pending); self.assertFalse(self.api.clock_fault)
        self.assertEqual(self.api.offset_ns,2000000000)
        self.assertFalse(self.api.fresh())
        await self.sample([100000000]*3,[2000000000]*3)
        self.assertFalse(self.api.clock_pending)

    async def test_clock_sampling_preserves_previous_valid_sync(self):
        self.api.clock_checked=time.monotonic()
        entered=asyncio.Event(); release=asyncio.Event()
        async def get(*args):
            entered.set(); await release.wait()
            return {"timeNs":time.time_ns()}
        self.api.get=get
        task=asyncio.create_task(self.api.clock())
        try:
            await entered.wait()
            self.assertFalse(self.api.clock_pending)
            release.set(); await task
            self.assertFalse(self.api.clock_pending)
        finally:
            task.cancel(); await asyncio.gather(task,return_exceptions=True)

    async def test_wall_jump_between_syncs_latches(self):
        self.api.clock_anchor=(100000000000,50000000000)
        with patch("arcus_mm.api.time.time_ns",return_value=105000000000), patch("arcus_mm.api.time.monotonic_ns",return_value=51000000000):
            self.assertFalse(self.api.check_wall_clock())
        self.assertTrue(self.api.clock_fault)

    async def test_healthy_refresh_keeps_quotes_and_does_not_block_cycle(self):
        api=self.api; api.market=market(); api.book=book()
        api.connected=True; api.ready={"bbo","trades"}
        api.clock_checked=time.monotonic()-21
        e=Engine(Config(),api,log); e.last_metadata=time.monotonic(); e.taker_fee=D(0)
        e.venue=Paper(Config(),market(),e.ledger,log,D(0),D(0))
        for q in targets(e.c,api.market,api.book,D(100),D(0)):
            await e.venue.place(q,api.book)
        ids=[o.client_id for o in e.venue.orders.values()]
        started=asyncio.Event(); finish=asyncio.Event()
        async def sync():
            started.set()
            await finish.wait()
            api.clock_checked=time.monotonic()
        api.clock=sync
        await asyncio.wait_for(e.cycle(),1)
        await started.wait()
        self.assertEqual(ids,[o.client_id for o in e.venue.orders.values()])
        self.assertFalse(e.clock_task.done())
        finish.set(); await e.clock_task
        self.assertIsNone(e.halt)

    async def test_small_price_move_keeps_order_ids(self):
        api=self.api; api.market=market(); api.book=book()
        api.connected=True; api.ready={"bbo","trades"}; api.clock_checked=time.monotonic()
        e=Engine(replace(Config(),order_equity_fraction="0.2"),api,log); e.last_metadata=time.monotonic(); e.taker_fee=D(0)
        e.venue=Paper(e.c,market(),e.ledger,log,D(0),D(0))
        await e.cycle()
        ids=[o.client_id for o in e.venue.orders.values()]
        api.book=book("99999.2","100001.2")
        await e.cycle()
        self.assertEqual(ids,[o.client_id for o in e.venue.orders.values()])
        api.book=book("100009","100011")
        await e.cycle()
        self.assertEqual(len(e.venue.orders),1)

    async def test_batch_cancels_confirm_both_then_replan_and_place_same_cycle(self):
        api=self.api;api.market=market();api.book=book()
        api.connected=True;api.ready={"bbo","trades"};api.clock_checked=time.monotonic()
        c=replace(Config(),order_equity_fraction=".2",batch_quote_cancels=True)
        e=Engine(c,api,log);e.last_metadata=time.monotonic();e.taker_fee=D(0)
        e.venue=Paper(c,market(),e.ledger,log,D(0),D(0))
        await e.cycle()
        self.assertEqual(len(e.venue.orders),2)
        api.book=book("100009","100011")
        await e.cycle()
        expected={q.slot:q.price for q in targets(c,market(),api.book,D(100),D(0))}
        self.assertEqual({slot:o.quote.price for slot,o in e.venue.orders.items()},expected)

    async def test_background_skew_stops_and_cancels(self):
        api=self.api; api.market=market(); api.book=book()
        e=Engine(Config(),api,log)
        e.venue=Paper(e.c,market(),e.ledger,log,D(0),D(0))
        await e.venue.place(Quote("BUY-1","BUY",D(99990),D("0.0001")),api.book)
        async def skew():
            api.clock_fault=True
            raise ExchangeError("skew")
        api.clock=skew
        await e.refresh_clock()
        self.assertTrue(api.clock_pending)
        self.assertEqual(e.halt,"clock skew protection triggered")
        self.assertEqual(e.venue.orders,{})

    async def test_pending_refresh_cancels_before_retry(self):
        api=self.api; api.market=market(); api.book=book(); api.clock_pending=True
        e=Engine(Config(),api,log)
        e.venue=Paper(Config(),market(),e.ledger,log,D(0),D(0))
        await e.venue.place(Quote("BUY-1","BUY",D(99990),D("0.0001")),api.book)
        async def sync():
            self.assertEqual(e.venue.orders,{})
            raise ClockUnavailable("unavailable")
        api.clock=sync
        await e.cycle(); await e.clock_task
        self.assertIsNone(e.halt)

    async def test_wall_clock_jump_stops(self):
        self.api.get=AsyncMock(return_value={"timeNs":102000000000})
        with patch("arcus_mm.api.time.time_ns",side_effect=[100000000000,103000000000]),patch("arcus_mm.api.time.monotonic_ns",side_effect=[100000000000,100100000000]):
            with self.assertRaises(ExchangeError): await self.api.clock()
        self.assertTrue(self.api.clock_fault)

    async def test_clock_pause_cancels_quotes_before_retry(self):
        c=Config(); api=self.api; api.market=market(); api.book=book()
        api.connected=True; api.ready={"bbo","trades"}; api.clock_checked=time.monotonic()-21
        e=Engine(c,api,log); e.last_metadata=time.monotonic(); e.taker_fee=D(0); e.venue=Paper(c,market(),e.ledger,log,D(0),D(0))
        await e.venue.place(targets(c,market(),api.book,D(100),D(0))[0],api.book)
        async def unavailable():
            api.clock_pending=True
            raise ClockUnavailable("slow requests")
        api.clock=unavailable
        await e.cycle()
        await e.clock_task
        self.assertEqual(e.venue.orders,{})
        self.assertIsNone(e.halt)
        self.assertEqual(e.failure_count,0)

if __name__=="__main__": unittest.main()
