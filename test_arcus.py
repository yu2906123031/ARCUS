import unittest
import time
from datetime import datetime
from decimal import Decimal as D
from arcus import read, validate, Blocked, Estimate, Engine, exit_reason, session, ET

class RiskTests(unittest.TestCase):
    def setUp(self):
        self.c = read('config.json')
    def test_three_exits(self):
        self.assertEqual(exit_reason(D(200),D('200.26'),0,self.c,D(10)),'target')
        self.assertEqual(exit_reason(D(200),D('199.2'),0,self.c,D(10)),'stop')
        self.assertEqual(exit_reason(D(200),D(200),1200,self.c,D(10)),'timeout')
        self.assertIsNone(exit_reason(D(200),D('200.25'),0,self.c,D(10)))
    def test_live_locked(self):
        self.c['dry_run'] = False
        with self.assertRaises(Blocked): validate(self.c)
    def test_missing_calendar_closed(self):
        self.assertFalse(session(self.c,{'sessions':{}},datetime(2026,9,29,10,tzinfo=ET)))
    def test_expiration(self):
        e = object.__new__(Engine)
        with self.assertRaises(Blocked): e.check(Estimate(D(200),D(1),time.time()-1),D(200))
    def test_timeout_ignores_missing_price_and_halt(self):
        class Adapter:
            def price(self,*args): raise AssertionError('timeout must bypass price')
            def quote(self,*args): return Estimate(D(1),D(199),time.time()+5)
        e = object.__new__(Engine)
        e.c,e.a,e.rt,e.day = self.c,Adapter(),D(10),'2026-09-29'
        e.s = {'halt':True,'position':{'usdg_spent':'200','token_amount':'1','opened_epoch':time.time()-1201},
               'pnl':'0','cash':'1800','rounds':0,'bad_exits':0}
        e.save = lambda: None
        logs = []
        e.log = lambda **kw: logs.append(kw)
        e.step()
        self.assertIsNone(e.s['position'])
        self.assertEqual(logs[0]['exit_reason'],'timeout')
        self.assertEqual(e.s['cash'],'1999')



class OptimizationTests(unittest.TestCase):
    def setUp(self):
        self.c = read('config.json')
    def engine(self,adapter=None):
        e=object.__new__(Engine)
        e.c,e.a,e.rt,e.day=self.c,adapter,D(10),datetime.now(ET).date().isoformat()
        e.s={'halt':False,'pending':None,'position':None,'cash':'100','pnl':'0','rounds':0,'failures':0,'bad_exits':0}
        e.save=lambda:None
        e.log=lambda **kw:None
        return e
    def test_current_budget_unlimited_rounds(self):
        from arcus import round_limit_reached
        validate(self.c)
        self.assertEqual(self.c['daily_loss_limit'],'5')
        self.assertFalse(round_limit_reached(self.c,{'rounds':100000}))
        self.assertTrue(round_limit_reached({**self.c,'max_rounds':2},{'rounds':2}))
    def test_nonfinite_settings_rejected(self):
        for key in ('capital','notional','stop_bps','slippage_bps','daily_loss_limit','extra_bps','round_trip_bps'):
            for value in ('NaN','Infinity','-Infinity'):
                with self.subTest(key=key,value=value), self.assertRaises(Blocked):
                    validate({**self.c,key:value})
    def test_nonfinite_estimate_rejected(self):
        e=self.engine()
        for value in (float('nan'),float('inf')):
            with self.assertRaises(Blocked):e.check(Estimate(D(10),D(1),value),D(10))
        with self.assertRaises(Blocked):e.check(Estimate(D(10),D('NaN'),time.time()+10),D(10))
    def test_pending_blocks_quotes(self):
        e=self.engine()
        e.s['pending']='unknown-intent'
        e.cycle()
        self.assertTrue(e.s['halt'])
        self.assertIsNone(e.s['position'])
    def test_reconciliation_requires_explicit_success(self):
        class Adapter:
            def reconcile(self,state):return None
        e=self.engine(Adapter())
        with self.assertRaises(Blocked):e.reconcile()
        self.assertTrue(e.s['halt'])
    def test_idle_does_not_reset_failures(self):
        e=self.engine()
        e.s['failures']=2
        e.step=lambda:None
        e.cycle()
        self.assertEqual(e.s['failures'],2)
        def fail():raise Blocked('no quote')
        e.step=fail
        e.cycle()
        self.assertTrue(e.s['halt'])
        self.assertEqual(e.s['failures'],3)
    def test_timeout_overrides_latched_target(self):
        class Adapter:
            def quote(self,*args):return Estimate(D(1),D('9.99'),time.time()+10)
            def price(self,*args):raise AssertionError('timeout should bypass price')
        e=self.engine(Adapter())
        e.s['cash']='90'
        e.s['position']={'usdg_spent':'10','token_amount':'1','opened_epoch':time.time()-1201,'exit_reason':'target'}
        logs=[]
        e.log=lambda **kw:logs.append(kw)
        e.step()
        self.assertIsNone(e.s['position'])
        self.assertEqual(logs[0]['exit_reason'],'timeout')
    def test_daily_loss_includes_open_position(self):
        class Adapter:
            def price(self,*args):return Estimate(D(1),D('9.97'),time.time()+10)
            def quote(self,*args):return Estimate(D(1),D('9.97'),time.time()+10)
        e=self.engine(Adapter())
        e.s['pnl']='-4.98'
        e.s['cash']='85.02'
        e.s['position']={'usdg_spent':'10','token_amount':'1','opened_epoch':time.time(),'exit_reason':None}
        e.step()
        self.assertTrue(e.s['halt'])
        self.assertEqual(e.s['pnl'],'-5.01')
        self.assertIsNone(e.s['position'])

if __name__ == '__main__': unittest.main()
