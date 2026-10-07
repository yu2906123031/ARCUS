import copy
import json
import time
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
from decimal import Decimal as D
from eth_account import Account
from eth_account.messages import encode_typed_data
from arcus import Blocked,ReconcileError,read
from spot_api import Spot,Quote,FeePolicyError,TransportError,addr,human,atomic
from spot_runtime import Runtime

class ApiTests(unittest.TestCase):
    def setUp(self):
        self.fixture=read('fixtures/quote.json')
        self.spot=object.__new__(Spot)
        self.spot.c=read('config.json')
        self.spot.wallet=addr(self.fixture['request']['taker'])
        self.spot.contracts=read('fixtures/deployment.json')['contracts']
        self.spot.tokens=read('fixtures/tokens.json')
        self.spot.meta={addr(t['address']):t for t in self.spot.tokens.values()}
        self.spot.account=None
        self.row=copy.deepcopy(self.fixture['body']['all'][0])
        expiry=int(time.time()+60)
        self.row['expiry']=expiry
        self.row['toSign']['message']['deadline']=str(expiry)
        self.row['toSign']['message']['witness']['deadline']=str(expiry)
        self.params=self.fixture['request']
    def quote(self):return self.spot.parse_quote(self.row,self.params,'BUY')
    def test_real_payload_parses(self):
        q=self.quote()
        self.assertEqual(q.sell_raw,10000000)
        self.assertEqual(q.buy_raw,43196101033525016)
        self.assertEqual(self.spot.output(q),D('0.043196101033525016'))
    def test_tampered_signatures_blocked(self):
        edits=[('chain',lambda r:r['toSign']['domain'].update(chainId=1)),
               ('spender',lambda r:r['toSign']['message'].update(spender=self.spot.wallet)),
               ('taker',lambda r:r['toSign']['message']['witness'].update(taker=self.spot.contracts['permit2'])),
               ('wrapping',lambda r:r['toSign']['message']['witness'].update(allowWrapped=True)),
               ('quantity',lambda r:r['toSign']['message']['permitted'].update(amount='100000000')),
               ('minimum',lambda r:r['toSign']['message']['witness'].update(minBuyAmount='1')),
               ('nonce',lambda r:r['toSign']['message']['witness'].update(nonce='1')),
               ('extra',lambda r:r['toSign']['message'].update(external='surprise'))]
        original=copy.deepcopy(self.row)
        for name,edit in edits:
            with self.subTest(name=name):
                self.row=copy.deepcopy(original);edit(self.row)
                with self.assertRaises(Blocked):self.quote()
    def test_expired(self):
        self.row['expiry']=int(time.time()-1)
        self.row['toSign']['message']['deadline']=str(self.row['expiry'])
        self.row['toSign']['message']['witness']['deadline']=str(self.row['expiry'])
        with self.assertRaises(Blocked):self.quote()
    def test_actual_fees_block_original_policy(self):
        q=self.quote()
        with self.assertRaises(FeePolicyError):self.spot.fee_policy(q)
        self.spot.c['accept_quote_fees']=True
        self.spot.fee_policy(q)
    def test_sign_and_recover_ephemeral_test_key(self):
        # Ephemeral test key only; no mnemonic, persistent secret or network call.
        self.spot.account=Account.create()
        self.spot.wallet=addr(self.spot.account.address)
        self.row['toSign']['message']['witness']['taker']=self.spot.account.address
        q=self.quote();body=self.spot.signed_body(q)
        recovered=Account.recover_message(encode_typed_data(full_message=q.typed_data),signature=body['signature'])
        self.assertEqual(addr(recovered),self.spot.wallet)
        self.assertEqual(body['venue'],'arcus')
    def test_exact_precision(self):
        self.assertEqual(atomic('10',6),10000000)
        with self.assertRaises(Blocked):atomic('0.0000001',6)

