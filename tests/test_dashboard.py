import asyncio
import json
import unittest
import time
import tempfile
from decimal import Decimal as D
from dataclasses import replace
from types import SimpleNamespace
from arcus_mm.models import Log, Order, Quote
from tests.test_mm import book
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from arcus_mm.api import PublicAPI
from arcus_mm.config import Config
from arcus_mm.engine import Engine
from arcus_mm.dashboard import Dashboard

class DashboardTests(unittest.IsolatedAsyncioTestCase):
    async def test_status_and_authenticated_stop(self):
        api=PublicAPI(Config(),lambda *a,**k: None)
        e=Engine(api.c,api,api.log)
        d=Dashboard(e,asyncio.get_running_loop(),0)
        d.start()
        base=f"http://127.0.0.1:{d.server.server_port}"
        def request(path,token=None):
            req=Request(base+path,method="POST" if path.endswith("stop") else "GET")
            if token: req.add_header("X-Control-Token",token)
            with urlopen(req,timeout=3) as r: return r.status,r.read()
        try:
            code,body=await asyncio.to_thread(request,"/api/status")
            self.assertEqual(code,200)
            self.assertEqual(json.loads(body)["mode"],"paper")
            with self.assertRaises(HTTPError) as ctx:
                await asyncio.to_thread(request,"/api/stop")
            self.assertEqual(ctx.exception.code,403)
            self.assertIsNone(e.halt)
            code,_=await asyncio.to_thread(request,"/api/stop",d.token)
            self.assertEqual(code,202)
            self.assertEqual(e.halt,"operator stop")
            self.assertFalse(e.flatten)
            _,body=await asyncio.to_thread(request,"/")
            self.assertIn(d.token.encode(),body)
            html=body.decode("utf-8")
            self.assertIn("交易服务器监控",html)
            self.assertNotIn("???",html)
        finally:
            await asyncio.to_thread(d.close)
            await api.http.aclose()


    async def test_live_risk_snapshot_matches_engine_and_preserves_dust(self):
        api=PublicAPI(replace(Config(),leverage_cap=5,capital_cap="100",max_position_equity_fraction=".25",stop_loss_equity_fraction=".01"),lambda *a,**k:None)
        e=Engine(api.c,api,api.log,live=True,auto_resume=True)
        api.book=book();api.channels={"bbo","orders"};api.ready={"bbo"}
        e.ledger.qty=D("-.00000043");e.ledger.entry=D(100000);e.maker_fee=D(0);e.taker_fee=D(".000225")
        q=Quote("BUY-1","BUY",D(99990),D(".0001"))
        e.venue=SimpleNamespace(equity=D(80),free_collateral=D(50),orders={q.slot:Order(q,"test",q.qty,time.monotonic())},
            account_qty=e.ledger.qty,last_account_seq=10,last_fill_seq=9,account_time=time.monotonic(),arm_time=time.monotonic(),
            failures=1,recovering=False,resume_info=dict(since_us=1000000,initial_equity="100"))
        d=object.__new__(Dashboard);d.flatten=True
        try:
            s=d.account_snapshot(e)
            self.assertEqual(s["state"],"paused")
            self.assertFalse(s["health"]["trading_enabled"])
            self.assertIn("等待服务端保护",s["pause_reason"])
            self.assertEqual(s["risk_equity"],D(80))
            self.assertEqual(s["account_equity"],D(80))
            self.assertEqual(s["risk"]["position_limit_usd"],D(20))
            self.assertEqual(s["risk"]["stop_budget_usd"],D(1))
            self.assertEqual(s["risk"]["worst_buy_usd"],D("9.957"))
            self.assertEqual(s["risk"]["worst_sell_usd"],D(".043"))
            self.assertEqual(s["position"],D("-.00000043"))
            self.assertEqual(s["orders"][0]["notional"],D("9.999"))
            self.assertTrue(s["health"]["account_synced"])
            self.assertEqual(s["health"]["missing_channels"],["orders"])
            self.assertTrue(s["recovery"]["resumed"])
            e.venue.last_fill_seq=11
            self.assertFalse(d.account_snapshot(e)["health"]["account_synced"])
        finally:await api.http.aclose()

    async def test_recent_history_is_bounded_and_snapshot_fields_are_filtered(self):
        with tempfile.TemporaryDirectory() as directory:
            log=Log(directory,"paper")
            log.recent.extend([dict(event="fill",time_ns=n,side="BUY",quantity=".1",price="100",fee="0",private_key="never expose") for n in range(250)])
            self.assertEqual(len(log.recent),200)
            api=PublicAPI(Config(),log);e=Engine(api.c,api,log)
            d=object.__new__(Dashboard);d.flatten=False
            try:
                s=d.account_snapshot(e)
                self.assertEqual(len(s["fills"]),30)
                self.assertEqual(s["fills"][0]["time_ns"],249)
                self.assertNotIn("private_key",json.dumps(s,default=str))
                self.assertIsNone(s["position_notional"])
            finally:await api.http.aclose()
