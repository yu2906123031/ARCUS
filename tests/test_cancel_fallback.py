import unittest
import time
from dataclasses import replace
from decimal import Decimal as D
from unittest.mock import AsyncMock
import httpx
from arcus_mm.api import PublicAPI,Live,UnclassifiedRejection,RequestForbidden,RateLimited,UncertainOrder,ExchangeError
from arcus_mm.models import Ledger,Quote,Order
from arcus_mm.config import Config
from tests.test_mm import credentials,market,log

class CancelFallback(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.api=PublicAPI(replace(Config(),confirmation_seconds=.01),log);self.api.market=market()
        with credentials(): self.live=Live(self.api,Ledger(100),log)
        self.rows=[];self.states={}
        async def get(path,params=None):
            if path=="/v1/openOrders":return {"orders":list(self.rows)}
            ident=path.split("/")[-1]
            return dict(orderId=ident,status=self.states[ident])
        self.api.get=AsyncMock(side_effect=get)
        async def post(action,body):
            if action=="cancelAllOrders":
                self.rows=[]
                for key in self.states:self.states[key]="CANCELED"
            else:
                ident=next(r["orderId"] for r in self.rows if r["clientId"]==body["clientId"])
                self.states[ident]="CANCELED"
                self.rows=[r for r in self.rows if r["orderId"]!=ident]
            return {}
        self.post_ok=post;self.live.post=AsyncMock(side_effect=post)
    async def asyncTearDown(self):await self.api.http.aclose()
    def orders(self,count):
        for i in range(count):
            client=f"mm-{i}";ident=f"o-{i}";q=Quote(f"BUY-{i}","BUY",D(100),D(".01"))
            o=Order(q,client,q.qty,time.monotonic(),order_id=ident,status="OPEN")
            self.live.history[client]=o;self.live.orders[q.slot]=o
            self.rows.append(dict(clientId=client,orderId=ident,marketId=1));self.states[ident]="OPEN"
    async def test_no_open_orders_and_terminal_fill_need_no_mutation(self):
        self.orders(1);self.rows=[];self.states["o-0"]="FILLED"
        await self.live.cancel_all()
        self.live.post.assert_not_awaited();self.assertEqual(self.live.orders,{})
    async def test_empty_snapshot_does_not_prove_unacknowledged_order(self):
        self.orders(1);self.rows=[];self.live.orders["BUY-0"].order_id=""
        with self.assertRaises(UncertainOrder):await self.live.cancel_all()
        self.live.post.assert_not_awaited()
    async def test_empty_snapshot_does_not_override_nonterminal_order_state(self):
        self.orders(1);self.rows=[]
        with self.assertRaises(UncertainOrder):await self.live.cancel_all()
        self.assertIn("BUY-0",self.live.orders)
        self.live.post.assert_not_awaited()

    async def test_one_or_two_orders_use_individual_cancel(self):
        self.orders(2);await self.live.cancel_all()
        self.assertEqual([c.args[0] for c in self.live.post.await_args_list],["cancelOrder","cancelOrder"])
        self.assertEqual(self.live.orders,{})
    async def test_bulk_unclassified_403_falls_back_and_verifies(self):
        self.orders(3)
        async def post(action,body):
            if action=="cancelAllOrders":raise UnclassifiedRejection("403")
            return await self.post_ok(action,body)
        self.live.post.side_effect=post
        await self.live.cancel_all()
        self.assertEqual([c.args[0] for c in self.live.post.await_args_list],["cancelAllOrders","cancelOrder","cancelOrder","cancelOrder"])
        self.assertEqual(self.live.orders,{})
        self.assertIsNone(self.live.fatal)
    async def test_bulk_denial_after_fill_is_recovered_without_more_writes(self):
        self.orders(3)
        async def post(action,body):
            self.rows=[]
            for key in self.states:self.states[key]="FILLED"
            raise UnclassifiedRejection("403")
        self.live.post.side_effect=post
        await self.live.cancel_all();self.live.post.assert_awaited_once()
        self.assertEqual(self.live.orders,{})
    async def test_bulk_429_fallback_obeys_cooldown_and_does_not_retry_bulk(self):
        self.orders(3)
        async def post(action,body):
            if action=="cancelAllOrders":self.api.cooldown=time.monotonic()+10;raise RateLimited("429")
            if time.monotonic()<self.api.cooldown:raise RateLimited("cooldown")
            return await self.post_ok(action,body)
        self.live.post.side_effect=post
        with self.assertRaises(RateLimited):await self.live.cancel_all()
        self.assertEqual(len(self.live.orders),3)
        self.api.cooldown=0
        await self.live.cancel_all()
        self.assertEqual(sum(c.args[0]=="cancelAllOrders" for c in self.live.post.await_args_list),1)
        self.assertEqual(self.live.orders,{})
    async def test_permission_denial_does_not_fallback(self):
        self.orders(3);self.live.post.side_effect=RequestForbidden("permission")
        with self.assertRaises(RequestForbidden):await self.live.cancel_all()
        self.live.post.assert_awaited_once()
    async def test_foreign_order_is_not_cancelled(self):
        self.rows=[dict(clientId="manual",orderId="other",marketId=1)]
        with self.assertRaisesRegex(ExchangeError,"not proven"):await self.live.cancel_all()
        self.live.post.assert_not_awaited()
    async def test_unknown_403_is_not_permanently_latched(self):
        self.live.post=Live.post.__get__(self.live)
        self.api.http.post=AsyncMock(return_value=httpx.Response(403,json={"error":"unknown upstream failure"}))
        with self.assertRaises(UnclassifiedRejection):await self.live.post("cancelAllOrders",self.live.scope())
        self.assertIsNone(self.live.fatal);self.assertEqual(self.live.forbidden_actions,{})
        self.assertGreater(self.api.cooldown,time.monotonic()+9)
    async def test_individual_403_racing_a_fill_is_confirmed(self):
        self.orders(1)
        async def post(action,body):
            self.rows=[];self.states["o-0"]="FILLED"
            raise UnclassifiedRejection("403")
        self.live.post.side_effect=post
        await self.live.cancel("BUY-0")
        self.assertEqual(self.live.orders,{})
        self.assertIsNone(self.live.fatal)

    async def test_individual_cleanup_works_with_real_live_logger(self):
        import tempfile,json,io
        from contextlib import redirect_stdout
        from arcus_mm.models import Log
        self.orders(2)
        with tempfile.TemporaryDirectory() as directory:
            self.live.log=Log(directory,"live")
            with redirect_stdout(io.StringIO()):await self.live.cancel_all()
            rows=[json.loads(line) for line in self.live.log.path.read_text(encoding="utf-8").splitlines()]
        cleanup=next(r for r in rows if r["event"]=="cancel_cleanup_mode")
        self.assertEqual(cleanup["mode"],"live")
        self.assertEqual(cleanup["cleanup_mode"],"individual")
        self.assertEqual(self.live.orders,{})
        self.assertEqual([call.args[0] for call in self.live.post.await_args_list],["cancelOrder","cancelOrder"])