class ExecutionTests(ApiTests):
    def runtime(self):
        r=object.__new__(Runtime)
        r.live=True;r.c=read('config.json');r.spot=self.spot
        r.s={'pending':None,'position':None,'halt':False,'cash':'100','rounds':0,'bad_exits':0,'pnl':'0','trades':[],
             'balances':{'USDG':100000000,'NVDA':0,'SPY':0,'QQQ':0},'calibration':[]}
        r.day='2026-09-29';r.save=Mock();r.log=Mock();r.preview=Mock()
        r.reconcile=Mock(return_value={**r.s['balances'],'block':1,'eth':10**18})
        self.spot.ensure_allowance=Mock();self.spot.signed_body=Mock(return_value={'signature':'test-only'})
        self.spot.fee_policy=Mock();self.spot.router=Mock()
        return r
    def test_submit_timeout_does_not_retry(self):
        r=self.runtime();self.spot.router.submit.side_effect=TransportError('timeout')
        with self.assertRaises(ReconcileError):r.execute(self.quote())
        self.assertTrue(r.s['halt']);self.assertIsNotNone(r.s['pending'])
        with self.assertRaises(ReconcileError):r.execute(self.quote())
        self.assertEqual(self.spot.router.submit.call_count,1)
        self.assertNotIn('signature',json.dumps(r.s['pending']))
        self.assertIsNone(r.s['position'])
    def test_dry_run_never_posts(self):
        r=self.runtime();r.live=False;r.execute(self.quote())
        self.spot.router.submit.assert_not_called()
        self.spot.signed_body.assert_not_called()
        self.assertIsNotNone(r.s['position'])
        self.assertEqual(r.s['trades'][-1]['status'],'SIMULATED')
        self.assertEqual(r.s['cash'],'90')
    def test_buy_commit_only_from_actual_fill(self):
        r=self.runtime();q=self.quote()
        r.s['pending']={'side':'BUY','sell_token':q.sell_token,'buy_token':q.buy_token,'reason':None}
        r.commit(q.sell_raw,q.min_raw,'0x'+'1'*64,{'USDG':90000000,'NVDA':q.min_raw})
        self.assertEqual(D(r.s['position']['token_amount']),self.spot.output(q,True))
        self.assertIsNone(r.s['pending'])
    def test_pending_without_hash_is_never_resent(self):
        r=self.runtime();r.s['pending']={'txHash':None}
        with self.assertRaises(ReconcileError):r.resolve()
        self.spot.router.submit.assert_not_called()
    def test_final_quote_daily_loss_forces_exit(self):
        r=self.runtime()
        r.s.update(date=__import__('arcus').now().date().isoformat(),round_trip_bps='10',pnl='-4.99',last_sell_px=None)
        r.s['position']={'usdg_spent':'10','token_amount':'1','opened_epoch':time.time(),'exit_reason':'target','calibration':False}
        q=Mock()
        self.spot.quote=Mock(return_value=q)
        self.spot.output=Mock(return_value=D('9.98'))
        r.execute=Mock()
        r.step()
        self.assertTrue(r.s['halt'])
        self.assertEqual(r.execute.call_args.args[1],'stop')
    def test_receipt_balance_mismatch_blocks_commit(self):
        r=self.runtime();q=self.quote();tx='0x'+'1'*64
        p={'txHash':tx,'before':r.s['balances']}
        self.spot.rpc=Mock()
        self.spot.rpc.call.side_effect=['0x3',{'hash':'0xabc'}]
        self.spot.transfer_deltas=Mock(return_value={'USDG':-10000000,'NVDA':q.min_raw,'SPY':0,'QQQ':0})
        self.spot.snapshot=Mock(return_value=r.s['balances'])
        receipt={'transactionHash':tx,'to':self.spot.contracts['arcusSettlement'],'blockNumber':'0x1','blockHash':'0xabc'}
        with self.assertRaises(ReconcileError):r.finish_receipt(p,{'status':'confirmed'},receipt)
        self.assertIsNone(r.s['position'])


class TargetGateTests(ExecutionTests):
    def held(self,minimum='10.012',expected='10.05'):
        r=self.runtime()
        r.s.update(date=__import__('arcus').now().date().isoformat(),round_trip_bps='10',last_sell_px=None,last_px=None)
        r.s['position']={'usdg_spent':'10','token_amount':'1','opened_epoch':time.time(),'exit_reason':None,'calibration':False}
        q=Quote('SELL',addr(self.spot.tokens['NVDA']['address']),addr(self.spot.tokens['USDG']['address']),
            10**18,atomic(expected,6),atomic(minimum,6),int(time.time()+60),{},[])
        self.spot.price=Mock(return_value=D('10.006'))
        self.spot.quote=Mock(return_value=q)
        return r,q
    def test_near_target_pulls_quote_but_low_minimum_waits(self):
        r,q=self.held();r.execute=Mock()
        r.step()
        self.spot.quote.assert_called_once()
        r.execute.assert_not_called()
        self.assertIsNone(r.s['position']['exit_reason'])
    def test_far_from_target_does_not_pull_quote(self):
        r,q=self.held();self.spot.price.return_value=D('10.00');r.execute=Mock()
        r.step();self.spot.quote.assert_not_called();r.execute.assert_not_called()
    def test_expected_output_above_target_cannot_bypass_signature_gate(self):
        r,q=self.held()
        with self.assertRaises(Blocked):r.execute(q,'target')
        self.spot.signed_body.assert_not_called()
        self.spot.router.submit.assert_not_called()
        self.assertIsNone(r.s['pending'])
    def test_exact_minimum_target_passes(self):
        r,q=self.held(minimum='10.013');r.execute=Mock()
        r.step()
        self.assertEqual(r.execute.call_args.args[1],'target')
    def test_timeout_ignores_target_and_missing_price(self):
        r,q=self.held(minimum='9.9',expected='9.91')
        r.s['position']['opened_epoch']=time.time()-1201
        self.spot.price.side_effect=AssertionError('timeout must not depend on price')
        r.execute=Mock();r.step()
        self.spot.price.assert_not_called()
        self.assertEqual(r.execute.call_args.args[1],'timeout')
    def test_stop_bypasses_target_gate(self):
        r,q=self.held(minimum='9.93',expected='9.95')
        self.spot.price.return_value=D('9.95');r.execute=Mock();r.step()
        self.assertEqual(r.execute.call_args.args[1],'stop')
        self.assertEqual(r.check_exit(q,'stop'),'stop')
    def test_missing_minimum_not_estimated_from_slippage(self):
        del self.row['toSign']['message']['witness']['minBuyAmount']
        with self.assertRaises(Blocked):self.quote()
    def test_missing_expiry_is_not_assumed_alive(self):
        del self.row['expiry']
        with self.assertRaises(Blocked):self.quote()
    def test_exact_40bp_price_probe_blocks_entry(self):
        r=self.runtime()
        r.s.update(date=__import__('arcus').now().date().isoformat(),last_px=None)
        r.calendar={};r.threshold=Mock(return_value=D(30))
        self.spot.measure_round_trip=Mock(return_value={'round_trip_bps':D(40)})
        self.spot.quote=Mock()
        with patch('spot_runtime.session',return_value=True):r.step()
        self.spot.quote.assert_not_called()
    def test_default_dry_run_blocks_live_before_wallet_or_signer(self):
        from spot_runtime import main
        with patch.dict(__import__('os').environ,{'DRY_RUN':'1'}),patch('spot_runtime.Spot') as constructor:
            with self.assertRaises(Blocked):main(['live'])
            constructor.assert_not_called()

if __name__=='__main__':unittest.main()

