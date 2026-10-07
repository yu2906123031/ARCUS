import unittest
from unittest.mock import Mock,AsyncMock
import time
import asyncio
from dataclasses import replace
from decimal import Decimal as D
from arcus_mm.restart import supervise,retryable,RETRY_EXIT
from arcus_mm.api import daily_protection_quota,RateLimited,ExchangeError,UncertainOrder,PublicAPI,Live
from arcus_mm.config import Config
from arcus_mm.models import Ledger,Quote
from arcus_mm.engine import Engine
from tests.test_mm import credentials,market,book,log
import httpx

class RestartPolicy(unittest.TestCase):
    def test_backoff_and_normal_stop(self):
        run=Mock(side_effect=[75,75,75,75,0]);wait=Mock()
        self.assertEqual(supervise(run,sleep=wait,clock=lambda:0,report=Mock()),0)
        self.assertEqual([x.args[0] for x in wait.call_args_list],[15,30,60,60])
    def test_server_cooldown_is_preserved_across_restart(self):
        wait=Mock()
        self.assertEqual(supervise(Mock(side_effect=[75,0]),sleep=wait,report=Mock(),cooldown=lambda:120),0)
        wait.assert_called_once_with(120)
    def test_permanent_failure_does_not_restart(self):
        run=Mock(return_value=1);wait=Mock()
        self.assertEqual(supervise(run,sleep=wait,report=Mock()),1)
        run.assert_called_once();wait.assert_not_called()
    def test_operator_stops_during_backoff(self):
        run=Mock(return_value=75)
        self.assertEqual(supervise(run,sleep=Mock(side_effect=KeyboardInterrupt),report=Mock()),0)
        run.assert_called_once()
    def test_transient_and_risk_classification(self):
        self.assertTrue(retryable(RateLimited("429")))
        self.assertTrue(retryable(ExchangeError("read-only request unavailable after 3 attempts")))
        self.assertTrue(retryable(RuntimeError("risk stop"),"disconnected, stale data, clock or incomplete subscriptions"))
        for halt in ("operator stop","floating loss stop reached","exchange self-trade prevention triggered","clock skew protection triggered"):
            self.assertFalse(retryable(RateLimited("429"),halt))
        self.assertFalse(retryable(UncertainOrder("unknown")))
        self.assertFalse(retryable(ExchangeError("mutation HTTP 400: setLeverage")))
        self.assertFalse(retryable(ExchangeError("startup requires no open orders")))

class RateLimitTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.api=PublicAPI(Config(),log);self.api.market=market()
        with credentials():self.live=Live(self.api,Ledger(100),log)
    async def asyncTearDown(self):await self.api.http.aclose()
    async def test_429_waits_without_resending_and_blocks_cooldown(self):
        self.api.http.post=AsyncMock(return_value=httpx.Response(429,headers={"Retry-After":"12"},json={"retryAfterMs":15000}))
        before=time.monotonic()
        with self.assertRaises(RateLimited):await self.live.post("cancelAllOrders",self.live.scope())
        self.assertGreaterEqual(self.api.cooldown,before+15)
        with self.assertRaises(RateLimited):await self.live.post("cancelAllOrders",self.live.scope())
        self.api.http.post.assert_awaited_once()
    async def test_429_plain_text_response_safe(self):
        self.api.http.post=AsyncMock(return_value=httpx.Response(429,text="limited"))
        with self.assertRaises(RateLimited):await self.live.post("cancelAllOrders",self.live.scope())
        self.assertGreater(self.api.cooldown,time.monotonic())
    async def test_rate_limited_placement_releases_unsent_reservation_without_failure(self):
        self.api.fresh=lambda:True
        self.live.post=AsyncMock(side_effect=RateLimited("429"))
        with self.assertRaises(RateLimited):await self.live.place(Quote("BUY-1","BUY",D(99990),D(".0001")),book())
        self.assertEqual(self.live.orders,{})
        self.assertEqual(self.live.failures,0)
    async def test_cleanup_waits_then_cancels(self):
        e=Engine(self.api.c,self.api,log,live=True);e.venue=self.live
        self.api.cooldown=time.monotonic()+.01
        self.live.cancel_all=AsyncMock(side_effect=[RateLimited("429"),None])
        await e.cancel_all()
        self.assertEqual(self.live.cancel_all.await_count,2)

    async def test_activation_limit_waits_in_same_session_before_trading(self):
        e=Engine(replace(self.api.c,quote_interval_seconds=.001),self.api,log,live=True)
        e.venue=self.live;e.taker_fee=D(0);self.api.book=book();self.live.equity=D(100)
        self.api.fresh=lambda:True;self.api.clock_checked=time.monotonic()
        e.setup=AsyncMock()
        async def idle():await asyncio.Event().wait()
        self.api.stream=idle;e.watchdog=idle
        self.live.enable=AsyncMock(side_effect=[RateLimited("429"),None])
        self.live.reconcile=AsyncMock(return_value=True);self.live.cancel_all=AsyncMock()
        async def cycle():
            self.assertTrue(e.trading_enabled)
            self.assertEqual(self.live.enable.await_count,2)
            e.trip("operator stop")
        e.cycle=AsyncMock(side_effect=cycle)
        reason=await e.run()
        self.assertEqual(reason,"operator stop")
        self.assertEqual(e.failure_count,0)
        e.cycle.assert_awaited_once()
        self.assertFalse(e.trading_enabled)
    async def test_protection_retry_does_not_repeat_leverage_change(self):
        async def post(action,body):
            if action=="scheduleCancel" and self.live.post.await_count==2:raise RateLimited("429")
            return {}
        self.live.post=AsyncMock(side_effect=post)
        self.api.get=AsyncMock(return_value={"leverages":[{"marketId":1,"leverage":3,"marginMode":"CROSS"}]})
        with self.assertRaises(RateLimited):await self.live.enable()
        self.assertTrue(self.live.leverage_confirmed)
        self.assertEqual(self.live.arm_time,0)
        await self.live.enable()
        self.assertGreater(self.live.arm_time,0)
        self.assertEqual([c.args[0] for c in self.live.post.await_args_list],["setLeverage","scheduleCancel","scheduleCancel"])

    async def test_daily_quota_defers_protection_only_and_keeps_cancel_available(self):
        self.api.http.post=AsyncMock(side_effect=[httpx.Response(429,json={"error":"schedule cancel trigger limit reached (10 per UTC day)"}),httpx.Response(200,json={})])
        body=dict(self.live.scope(),time=2000000000000000)
        with self.assertRaises(RateLimited):await self.live.post("scheduleCancel",body)
        self.assertEqual(self.live.protection_block_reason,"daily_trigger_limit")
        self.assertGreater(self.live.protection_cooldown,time.monotonic())
        self.assertGreater(self.live.protection_retry_at_ms,time.time()*1000)
        self.api.cooldown=0
        with self.assertRaises(RateLimited):await self.live.post("scheduleCancel",body)
        self.api.http.post.assert_awaited_once()
        await self.live.post("cancelAllOrders",self.live.scope())
        self.assertEqual(self.api.http.post.await_count,2)
    async def test_stop_loss_still_runs_while_daily_quota_blocks_startup(self):
        e=Engine(self.api.c,self.api,log,live=True);e.venue=self.live;e.taker_fee=D(0)
        self.api.book=book("89999","90001");self.api.fresh=lambda:True;self.api.clock_checked=time.monotonic()
        e.ledger.qty=D(".001");e.ledger.entry=D(100000);self.live.equity=D(99)
        self.live.protection_cooldown=time.monotonic()+86400
        self.live.reconcile=AsyncMock(return_value=True);self.live.enable=AsyncMock()
        e.setup=AsyncMock();e.cancel_all=AsyncMock();e.close_position=AsyncMock(return_value=True)
        async def idle():await asyncio.Event().wait()
        self.api.stream=idle;e.watchdog=idle
        reason=await e.run()
        self.assertEqual(reason,"floating loss stop reached")
        self.assertTrue(e.flatten)
        self.live.enable.assert_not_awaited()
        e.close_position.assert_awaited_once()
        self.assertGreaterEqual(self.live.reconcile.await_count,1)

class ProtectionQuotaParsing(unittest.TestCase):
    def test_utc_boundary_with_safety_margin(self):
        result=daily_protection_quota({"error":"schedule cancel trigger limit reached (10 per UTC day)"},86398)
        self.assertEqual(result,(7,86400))
    def test_ordinary_limit_does_not_become_daily_quota(self):
        self.assertIsNone(daily_protection_quota({"error":"rate limit reached"},1234))
