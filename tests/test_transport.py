import unittest
import os
from unittest.mock import AsyncMock,patch
import httpx
from arcus_mm.api import PublicAPI,ExchangeError
from arcus_mm.config import Config

class ReadRetry(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self): self.api=PublicAPI(Config(),lambda *a,**k:None)
    async def asyncTearDown(self): await self.api.http.aclose()
    async def test_one_timeout_then_recovers(self):
        r=httpx.Response(200,json={"value":1},request=httpx.Request("GET","https://api.arcus.xyz/v1/markets"))
        self.api.http.get=AsyncMock(side_effect=[httpx.ConnectTimeout("test"),r])
        with patch("arcus_mm.api.asyncio.sleep",new_callable=AsyncMock):
            self.assertEqual(await self.api.get("/v1/markets"),{"value":1})
        self.assertEqual(self.api.http.get.await_count,2)
    async def test_repeated_timeout_is_controlled_failure(self):
        self.api.http.get=AsyncMock(side_effect=httpx.ConnectTimeout("test"))
        with patch("arcus_mm.api.asyncio.sleep",new_callable=AsyncMock):
            with self.assertRaises(ExchangeError): await self.api.get("/v1/markets")
        self.assertEqual(self.api.http.get.await_count,3)
    async def test_http_error_not_blindly_retried(self):
        r=httpx.Response(429,request=httpx.Request("GET","https://api.arcus.xyz/v1/markets"))
        self.api.http.get=AsyncMock(return_value=r)
        with self.assertRaises(ExchangeError): await self.api.get("/v1/markets")
        self.assertEqual(self.api.http.get.await_count,1)

    async def test_proxy_is_scoped_to_the_service_environment(self):
        with patch.dict(os.environ,{"ARCUS_PROXY_URL":""}):
            direct=PublicAPI(Config(),lambda *a,**k:None)
        with patch.dict(os.environ,{"ARCUS_PROXY_URL":"http://proxy.example:8080"}):
            proxied=PublicAPI(Config(),lambda *a,**k:None)
        try:
            self.assertEqual(proxied.proxy,"http://proxy.example:8080")
            self.assertIsNone(direct.proxy)
        finally:
            await direct.http.aclose()
            await proxied.http.aclose()
