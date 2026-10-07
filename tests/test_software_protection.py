import asyncio
import time
import unittest
from dataclasses import replace
from decimal import Decimal as D
from unittest.mock import AsyncMock,patch
from arcus_mm.api import PublicAPI,Live,ExchangeError,UncertainOrder
from arcus_mm.config import Config
from arcus_mm.engine import Engine
from arcus_mm.dashboard import Dashboard
from arcus_mm.models import Ledger
from tests.test_mm import credentials,market,book,log

class SoftwareProtection(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.c=replace(Config(),server_protection=False,disconnect_seconds=5,quote_interval_seconds=.001)
        self.api=PublicAPI(self.c,log);self.api.market=market();self.api.book=book()
        self.api.fresh=lambda:True;self.api.clock_checked=time.monotonic()
        with credentials():self.live=Live(self.api,Ledger(100),log)
        self.live.equity=D(100)
        self.live.post=AsyncMock(return_value={})
        self.api.get=AsyncMock(return_value={"leverages":[{"marketId":1,"leverage":3,"marginMode":"CROSS"}]})
    async def asyncTearDown(self):await self.api.http.aclose()
    async def test_enable_bypasses_daily_quota_without_any_schedule_request(self):
        self.live.protection_cooldown=time.monotonic()+86400
        self.live.protection_block_reason="daily_trigger_limit"
        await self.live.enable()
        self.assertTrue(self.live.leverage_confirmed)
        self.assertEqual([c.args[0] for c in self.live.post.await_args_list],["setLeverage"])
        self.assertEqual(self.live.arm_time,0)
        self.assertEqual(self.live.protection_cooldown,0)
        await self.live.arm()
        self.assertEqual(self.live.post.await_count,1)
    async def test_watchdog_requests_cancel_without_waiting_for_quote_loop(self):
        e=Engine(self.c,self.api,log,live=True);e.venue=self.live
        self.api.clock_fault=True
        self.live.cancel_all=AsyncMock()
        await e.watchdog()
        self.assertEqual(e.halt,"clock skew protection triggered")
        self.live.cancel_all.assert_awaited_once()
        self.assertTrue(e.safety_cancelled)
    async def test_already_stale_book_does_not_wait_an_extra_disconnect_window(self):
        e=Engine(self.c,self.api,log,live=True);e.venue=self.live
        self.api.fresh=lambda:False;self.api.book.received=time.monotonic()-6
        self.live.cancel_all=AsyncMock()
        await asyncio.wait_for(e.watchdog(),timeout=.5)
        self.live.cancel_all.assert_awaited_once()
        self.assertIsNotNone(e.halt)
    async def test_uncertain_cancel_is_not_resent(self):
        e=Engine(self.c,self.api,log,live=True);e.venue=self.live
        self.api.clock_fault=True
        self.live.cancel_all=AsyncMock(side_effect=UncertainOrder("unknown"))
        await e.watchdog()
        self.live.cancel_all.assert_awaited_once()
        self.assertFalse(e.safety_cancelled)
        self.assertIsNotNone(e.halt)
    async def test_only_definite_before_send_failure_retries(self):
        e=Engine(self.c,self.api,log,live=True);e.venue=self.live
        self.live.cancel_all=AsyncMock(side_effect=[ExchangeError("mutation connection failed before sending: cancelAllOrders"),None])
        with patch("arcus_mm.engine.asyncio.sleep",new=AsyncMock()):await e.cancel_all()
        self.assertEqual(self.live.cancel_all.await_count,2)
    async def test_software_start_and_stop_never_arm_or_disarm_server(self):
        e=Engine(self.c,self.api,log,live=True);e.venue=self.live;e.taker_fee=D(0)
        self.live.protection_cooldown=time.monotonic()+86400
        e.setup=AsyncMock();self.live.reconcile=AsyncMock(return_value=True)
        self.live.cancel_all=AsyncMock();self.live.disarm_if_clear=AsyncMock()
        async def idle():await asyncio.Event().wait()
        self.api.stream=idle;e.watchdog=idle
        async def cycle():
            self.assertTrue(e.trading_enabled)
            e.trip("operator stop")
        e.cycle=AsyncMock(side_effect=cycle)
        self.assertEqual(await e.run(),"operator stop")
        e.cycle.assert_awaited_once()
        self.live.cancel_all.assert_awaited_once()
        self.live.disarm_if_clear.assert_not_awaited()
        self.assertEqual([c.args[0] for c in self.live.post.await_args_list],["setLeverage"])
    async def test_dashboard_can_show_running_without_server_protection(self):
        e=Engine(self.c,self.api,log,live=True);e.venue=self.live;e.trading_enabled=True;e.taker_fee=D(0)
        d=object.__new__(Dashboard);d.flatten=True
        s=d.account_snapshot(e)
        self.assertEqual(s["state"],"running")
        self.assertIsNone(s["pause_reason"])
        self.assertEqual(s["health"]["protection_mode"],"software_only")
        self.assertTrue(s["health"]["software_protection_active"])
        self.assertFalse(s["health"]["protection_active"])
        self.assertIsNone(s["health"]["protection_lead_seconds"])

class ServerModeDefaults(unittest.TestCase):
    def test_software_mode_requires_boolean_and_server_default_preserved(self):
        self.assertTrue(Config().server_protection)
        replace(Config(),server_protection=False).validate()
        with self.assertRaises(ValueError):replace(Config(),server_protection="false").validate()
