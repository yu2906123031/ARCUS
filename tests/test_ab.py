import tempfile
import unittest
from dataclasses import replace
from decimal import Decimal as D
from types import SimpleNamespace
from unittest.mock import patch
from arcus_mm.ab import Measurements, make_pair
from arcus_mm.config import Config
from tests.test_mm import market, book

class ABTests(unittest.IsolatedAsyncioTestCase):
    async def test_shared_feed_independent_ledgers_and_no_signer(self):
        api=SimpleNamespace()
        with tempfile.TemporaryDirectory() as root,patch("arcus_mm.api.Signer",side_effect=AssertionError("no credentials")):
            engines=make_pair(replace(Config(),paper=False,inventory_skew_bps="2"),api,market(),D(0),D(".000225"),root)
            self.assertTrue(all(not e.live and e.c.paper for e in engines))
            self.assertEqual([e.c.inventory_skew_bps for e in engines],["0","2"])
            for e in engines:
                from arcus_mm.models import Quote
                await e.venue.place(Quote("BUY-1","BUY",D(100000),D(".0001")),book())
                for o in e.venue.orders.values(): o.created-=1
            b=book("99997","99998")
            api.on_trades([dict(tradeId="a",price="99997",size=".0001",timestamp=b.timestamp_us)],b)
            self.assertEqual([e.ledger.qty for e in engines],[D(".0001"),D(".0001")])
            engines[0].ledger.fill("SELL",".0001","100000",0)
            self.assertEqual(engines[1].ledger.qty,D(".0001"))

    async def test_variants_share_frozen_frame_and_single_clock_refresh(self):
        from unittest.mock import AsyncMock
        from arcus_mm.ab import FeedView
        import asyncio
        import time
        original=book()
        source=SimpleNamespace(book=original,c=Config(),fresh=lambda:True,clock_pending=False,clock_checked=time.monotonic(),clock=AsyncMock())
        lock=asyncio.Lock();a=FeedView(source,lock);b=FeedView(source,lock)
        a.frame=b.frame=original
        source.book=book("100010","100012")
        self.assertIs(a.book,b.book)
        self.assertIsNot(a.book,source.book)
        self.assertTrue(a.fresh())
        await asyncio.gather(a.clock(),b.clock())
        source.clock.assert_not_awaited()
        a.frame=None
        self.assertIs(a.book,source.book)

    def test_half_life_and_censoring(self):
        now=[0];m=Measurements(lambda:now[0])
        m.observe("fill",dict(position="1"))
        now[0]=3;m.observe("fill",dict(position="2"))
        now[0]=8;m.observe("fill",dict(position="1"))
        self.assertEqual(m.half_times,[5]);self.assertEqual(m.censored,1)
        m.observe("cancel",{});self.assertEqual(m.cancels,1)

    def test_exit_fills_do_not_improve_inventory_half_life(self):
        m=Measurements(lambda:0)
        m.observe("fill",dict(position="1",maker=True))
        m.observe("fill",dict(position="0",maker=False))
        self.assertEqual(m.completed,0)
        self.assertEqual(m.censored,1)

    def test_no_fills_metrics_are_unavailable(self):
        from arcus_mm.models import Ledger
        r=Measurements().report(Ledger(100),book(),D(0))
        self.assertIsNone(r["inventory_half_life_seconds"])
        self.assertIsNone(r["cancels_per_1000_maker_usd"])
