import unittest
from decimal import Decimal as D
from unittest.mock import patch
from arcus import Blocked, read
from perps_runtime import normalize, vwap, net_exit, quantity, validate, rate

class PerpsTests(unittest.TestCase):
    def setUp(self): self.c=read('perps_config.json')
    def test_costs_can_turn_gross_profit_into_loss(self):
        p={'side':'LONG','entry_px':'100','quantity':'2','entry_fee':'0.1'}
        self.assertEqual(net_exit(p,D('100.05'),D('0.0005'),D('0.01')),D('-0.110050'))
    def test_short_sign_and_both_fees(self):
        p={'side':'SHORT','entry_px':'100','quantity':'2','entry_fee':'0.1'}
        self.assertEqual(net_exit(p,D('99'),D('0.0005'),D('0.01')),D('1.791'))
    def test_depth_not_top_of_book_fill(self):
        self.assertEqual(vwap([(D(100),D(1)),(D(110),D(2))],D(2)),D(105))
        with self.assertRaises(Blocked): vwap([(D(100),D(1))],D(2))
    def test_stale_crossed_and_unsorted_books_block(self):
        good={'bids':[['99','1']],'asks':[['100','1']],'timestamp':100000000}
        self.assertIn('bids',normalize(good,10,epoch=100))
        with self.assertRaises(Blocked): normalize(good,10,epoch=111)
        with self.assertRaises(Blocked): normalize({**good,'bids':[['101','1']]},10,epoch=100)
        with self.assertRaises(Blocked): normalize({**good,'asks':[['101','1'],['100','1']]},10,epoch=100)
    def test_size_reserves_capital_and_scales_down(self):
        m={'status':'ONLINE','type':'PERPETUAL','stepSize':'0.001','minOrderSize':'0.001',
           'minOrderNotional':'5','maxOrderSize':'100','initialMarginFraction':'0.025','maintenanceMarginFraction':'0.016667'}
        self.assertEqual(quantity(m,self.c,D(100)),D('2.85'))
        self.assertEqual(quantity(m,self.c,D(100),D(95)),D('2.7'))
        with self.assertRaises(Blocked): quantity(m,self.c,D(100),D(5))
    def test_missing_fees_and_excess_leverage_block(self):
        with self.assertRaises(Blocked): rate({'tiers':[]})
        with self.assertRaises(Blocked): validate({**self.c,'leverage':'40'})
    def test_live_never_submits(self):
        from perps_runtime import main
        with patch('perps_runtime.get') as call:
            with self.assertRaises(Blocked): main(['live'])
            call.assert_not_called()

if __name__=='__main__': unittest.main()

class PaperLifecycleTests(unittest.TestCase):
    def test_profitable_exit_is_net_of_all_costs_and_volume_is_simulated(self):
        import os, tempfile
        from perps_runtime import Paper
        c=read('perps_config.json')
        m={'marketDisplayName':'BTC-USD','status':'ONLINE','type':'PERPETUAL','stepSize':'0.001',
           'minOrderSize':'0.001','minOrderNotional':'5','maxOrderSize':'100',
           'initialMarginFraction':'0.025','maintenanceMarginFraction':'0.016667',
           'fundingRate':'0.0000125','nextFundingRate':'0.0000125','volume24hNotional':'1000','trades24h':10}
        first={'bids':[(D('100'),D(100))],'asks':[(D('100.001'),D(100))]}
        last={'bids':[(D('100.2'),D(100))],'asks':[(D('100.201'),D(100))]}
        cwd=os.getcwd()
        with tempfile.TemporaryDirectory() as temp:
            try:
                os.chdir(temp)
                runtime=Paper(c)
                runtime.history.extend([D('99.9')]*c['signal_samples'])
                with patch('perps_runtime.market_data',return_value={'BTC-USD':m}), patch('perps_runtime.get',return_value={'tiers':[{'level':0,'taker_fee_ppm':225}]}), patch('perps_runtime.snapshot',side_effect=[first,last]):
                    runtime.cycle()
                    self.assertIsNotNone(runtime.s['position'])
                    runtime.cycle()
                self.assertIsNone(runtime.s['position'])
                self.assertEqual(runtime.s['trades'][0]['reason'],'target')
                self.assertGreater(D(runtime.s['trades'][0]['net_pnl']),D('0.0285'))
                self.assertEqual(runtime.s['real_volume_usd'],'0')
                self.assertGreater(D(runtime.s['simulated_volume_usd']),D(560))
            finally: os.chdir(cwd)
