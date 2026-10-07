import unittest
import time
from dataclasses import replace
from decimal import Decimal as D
from unittest.mock import AsyncMock
from arcus_mm.config import Config
from arcus_mm.api import PublicAPI,Live,UncertainOrder,RateLimited
from arcus_mm.models import Ledger,Order,Quote
from tests.test_mm import credentials,market,log

class ProtectionConfig(unittest.TestCase):
    def test_default_headroom_exceeds_cleanup_backoff_and_startup(self):
        c=replace(Config(),disconnect_seconds=5);c.validate()
        self.assertEqual(c.protection_seconds,180)
        self.assertEqual(c.protection_refresh_seconds,30)
        self.assertGreater(c.protection_seconds-c.protection_refresh_seconds,60+60+20)
    def test_invalid_deadline_and_refresh_rejected(self):
        for changes in ({"protection_seconds":10},{"protection_seconds":301},{"protection_refresh_seconds":61},{"protection_refresh_seconds":0},{"protection_seconds":True}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):replace(Config(),**changes).validate()

class DisarmProtection(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.api=PublicAPI(Config(),log);self.api.market=market()
        with credentials():self.live=Live(self.api,Ledger(100),log)
        self.live.arm_time=time.monotonic()
        self.api.get=AsyncMock(return_value={"orders":[]})
        self.live.post=AsyncMock(return_value={"status":"disarmed"})
    async def asyncTearDown(self):await self.api.http.aclose()
    def order(self,status="PENDING"):
        o=Order(Quote("BUY-1","BUY",D(100),D(".001")),"known",D(".001"),0,status=status)
        self.live.history[o.client_id]=o
        return o
    async def test_proven_clear_disarms_without_new_deadline(self):
        self.order("CANCELED")
        self.assertTrue(await self.live.disarm_if_clear())
        action,body=self.live.post.await_args.args
        self.assertEqual(action,"scheduleCancel")
        self.assertNotIn("time",body)
        self.assertEqual(body["marketId"],1)
        self.assertEqual(self.live.arm_time,0)
    async def test_unknown_pending_order_keeps_fallback_even_if_not_in_slots(self):
        self.order()
        self.assertFalse(await self.live.disarm_if_clear())
        self.live.post.assert_not_awaited()
        self.assertGreater(self.live.arm_time,0)
    async def test_rest_open_order_keeps_fallback(self):
        self.api.get=AsyncMock(return_value={"orders":[{"orderId":"unknown"}]})
        self.assertFalse(await self.live.disarm_if_clear())
        self.live.post.assert_not_awaited()
    async def test_uncertain_fault_keeps_fallback(self):
        self.live.fatal="uncertain placement"
        self.assertFalse(await self.live.disarm_if_clear())
        self.live.post.assert_not_awaited()
    async def test_rate_limit_and_unknown_disarm_result_keep_fallback(self):
        for error in (RateLimited("429"),UncertainOrder("unknown")):
            self.live.post=AsyncMock(side_effect=error)
            self.assertFalse(await self.live.disarm_if_clear())
            self.assertGreater(self.live.arm_time,0)
    async def test_unexpected_response_does_not_claim_success(self):
        self.live.post=AsyncMock(return_value={"status":"armed"})
        self.assertFalse(await self.live.disarm_if_clear())
        self.assertGreater(self.live.arm_time,0)
    async def test_new_order_during_rest_check_prevents_disarm(self):
        async def get(*args):
            self.order();return {"orders":[]}
        self.api.get=AsyncMock(side_effect=get)
        self.assertFalse(await self.live.disarm_if_clear())
        self.live.post.assert_not_awaited()
