import unittest
from dataclasses import replace
from decimal import Decimal as D
from arcus_mm.config import Config, load
from arcus_mm.strategy import QuotePolicy, targets, safe_resting, fair_value, inventory_shift
from arcus_mm.models import Order
from tests.test_mm import market, book

class QuotePolicyTests(unittest.TestCase):
    def test_inventory_moves_both_quotes_towards_reduction(self):
        c=replace(Config(),inventory_skew_bps="2",order_equity_fraction="0.2")
        neutral=targets(c,market(),book(),D(100),D(0))
        for position,direction in [(D("0.0002"),-1),(D("-0.0002"),1)]:
            quotes=targets(c,market(),book(),D(100),position)
            self.assertEqual(len(quotes),2)
            for old,new in zip(neutral,quotes): self.assertGreater((new.price-old.price)*direction,0)
            self.assertTrue(safe_resting(c,D(100),position,book(),[Order(q,"test",q.qty,0) for q in quotes]))

    def test_high_volatility_widens_and_removes_grid_outer_level(self):
        c=replace(Config(),adaptive_spread=True,strategy="grid")
        policy=QuotePolicy()
        for i in range(6):
            b=book(str(99999+i*100),str(100001+i*100));b.received=float(i)
            effective=policy.effective(c,b)
        self.assertEqual(D(effective.spread_bps),D(8))
        self.assertEqual(D(effective.reprice_bps),D(2))
        self.assertEqual(effective.strategy,"mid")
        b.received=40
        effective=policy.effective(c,b)
        self.assertEqual(D(effective.spread_bps),D(2))
        self.assertEqual(effective.strategy,"grid")
        self.assertEqual(policy.state["samples"],1)

    def test_wide_book_and_fee_floor(self):
        c=replace(Config(),adaptive_spread=True,exit_fee_reserve_fraction="1")
        policy=QuotePolicy();effective=policy.effective(c,book("99900","100100"))
        self.assertEqual(D(effective.spread_bps),D(8))
        effective=policy.effective(c,book(),D("0.0001"),D("0.000225"))
        self.assertEqual(D(effective.spread_bps),D("3.25"))
        effective=QuotePolicy().effective(c,book(),D("-0.0001"),D(0))
        self.assertEqual(D(effective.spread_bps),D(2))

    def test_aggressive_skew_keeps_passive_reducing_side(self):
        c=replace(Config(),spread_bps="1.2",inventory_skew_bps="2",order_equity_fraction=".2")
        for position,side in [(D(".0004"),"SELL"),(D("-.0004"),"BUY")]:
            qs=targets(c,market(),book(),D(100),position)
            reducing=[q for q in qs if q.side==side]
            self.assertTrue(reducing)
            self.assertTrue(all(q.price>book().bid if side=="SELL" else q.price<book().ask for q in reducing))
            self.assertTrue(safe_resting(c,D(100),position,book(),[Order(q,"test",q.qty,0) for q in qs]))

    def test_microprice_direction_bound_and_zero_weight_baseline(self):
        c=replace(Config(),microprice_weight="1",max_fair_shift_bps="1")
        b=replace(book("99900","100100"),bid_size=D(9),ask_size=D(1))
        f=fair_value(c,b)
        self.assertEqual(f["microprice"],D(100080))
        self.assertEqual(f["l1_imbalance"],D(".8"))
        self.assertEqual(f["fair_shift_bps"],D(1))
        self.assertEqual(fair_value(replace(c,microprice_weight="0"),b)["fair_price"],b.mid)
        reverse=fair_value(c,replace(b,bid_size=D(1),ask_size=D(9)))
        self.assertEqual(reverse["fair_shift_bps"],D(-1))

    def test_cubic_skew_is_small_near_zero_and_stronger_near_cap(self):
        c=replace(Config(),inventory_skew_bps="2",inventory_cubic_bps="1")
        self.assertEqual(inventory_shift(c,D(".1")),D("-.201"))
        self.assertEqual(inventory_shift(c,D(1)),D(-3))
        self.assertEqual(inventory_shift(c,D(-1)),D(3))
        self.assertEqual(inventory_shift(c,D(2)),D(-3))

    def test_duplicate_frames_do_not_build_volatility_history(self):
        policy=QuotePolicy();b=book();b.received=1
        for _ in range(20): policy.effective(Config(),b)
        self.assertEqual(len(policy.samples),1)

    def test_invalid_controls_and_live_leverage(self):
        for name,value in [("inventory_skew_bps","4"),("max_spread_bps","1"),("exit_fee_reserve_fraction","2"),("adaptive_spread",1),("volatility_multiplier","NaN")]:
            with self.subTest(name=name),self.assertRaises(ValueError): replace(Config(),**{name:value}).validate()
        self.assertEqual(load("mm_live_btc_100.json").leverage_cap,2)

    def test_half_cap_inventory_uses_only_passive_reduce_quote_at_touch(self):
        c=replace(Config(),max_position_equity_fraction=".15",order_notional_min="40",order_notional_max="80")
        b=book()
        cap=D(500)*D(".15")/b.mid
        for position,side in ((cap*D(".5"),"SELL"),(-cap*D(".5"),"BUY")):
            qs=targets(c,market(),b,D(500),position)
            self.assertEqual(len(qs),1)
            self.assertEqual(qs[0].side,side)
            self.assertTrue(qs[0].reduce)
            self.assertLess(qs[0].qty,abs(position)+market().step)
            self.assertLess(qs[0].price,b.ask)
            self.assertGreater(qs[0].price,b.bid)

if __name__=="__main__": unittest.main()
