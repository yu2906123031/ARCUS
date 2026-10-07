import unittest
from dataclasses import replace
from unittest.mock import AsyncMock
from arcus_mm.api import PublicAPI,Live,ExchangeError,UnclassifiedRejection,RequestForbidden,RateLimited
from arcus_mm.config import Config
from arcus_mm.models import Ledger
from tests.test_mm import credentials,market,log

class LeverageActivation(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.api=PublicAPI(replace(Config(),leverage_cap=5,server_protection=False),log);self.api.market=market()
        with credentials():self.live=Live(self.api,Ledger(100),log)
        self.live.post=AsyncMock(return_value={})
    async def asyncTearDown(self):await self.api.http.aclose()
    def state(self,leverage=5,mode="CROSS"):
        return {"leverages":[dict(marketId=1,leverage=leverage,marginMode=mode,isolated=mode=="ISOLATED")]}
    async def test_existing_five_cross_needs_no_write(self):
        self.api.get=AsyncMock(return_value=self.state())
        await self.live.enable();await self.live.enable()
        self.live.post.assert_not_awaited();self.assertTrue(self.live.leverage_confirmed)
        self.api.get.assert_awaited_once()
    async def test_different_leverage_is_set_once_and_verified(self):
        self.api.get=AsyncMock(side_effect=[self.state(3),self.state(5)])
        await self.live.enable()
        self.live.post.assert_awaited_once()
        action,body=self.live.post.await_args.args
        self.assertEqual(action,"setLeverage");self.assertEqual(body["leverage"],5);self.assertFalse(body["isolated"])
    async def test_unknown_403_with_verified_target_is_recovered(self):
        self.api.get=AsyncMock(side_effect=[self.state(3),self.state(5)])
        self.live.post.side_effect=UnclassifiedRejection("403")
        await self.live.enable()
        self.live.post.assert_awaited_once();self.assertTrue(self.live.leverage_confirmed);self.assertIsNone(self.live.fatal)
    async def test_unknown_403_with_mismatch_never_repeats_write(self):
        self.api.get=AsyncMock(return_value=self.state(3));self.live.post.side_effect=UnclassifiedRejection("403")
        for _ in range(2):
            with self.assertRaisesRegex(ExchangeError,"expected 5x CROSS; observed 3x CROSS"):await self.live.enable()
        self.live.post.assert_awaited_once();self.assertFalse(self.live.leverage_confirmed)
    async def test_isolated_five_is_not_adopted_as_cross(self):
        self.api.get=AsyncMock(side_effect=[self.state(5,"ISOLATED"),self.state()])
        await self.live.enable();self.live.post.assert_awaited_once()
    async def test_explicit_permission_denial_is_not_overridden(self):
        self.api.get=AsyncMock(return_value=self.state(3));self.live.post.side_effect=RequestForbidden("permission")
        with self.assertRaises(RequestForbidden):await self.live.enable()
        self.assertFalse(self.live.leverage_confirmed);self.api.get.assert_awaited_once()
    async def test_failed_read_never_triggers_write(self):
        self.api.get=AsyncMock(side_effect=RateLimited("429"))
        with self.assertRaises(RateLimited):await self.live.enable()
        self.live.post.assert_not_awaited()
    async def test_missing_market_does_not_guess_leverage(self):
        self.api.get=AsyncMock(return_value={"leverages":[]})
        with self.assertRaisesRegex(ExchangeError,"unique market"):await self.live.enable()
        self.live.post.assert_not_awaited()

    async def test_denial_recheck_read_limit_never_causes_another_write(self):
        self.api.get=AsyncMock(side_effect=[self.state(3),RateLimited("429"),self.state(3)])
        self.live.post.side_effect=UnclassifiedRejection("403")
        with self.assertRaises(RateLimited):await self.live.enable()
        with self.assertRaisesRegex(ExchangeError,"expected 5x CROSS"):await self.live.enable()
        self.live.post.assert_awaited_once();self.assertFalse(self.live.leverage_confirmed)
