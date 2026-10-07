import copy
import os
import tempfile
import unittest
from pathlib import Path
from datetime import datetime
from unittest.mock import Mock,patch
from arcus import read,write,Blocked,ReconcileError,ET
from spot_ops import new_day,doctor

ROOT=Path(__file__).resolve().parent

class DailyOperationsTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.cwd=Path.cwd();os.chdir(self.tmp.name)
        self.c=read(ROOT/'config.json')
        self.spot=Mock();self.spot.wallet='0x'+'1'*40;self.spot.contracts={'permit2':'pinned'}
        self.balances={'USDG':95000000,'NVDA':0,'SPY':0,'QQQ':0,'eth':10**18,'block':100}
        self.spot.snapshot.return_value=copy.deepcopy(self.balances)
        self.old={'date':'2026-09-28','wallet':self.spot.wallet,'contracts':self.spot.contracts,
            'config':self.c,'position':None,'pending':None,'balances':self.balances,
            'cash':'95','pnl':'-5','halt':True,'trades':[{'txid':'kept-in-archive'}]}
        write('live_state.json',self.old)
        self.clock=patch('spot_ops.now',return_value=datetime(2026,9,29,10,tzinfo=ET));self.clock.start()
    def tearDown(self):
        self.clock.stop();os.chdir(self.cwd);self.tmp.cleanup()
    def test_new_day_keeps_remaining_capital(self):
        result=new_day(self.c,self.spot)
        state=read('live_state.json')
        self.assertEqual(state['cash'],'95')
        self.assertEqual(state['pnl'],'0')
        self.assertEqual(state['lifetime_pnl'],'-5')
        self.assertIsNone(state['round_trip_bps'])
        self.assertEqual(state['calibration'],[])
        self.assertEqual(read(result['archive']),self.old)
    def test_same_day_cannot_reset_loss_limit(self):
        self.old['date']='2026-09-29';write('live_state.json',self.old)
        with self.assertRaises(Blocked):new_day(self.c,self.spot)
        self.assertEqual(read('live_state.json')['pnl'],'-5')
    def test_position_blocks_new_day(self):
        self.old['position']={'token_amount':'1'};write('live_state.json',self.old)
        with self.assertRaises(ReconcileError):new_day(self.c,self.spot)
    def test_pending_blocks_new_day(self):
        self.old['pending']={'txHash':None};write('live_state.json',self.old)
        with self.assertRaises(ReconcileError):new_day(self.c,self.spot)
    def test_unknown_wallet_delta_blocks_new_day(self):
        self.spot.snapshot.return_value['USDG']=96000000
        with self.assertRaises(ReconcileError):new_day(self.c,self.spot)
        self.assertEqual(read('live_state.json'),self.old)
    def test_approval_pending_blocks_new_day(self):
        write('approval_pending.json',{'txHash':'unresolved'})
        with self.assertRaises(ReconcileError):new_day(self.c,self.spot)
    def test_doctor_never_displays_secret(self):
        with patch.dict(os.environ,{'ARCUS_PRIVATE_KEY':'secret-do-not-print','ARCUS_MNEMONIC':''}):
            report=doctor(self.c)
        self.assertTrue(report['checks']['one_local_signer'])
        self.assertNotIn('secret-do-not-print',str(report))
        self.spot.snapshot.assert_not_called()

if __name__=='__main__':unittest.main()
