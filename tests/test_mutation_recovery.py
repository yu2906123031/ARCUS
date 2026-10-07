import unittest
import httpx
from dataclasses import replace
from decimal import Decimal as D
from unittest.mock import AsyncMock
from arcus_mm.api import PublicAPI,Live,ExchangeError,UncertainOrder
from arcus_mm.config import Config
from arcus_mm.models import Ledger,Quote,Order
from tests.test_mm import credentials,market,log

class MutationRecovery(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.api=PublicAPI(replace(Config(),confirmation_seconds=.2),log)
        self.api.market=market(); self.api.connected=True
        with credentials():self.live=Live(self.api,Ledger(100),log)
        self.order=Order(Quote("BUY-1","BUY",D(100),D(".01")),"original",D(".01"),0,order_id="server",status="OPEN")
        self.live.orders[self.order.quote.slot]=self.order
        self.live.history[self.order.client_id]=self.order
        self.live.reconcile=AsyncMock(return_value=True)
        self.api.http.post=AsyncMock(side_effect=httpx.ReadTimeout("test"))
    async def asyncTearDown(self):await self.api.http.aclose()
    def cancel_body(self):
        return dict(self.live.scope(),marketId=1,kind="clientId",clientId="original")
    async def test_cancel_timeout_resolved_by_rest_even_with_connected_ws(self):
        self.api.get=AsyncMock(return_value={"orderId":"server","clientId":"original","status":"CANCELED","remainingSize":".01"})
        result=await self.live.post("cancelOrder",self.cancel_body())
        self.assertEqual(result["status"],"CANCELED")
        self.assertIsNone(self.live.fatal)
        self.assertEqual(self.live.orders,{})
        self.assertEqual(self.api.http.post.await_count,1)
        self.live.reconcile.assert_awaited_once()
    async def test_cancel_timeout_already_confirmed_by_ws(self):
        self.order.status="CANCELED";self.live.orders.clear()
        self.api.get=AsyncMock()
        result=await self.live.post("cancelOrder",self.cancel_body())
        self.assertEqual(result["status"],"CANCELED")
        self.api.get.assert_not_awaited()
        self.assertEqual(self.api.http.post.await_count,1)
    async def test_unconfirmed_cancel_still_stops_and_keeps_exposure(self):
        self.api.get=AsyncMock(side_effect=ExchangeError("unavailable"))
        with self.assertRaises(UncertainOrder):await self.live.post("cancelOrder",self.cancel_body())
        self.assertIsNotNone(self.live.fatal)
        self.assertIn("BUY-1",self.live.orders)
        self.assertEqual(self.api.http.post.await_count,1)
    async def test_known_place_result_accepted_without_resending(self):
        body=dict(self.live.scope(),marketId=1,clientId="original",price="100",quantity=".01",goodTilTime="2000000000000000",orderSide="BUY",timeInForce="ALO",reduceOnly=False)
        result=await self.live.post("placeOrder",body)
        self.assertEqual(result["orderId"],"server")
        self.assertEqual(self.api.http.post.await_count,1)
        self.assertIn("BUY-1",self.live.orders)
    async def test_unknown_place_is_never_treated_as_not_submitted(self):
        self.order.order_id="";self.order.status="PENDING"
        body=dict(self.live.scope(),marketId=1,clientId="original",price="100",quantity=".01",goodTilTime="2000000000000000",orderSide="BUY",timeInForce="ALO",reduceOnly=False)
        with self.assertRaises(UncertainOrder):await self.live.post("placeOrder",body)
        self.assertEqual(self.api.http.post.await_count,1)
        self.assertIn("BUY-1",self.live.orders)
    async def test_account_mismatch_blocks_recovery(self):
        self.order.status="CANCELED";self.live.orders.clear()
        self.live.reconcile=AsyncMock(return_value=False)
        with self.assertRaises(UncertainOrder):await self.live.post("cancelOrder",self.cancel_body())
        self.assertEqual(self.api.http.post.await_count,1)
    async def test_server_500_uses_same_confirmation_path(self):
        self.order.status="CANCELED";self.live.orders.clear()
        self.api.http.post=AsyncMock(return_value=httpx.Response(503))
        self.assertEqual((await self.live.post("cancelOrder",self.cancel_body()))["status"],"CANCELED")
        self.assertEqual(self.api.http.post.await_count,1)
    async def test_protection_mutation_without_proof_still_stops(self):
        with self.assertRaises(UncertainOrder):
            await self.live.post("scheduleCancel",dict(self.live.scope(),marketId=1,time=2000000000000000))
        self.assertEqual(self.api.http.post.await_count,1)

    async def test_connect_failure_retries_only_before_request_sent(self):
        self.api.http.post=AsyncMock(side_effect=[httpx.ConnectTimeout("test"),httpx.Response(200,json={"orderId":"server"})])
        result=await self.live.post("cancelOrder",self.cancel_body())
        self.assertEqual(result["orderId"],"server")
        self.assertEqual(self.api.http.post.await_count,2)
        self.assertIsNone(self.live.fatal)
    async def test_repeated_connect_failure_is_bounded_not_uncertain(self):
        self.api.http.post=AsyncMock(side_effect=httpx.ConnectTimeout("test"))
        with self.assertRaises(ExchangeError) as ctx:await self.live.post("cancelOrder",self.cancel_body())
        self.assertNotIsInstance(ctx.exception,UncertainOrder)
        self.assertEqual(self.api.http.post.await_count,2)
        self.assertIsNone(self.live.fatal)

    async def test_http_rejection_identifies_action_without_resending(self):
        self.api.http.post=AsyncMock(return_value=httpx.Response(400))
        with self.assertRaisesRegex(ExchangeError,"mutation HTTP 400: scheduleCancel"):
            await self.live.post("scheduleCancel",dict(self.live.scope(),marketId=1,time=2000000000000000))
        self.assertEqual(self.api.http.post.await_count,1)
