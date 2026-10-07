import asyncio
import unittest
import time
from decimal import Decimal as D
from unittest.mock import AsyncMock,patch
import httpx
from arcus_mm.api import PublicAPI,Live,RateLimited,RequestForbidden,UncertainOrder
from arcus_mm.config import Config
from arcus_mm.engine import Engine
from arcus_mm.models import Ledger
from arcus_mm.restart import retryable
from tests.test_mm import credentials,market,log

class RequestControl(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.api=PublicAPI(Config(),log);self.api.market=market()
        with credentials():self.live=Live(self.api,Ledger(100),log)
    async def asyncTearDown(self):await self.api.http.aclose()
    async def test_consecutive_limits_back_off_and_do_not_resend(self):
        self.api.http.post=AsyncMock(return_value=httpx.Response(429,json={}))
        with patch("arcus_mm.api.time.monotonic",return_value=100):
            for expected in (5,10,20,30+10,60):
                self.api.cooldown=0;self.live.last_mutation=0
                with self.assertRaises(RateLimited):await self.live.post("cancelAllOrders",self.live.scope())
                self.assertEqual(self.api.cooldown,100+expected)
        self.assertEqual(self.api.http.post.await_count,5)
    async def test_forbidden_is_latched_per_action_without_restart(self):
        self.api.http.post=AsyncMock(return_value=httpx.Response(403,json={"error":"accountIndex permission mismatch"}))
        for _ in range(2):
            with self.assertRaises(RequestForbidden):await self.live.post("cancelAllOrders",self.live.scope())
        self.api.http.post.assert_awaited_once()
        self.assertIsNotNone(self.live.fatal)
        self.assertFalse(retryable(RequestForbidden("403"),"disconnected, stale data, clock or incomplete subscriptions"))
    async def test_explicit_temporary_ban_pauses_instead_of_permission_stop(self):
        self.api.http.post=AsyncMock(return_value=httpx.Response(403,json={"error":"temporarily banned"}))
        before=time.monotonic()
        with self.assertRaises(RateLimited):await self.live.post("cancelAllOrders",self.live.scope())
        self.assertGreaterEqual(self.api.cooldown,before+30)
        self.assertIsNone(self.live.fatal)
        self.assertEqual(self.live.forbidden_actions,{})
    async def test_concurrent_cleanup_is_deduplicated_until_new_order_revision(self):
        e=Engine(self.api.c,self.api,log,live=True);e.venue=self.live
        self.live.cancel_all=AsyncMock()
        await asyncio.gather(e.cancel_all(),e.cancel_all(),e.cancel_all())
        self.live.cancel_all.assert_awaited_once()
        self.live.order_revision+=1
        await e.cancel_all()
        self.assertEqual(self.live.cancel_all.await_count,2)
    async def test_uncertain_cleanup_is_not_repeated_by_finally(self):
        e=Engine(self.api.c,self.api,log,live=True);e.venue=self.live
        self.live.cancel_all=AsyncMock(side_effect=UncertainOrder("unknown"))
        for _ in range(2):
            with self.assertRaises(UncertainOrder):await e.cancel_all()
        self.live.cancel_all.assert_awaited_once()
    async def test_drip_pool_and_pacing_are_applied_before_signing(self):
        self.api.http.post=AsyncMock(return_value=httpx.Response(200,json={"rateLimit":{"pool":"cancel","remaining":0}}))
        await self.live.post("cancelAllOrders",self.live.scope())
        self.assertGreater(self.live.pool_ready["cancel"],time.monotonic())
        with patch("arcus_mm.api.asyncio.sleep",new=AsyncMock()) as wait:
            await self.live.post("cancelAllOrders",self.live.scope())
        self.assertGreater(wait.call_args.args[0],1)
    async def test_new_quote_is_not_sent_when_market_becomes_stale_during_pacing(self):
        self.api.fresh=lambda:True
        self.live.last_mutation=time.monotonic()
        self.api.http.post=AsyncMock()
        async def stale(_): self.api.fresh=lambda:False
        with patch("arcus_mm.api.asyncio.sleep",side_effect=stale):
            from arcus_mm.api import ExchangeError
            with self.assertRaisesRegex(ExchangeError,"unsafe to place"):
                await self.live.post("placeOrder",dict(timeInForce="ALO"))
        self.api.http.post.assert_not_awaited()

    async def test_read_429_enters_cooldown_without_retries(self):
        request=httpx.Request("GET","https://api.arcus.xyz/v1/account")
        self.api.http.get=AsyncMock(return_value=httpx.Response(429,request=request,json={"retryAfterMs":9000}))
        with self.assertRaises(RateLimited):await self.api.get("/v1/account")
        self.api.http.get.assert_awaited_once()
        self.assertGreater(self.api.cooldown,time.monotonic()+8)
