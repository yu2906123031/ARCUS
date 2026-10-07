import asyncio
import json
import tempfile
import unittest
from dataclasses import replace
from decimal import Decimal as D
from unittest.mock import patch
from urllib.request import Request, urlopen
from arcus_mm.compare import make_engines
from arcus_mm.config import Config
from arcus_mm.dashboard import Dashboard
from arcus_mm.models import Market, Book
from arcus_mm.strategy import targets
import time

class Comparison(unittest.IsolatedAsyncioTestCase):
    async def test_two_accounts_are_paper_independent_and_stop_together(self):
        with tempfile.TemporaryDirectory() as root:
            with patch("arcus_mm.api.Signer",side_effect=AssertionError("must not read credentials")):
                engines=make_engines(replace(Config(),paper=False,paper_equity="999",log_dir=root))
            dashboard=Dashboard(engines,asyncio.get_running_loop(),0)
            try:
                for e in engines:
                    self.assertFalse(e.live)
                    self.assertTrue(e.c.paper)
                    self.assertEqual(e.ledger.initial,D(100))
                self.assertNotEqual(engines[0].log.path,engines[1].log.path)
                engines[0].ledger.fill("BUY","0.0001","85000","0")
                self.assertEqual(engines[1].ledger.qty,0)
                self.assertEqual(engines[1].ledger.fees,0)
                dashboard.start()
                base=f"http://127.0.0.1:{dashboard.server.server_port}"
                def read(path):
                    with urlopen(base+path,timeout=3) as r: return r.read()
                status=json.loads(await asyncio.to_thread(read,"/api/status"))
                self.assertEqual([s["market"] for s in status["accounts"]],["BTC-USD","ETH-USD"])
                self.assertEqual([s["initial_equity"] for s in status["accounts"]],["100","100"])
                html=(await asyncio.to_thread(read,"/")).decode("utf-8")
                self.assertIn("BTC / ETH",html)
                self.assertIn("\u53cc\u5e02\u573a\u6a21\u62df\u5bf9\u6bd4",html)
                self.assertNotIn("???",html)
                def stop():
                    req=Request(base+"/api/stop",method="POST",headers={"X-Control-Token":dashboard.token})
                    with urlopen(req,timeout=3) as r: return r.status
                self.assertEqual(await asyncio.to_thread(stop),202)
                self.assertTrue(all(e.halt=="operator stop" for e in engines))
            finally:
                if dashboard.thread.is_alive(): await asyncio.to_thread(dashboard.close)
                for e in engines: await e.api.http.aclose()

    async def test_eth_market_precision_and_minimums(self):
        c=replace(Config(),market="ETH-USD");c.validate()
        m=Market(2,D("0.01"),D("0.0000001"),D("0.001"),D(5),D(100000),[])
        b=Book(D("2702.30"),D("2702.31"),D(10),D(30),time.time_ns()//1000,time.monotonic())
        qs=targets(c,m,b,D(100),D(0))
        self.assertEqual(len(qs),2)
        for q in qs:
            self.assertEqual(q.price%m.tick,0)
            self.assertEqual(q.qty%m.step,0)
            self.assertGreaterEqual(q.qty,m.min_size)
            self.assertGreaterEqual(q.qty*q.price,m.min_notional)
