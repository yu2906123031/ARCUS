import tempfile
import json
import unittest
import time
from pathlib import Path
from decimal import Decimal as D
from dataclasses import replace
from unittest.mock import AsyncMock
from arcus_mm.recovery import Resume,load_resume,latest_resume,save_checkpoint,load_checkpoint
from arcus_mm.api import PublicAPI,Live,ExchangeError
from arcus_mm.config import Config
from arcus_mm.models import Ledger
from tests.test_mm import credentials,market,log

class ResumePreflight(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.api=PublicAPI(replace(Config(),capital_cap="100"),log);self.api.market=market()
        with credentials():self.live=Live(self.api,Ledger(100),log)
        self.resume=Resume(1000000,"100","BTC-USD",0)
        self.fill=dict(tradeId="t",orderId="o",marketId=1,side="SELL",size=".001",price="85000",fee=".01",role="MAKER",createdAt=2000000,sequenceNumber=2)
        self.account=dict(equity="99",freeCollateral="10",positions={"1":{"size":"-.001"}},sequenceNumber=3)
        self.open_orders=[]
        async def get(path,params=None):
            if path=="/v1/apiKeys":return {"apiKeys":[dict(apiKey=self.live.signer.public,status="ACTIVE",accountIndex=0,validUntil=0)]}
            if path=="/v1/account":return self.account
            if path=="/v1/openOrders":return {"orders":self.open_orders}
            if path=="/v1/fills":return {"fills":[self.fill]}
            raise AssertionError(path)
        self.api.get=AsyncMock(side_effect=get)
    async def asyncTearDown(self):await self.api.http.aclose()
    async def test_replays_cost_basis_fees_and_proves_position(self):
        await self.live.preflight(self.resume)
        self.assertEqual(self.live.ledger.qty,D("-.001"))
        self.assertEqual(self.live.ledger.entry,D(85000))
        self.assertEqual(self.live.ledger.fees,D(".01"))
        self.assertEqual(self.live.ledger.volume,D(85))
        self.assertEqual(self.live.ledger.initial,D(100))

    async def test_preflight_pages_through_more_than_one_thousand_fills(self):
        fills=[]
        for i in range(1001):
            fills.append({**self.fill,"tradeId":str(i),"createdAt":2000000+i,"sequenceNumber":i+1,
                          "side":"BUY" if i%2==0 else "SELL","size":".001"})
        self.account["positions"]["1"]["size"]=".001"
        self.account["sequenceNumber"]=2000
        previous=self.api.get.side_effect
        async def get(path,params=None):
            if path!="/v1/fills": return await previous(path,params)
            upper=int(params.get("to",10**30))
            rows=[row for row in fills if row["createdAt"]>=params["from"] and row["createdAt"]<=upper]
            return {"fills":list(reversed(rows))[:1000]}
        self.api.get.side_effect=get
        await self.live.preflight(self.resume)
        self.assertEqual(self.live.ledger.qty,D(".001"))
        fill_calls=[c for c in self.api.get.await_args_list if c.args[0]=="/v1/fills"]
        self.assertEqual(len(fill_calls),2)
        self.assertIn("to",fill_calls[1].args[1])

    async def test_auto_resume_uses_checkpoint_and_fetches_only_incremental_fills(self):
        with tempfile.TemporaryDirectory() as d:
            save_checkpoint(d,"BTC-USD",0,self.live.ledger,
                            trade_id="old",created_at=19000000,sequence_number=1)
            self.fill.update(tradeId="new",createdAt=80000000,sequenceNumber=2)
            await self.live.preflight(auto_resume_dir=d)
            call=next(c for c in self.api.get.await_args_list if c.args[0]=="/v1/fills")
            self.assertEqual(call.args[1]["from"],13000000)
            self.assertEqual(self.live.ledger.qty,D("-.001"))
            checkpoint=load_checkpoint(d,"BTC-USD",0)
            self.assertEqual(checkpoint.trade_id,"new")
            self.assertEqual(checkpoint.sequence_number,2)

    async def test_checkpoint_overlap_does_not_reapply_old_trade(self):
        with tempfile.TemporaryDirectory() as d:
            self.live.ledger.fill("BUY",".001","84000",".01",True)
            save_checkpoint(d,"BTC-USD",0,self.live.ledger,
                            trade_id="old",created_at=2000000,sequence_number=2)
            self.fill.update(tradeId="old",side="BUY",price="84000",createdAt=2000000,sequenceNumber=2)
            self.fill.pop("sequenceNumber")
            self.account["positions"]["1"]["size"]=".001"
            await self.live.preflight(auto_resume_dir=d)
            self.assertEqual(self.live.ledger.qty,D(".001"))
            self.assertEqual(self.live.ledger.fees,D(".01"))

    async def test_same_timestamp_new_fills_are_all_applied_in_sequence_order(self):
        with tempfile.TemporaryDirectory() as d:
            save_checkpoint(d,"BTC-USD",0,self.live.ledger,
                            trade_id="old",created_at=19000000,sequence_number=10)
            low={**self.fill,"tradeId":"z","createdAt":20000000,"side":"BUY","size":".001","sequenceNumber":11}
            high={**self.fill,"tradeId":"a","createdAt":20000000,"side":"BUY","size":".001","sequenceNumber":12}
            account={**self.account,"positions":{"1":{"size":".002"}},"sequenceNumber":12}
            key=dict(apiKey=self.live.signer.public,status="ACTIVE",accountIndex=0,validUntil=0)
            self.api.get=AsyncMock(side_effect=[{"apiKeys":[key]},account,{"orders":[]},account,{"fills":[high,low]}])
            await self.live.preflight(auto_resume_dir=d)
            self.assertEqual(self.live.ledger.qty,D(".002"))

    async def test_delayed_increment_keeps_checkpointed_cost_basis(self):
        with tempfile.TemporaryDirectory() as d:
            self.live.ledger.fill("BUY",".001","84000",".01",True)
            save_checkpoint(d,"BTC-USD",0,self.live.ledger,
                            trade_id="old",created_at=20000000,sequence_number=10)
            delayed={**self.fill,"tradeId":"new","createdAt":19000001,"side":"SELL","size":".0005","sequenceNumber":11}
            account={**self.account,"positions":{"1":{"size":".0005"}},"sequenceNumber":11}
            key=dict(apiKey=self.live.signer.public,status="ACTIVE",accountIndex=0,validUntil=0)
            self.api.get=AsyncMock(side_effect=[{"apiKeys":[key]},account,{"orders":[]},account,{"fills":[delayed]}])
            await self.live.preflight(auto_resume_dir=d)
            self.assertEqual(self.live.ledger.qty,D(".0005"))
            self.assertEqual(self.live.ledger.entry,D("84000"))

    async def test_global_sequence_gap_commits_only_after_rest_reconciliation(self):
        with tempfile.TemporaryDirectory() as d:
            base=Ledger(D("100"))
            save_checkpoint(d,"BTC-USD",0,base,"old",19000000,10)
            gap={**self.fill,"tradeId":"gap","createdAt":20000000,"side":"BUY","size":".001","sequenceNumber":12}
            account={**self.account,"positions":{"1":{"size":".001"}},"sequenceNumber":12}
            key=dict(apiKey=self.live.signer.public,status="ACTIVE",accountIndex=0,validUntil=0)
            self.api.get=AsyncMock(side_effect=[{"apiKeys":[key]},account,{"orders":[]},account,{"fills":[gap]}])
            await self.live.preflight(auto_resume_dir=d)
            checkpoint=load_checkpoint(d,"BTC-USD",0)
            self.assertEqual(checkpoint.trade_id,"gap")
            self.assertEqual(checkpoint.sequence_number,12)

    async def test_unknown_low_sequence_blocks_checkpoint_advance(self):
        with tempfile.TemporaryDirectory() as d:
            base=Ledger(D("100"))
            save_checkpoint(d,"BTC-USD",0,base,"old",19000000,10)
            unknown={**self.fill,"tradeId":"unknown","createdAt":19000001,"sequenceNumber":9}
            account={**self.account,"sequenceNumber":10}
            key=dict(apiKey=self.live.signer.public,status="ACTIVE",accountIndex=0,validUntil=0)
            self.api.get=AsyncMock(side_effect=[{"apiKeys":[key]},account,{"orders":[]},account,{"fills":[unknown]}])
            with self.assertRaises(ExchangeError):
                await self.live.preflight(auto_resume_dir=d)
            self.assertEqual(load_checkpoint(d,"BTC-USD",0).sequence_number,10)

    async def test_websocket_fill_never_advances_checkpoint_before_rest_reconcile(self):
        with tempfile.TemporaryDirectory() as d:
            base=Ledger(D("100"))
            save_checkpoint(d,"BTC-USD",0,base,"old",19000000,10)
            self.live.checkpoint_dir=d
            self.live.has_checkpoint=True
            self.live.checkpoint_boundary_seq=10
            self.live.checkpoint_recent={"old":(19000000,10)}
            self.live.started_us=13000000
            self.live.checkpoint_base=dict(base.__dict__)
            late={**self.fill,"tradeId":"late","createdAt":20000000,"side":"BUY","size":".001","sequenceNumber":11}
            self.live.apply_fill(late)
            self.assertEqual(load_checkpoint(d,"BTC-USD",0).sequence_number,10)
            account={**self.account,"positions":{"1":{"size":".001"}},"sequenceNumber":11}
            persisted={k:v for k,v in late.items() if k!="sequenceNumber"}
            self.api.get=AsyncMock(side_effect=[account,{"fills":[persisted]}])
            self.assertTrue(await self.live.reconcile())
            self.assertEqual(load_checkpoint(d,"BTC-USD",0).sequence_number,11)

    async def test_auto_resume_replays_existing_position(self):
        with tempfile.TemporaryDirectory() as d:
            write_session(Path(d)/"live-20261007-000001.jsonl",since=1000000)
            await self.live.preflight(auto_resume_dir=d)
        self.assertEqual(self.live.ledger.qty,D("-.001"))
        self.assertEqual(self.live.ledger.entry,D(85000))
        self.assertEqual(self.live.ledger.initial,D(100))
    async def test_auto_resume_flat_account_needs_no_log(self):
        self.account["positions"]={}
        self.fill["createdAt"]=0
        with tempfile.TemporaryDirectory() as d:
            await self.live.preflight(auto_resume_dir=d)
        self.assertEqual(self.live.ledger.qty,0)
        self.assertEqual(self.live.ledger.initial,D(99))
    async def test_auto_resume_missing_log_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(ValueError,"no matching"):
                await self.live.preflight(auto_resume_dir=d)
    async def test_auto_resume_mismatched_position_rejected(self):
        self.account["positions"]["1"]["size"]="-.002"
        with tempfile.TemporaryDirectory() as d:
            write_session(Path(d)/"live-20261007-000001.jsonl",since=1000000)
            with self.assertRaises(ExchangeError): await self.live.preflight(auto_resume_dir=d)
    async def test_default_start_still_rejects_existing_position(self):
        with self.assertRaises(ExchangeError):await self.live.preflight()
    async def test_mismatched_position_rejects_takeover(self):
        self.account["positions"]["1"]["size"]="-.002"
        with self.assertRaises(ExchangeError):await self.live.preflight(self.resume)
    async def test_open_orders_reject_takeover(self):
        self.open_orders=[{"orderId":"other"}]
        with self.assertRaises(ExchangeError):await self.live.preflight(self.resume)
    async def test_auto_resume_cancels_logged_orders_then_replays_racing_fill(self):
        self.open_orders=[dict(orderId="old",clientId="mm-old",marketId=1)]
        self.live.post=AsyncMock(return_value={"status":"accepted"})
        previous=self.api.get.side_effect
        async def get(path,params=None):
            if path=="/v1/order/old":
                self.open_orders=[]
                # A fill racing cancellation must be included in the recovered ledger.
                return dict(orderId="old",status="CANCELED")
            return await previous(path,params)
        self.api.get.side_effect=get
        with tempfile.TemporaryDirectory() as root:
            p=Path(root)/"live-20261007-000001.jsonl";write_session(p)
            with p.open("a",encoding="utf-8") as f:f.write('\n'+json.dumps(dict(mode="live",event="order",client_id="mm-old")))
            await self.live.preflight(auto_resume_dir=root,allow_cancel=True)
        self.live.post.assert_awaited_once()
        self.assertEqual(self.live.post.call_args.args[0],"cancelOrder")
        self.assertEqual(self.live.ledger.qty,D("-.001"))
        self.assertEqual(self.live.ledger.initial,D(100))

    async def test_read_only_doctor_never_cancels_open_orders(self):
        self.open_orders=[dict(orderId="old",clientId="mm-old",marketId=1)]
        self.live.post=AsyncMock()
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaisesRegex(ExchangeError,"no open orders"):
                await self.live.preflight(auto_resume_dir=root)
        self.live.post.assert_not_awaited()

    async def test_unknown_or_other_market_orders_block_before_any_cancel(self):
        self.live.post=AsyncMock()
        for row in (dict(orderId="old",clientId="mm-unlogged",marketId=1),dict(orderId="old",clientId="mm-old",marketId=2)):
            self.open_orders=[dict(orderId="known",clientId="mm-old",marketId=1),row]
            with tempfile.TemporaryDirectory() as root:
                p=Path(root)/"live-20261007-000001.jsonl";write_session(p)
                with p.open("a",encoding="utf-8") as f:f.write('\n'+json.dumps(dict(mode="live",event="order",client_id="mm-old")))
                with self.assertRaisesRegex(ExchangeError,"not proven"):
                    await self.live.preflight(auto_resume_dir=root,allow_cancel=True)
        self.live.post.assert_not_awaited()

    async def test_unknown_cancel_response_is_not_retried(self):
        from arcus_mm.api import UncertainOrder
        self.live.post=AsyncMock(side_effect=UncertainOrder("unknown"))
        with self.assertRaises(UncertainOrder):
            await self.live.cancel_startup_orders([dict(orderId="old",clientId="mm-old",marketId=1)],{"mm-old"})
        self.live.post.assert_awaited_once()

    async def test_another_subaccount_rejects_takeover(self):
        with self.assertRaises(ExchangeError):await self.live.preflight(replace(self.resume,account_index=1))

class ResumeLog(unittest.TestCase):
    def test_preserves_replay_origin_after_multiple_resumes(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/"log.jsonl"
            rows=[dict(mode="live",event="preflight",equity="99",initial_equity="100",account_index=0,resume_since_us=1000000),dict(mode="live",event="session",market="BTC-USD",time_ns=time.time_ns())]
            p.write_text("\n".join(json.dumps(r) for r in rows),encoding="utf-8")
            resume=load_resume(p)
            self.assertEqual(resume.since_us,1000000)
            self.assertEqual(resume.initial_equity,"100")


def write_session(path, market="BTC-USD", account_index=0, since=1000000):
    rows=[dict(mode="live",event="preflight",equity="99",initial_equity="100",account_index=account_index,resume_since_us=since),
          dict(mode="live",event="session",market=market,time_ns=time.time_ns())]
    path.write_text("\n".join(json.dumps(r) for r in rows),encoding="utf-8")

class AutoResumeLog(unittest.TestCase):
    def test_skips_failed_start_and_wrong_account_preserving_latest_origin(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            write_session(root/"live-20261007-000001.jsonl",since=1000000)
            write_session(root/"live-20261007-000002.jsonl",since=2000000)
            write_session(root/"live-20261007-000003.jsonl",account_index=1)
            (root/"live-20261007-000004.jsonl").write_text('{"mode":"live","event":"fatal"}',encoding="utf-8")
            path,resume=latest_resume(root,"BTC-USD",0)
            self.assertEqual(path.name,"live-20261007-000002.jsonl")
            self.assertEqual(resume.since_us,2000000)
            self.assertEqual(resume.initial_equity,"100")
    def test_interrupted_trailing_line_does_not_destroy_replay_origin(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/"live-20261007-000001.jsonl"
            write_session(path)
            with path.open("a",encoding="utf-8") as f: f.write('\n{"event":')
            _,resume=latest_resume(d,"BTC-USD",0)
            self.assertEqual(resume.since_us,1000000)
    def test_wrong_market_does_not_match(self):
        with tempfile.TemporaryDirectory() as d:
            write_session(Path(d)/"live-20261007-000001.jsonl",market="ETH-USD")
            with self.assertRaises(ValueError): latest_resume(d,"BTC-USD",0)
