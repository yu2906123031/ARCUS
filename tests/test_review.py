"""Regressions found by the whole-repository execution review."""
import time
import httpx
import unittest
from dataclasses import replace
from decimal import Decimal as D
from types import SimpleNamespace
from unittest.mock import AsyncMock
from arcus_mm.api import PublicAPI,Live,RateLimited,UncertainOrder
from arcus_mm.config import Config
from arcus_mm.engine import Engine
from arcus_mm.models import Ledger,Quote,Order
from tests.test_mm import credentials,market,book,log

class ExecutionReview(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.c=replace(Config(),server_protection=False,confirmation_seconds=.03)
        self.api=PublicAPI(self.c,log);self.api.market=market()
        await self.api.http.aclose()
        def offline(request):raise AssertionError("unexpected HTTP in offline review")
        self.api.http=httpx.AsyncClient(base_url=self.c.api_url,transport=httpx.MockTransport(offline))
        with credentials():self.v=Live(self.api,Ledger(100),log)
    async def asyncTearDown(self):await self.api.http.aclose()

    def order(self):
        q=Quote("BUY-1","BUY",D(100000),D(".0002"))
        o=Order(q,"owned",q.qty,0,order_id="server",status="OPEN")
        self.v.history[o.client_id]=o;self.v.orders[q.slot]=o
        return o

    async def test_terminal_order_is_not_reopened_by_unsequenced_rest(self):
        o=self.order()
        self.v.update_order(dict(clientId="owned",orderId="server",status="FILLED",remainingSize="0",sequenceNumber=10))
        self.v.update_order(dict(clientId="owned",orderId="server",status="OPEN",remainingSize=".0002"))
        self.assertEqual(o.status,"FILLED");self.assertEqual(o.remaining,0)
        self.assertEqual(self.v.orders,{})

    async def test_conflicting_order_id_cannot_replace_owned_identity(self):
        o=self.order()
        self.v.update_order(dict(clientId="owned",orderId="different",status="OPEN",remainingSize=".0002",sequenceNumber=11))
        self.assertEqual(o.order_id,"server");self.assertIsNotNone(self.v.fatal)

    async def test_reconciliation_equity_change_rechecks_existing_exposure(self):
        e=Engine(self.c,self.api,log,live=True);e.venue=self.v;self.v.ledger=e.ledger
        self.api.book=book();self.api.fresh=lambda:True;self.api.clock_checked=time.monotonic()
        e.last_metadata=time.monotonic();self.v.equity=D(100)
        self.v.account_qty=D(0);self.v.last_account_seq=1;self.v.last_fill_seq=1
        self.order()
        async def reconcile():
            e.ledger.realized=D(-80);self.v.account_time=time.monotonic();return True
        self.v.reconcile=AsyncMock(side_effect=reconcile)
        e.cancel_all=AsyncMock();self.v.place=AsyncMock();self.v.cancel=AsyncMock()
        await e.cycle()
        e.cancel_all.assert_awaited_once();self.v.place.assert_not_awaited()

    async def test_clock_rate_limit_stops_sampling_immediately(self):
        self.api.get=AsyncMock(side_effect=RateLimited("read-only request HTTP 429"))
        with self.assertRaises(RateLimited):await self.api.clock()
        self.api.get.assert_awaited_once();self.assertTrue(self.api.clock_pending)

    async def test_confirmation_does_not_poll_again_during_read_cooldown(self):
        o=self.order();o.status="ACK"
        async def limited(*args):
            self.api.cooldown=time.monotonic()+60
            raise RateLimited("read-only request HTTP 429")
        self.api.get=AsyncMock(side_effect=limited)
        self.v.c=replace(self.c,confirmation_seconds=.6)
        with self.assertRaises(UncertainOrder):await self.v.confirmed(o)
        self.api.get.assert_awaited_once()
