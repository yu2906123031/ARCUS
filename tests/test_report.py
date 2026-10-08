import json
import tempfile
import unittest
from pathlib import Path
from decimal import Decimal as D
from arcus_mm.report import summarize

class ReportTests(unittest.TestCase):
    def test_hour_boundaries_fees_roles_alerts_and_partial_line(self):
        rows=[dict(time_ns=0,event="session"),dict(time_ns=1,event="fill",quantity="2",price="10",fee="-.1",role="MAKER",private_key="must not appear"),
              dict(time_ns=2,event="cancel_request",reason="quote_changed",order_lifetime_seconds="2"),
              dict(time_ns=3,event="cancel_request",reason="shutdown",order_lifetime_seconds="10"),dict(time_ns=4,event="ws_disconnected"),
              dict(time_ns=3600*10**9,event="fill",quantity="1",price="11",fee=".2",maker=False),
              dict(time_ns=3600*10**9+1,event="position",realized_pnl="1",fees=".1",unrealized_pnl="-.2",wear_per_dollar=".01",funding_cumulative_usd="-.03")]
        with tempfile.TemporaryDirectory() as root:
            p=Path(root,"log.jsonl");p.write_text("\n".join(json.dumps(r) for r in rows)+"\n{",encoding="utf-8")
            r=summarize(p)
        self.assertEqual(r["invalid_lines_or_records"],1)
        first,second=r["hours"]
        self.assertEqual(first["volume_usd"],D(20));self.assertEqual(first["fees_usd"],D("-.1"))
        self.assertEqual(first["maker_volume_pct"],100)
        self.assertEqual(first["cancel_requests_per_1000_maker_usd"],100)
        self.assertEqual(first["cancel_reasons"],{"quote_changed":1,"shutdown":1})
        self.assertEqual(first["order_lifetime_samples"],2)
        self.assertEqual(first["order_lifetime_p50_seconds"],D(6));self.assertEqual(first["order_lifetime_p90_seconds"],D("9.2"))
        self.assertEqual(first["alerts"]["ws_disconnected"],1)
        self.assertEqual(second["last_net_pnl_usd"],D(".7"))
        self.assertEqual(second["last_wear_per_dollar"],D(".01"))
        self.assertEqual(second["last_funding_cumulative_usd"],D("-.03"))
        self.assertIsNone(second["cancel_requests_per_1000_maker_usd"])
        self.assertNotIn("must not appear",json.dumps(r,default=str))

    def test_markouts_are_weighted_separated_and_missing_excluded(self):
        common=dict(time_ns=1,event="markout",side="BUY",horizon_seconds=5,spread_bucket="1-2",sample_lag_seconds=".2")
        rows=[dict(common,notional_usd="10",markout_bps="2",mid_move_bps="-1",net_markout_bps="1"),
              dict(common,notional_usd="30",markout_bps="6",mid_move_bps="-3",net_markout_bps="5"),
              dict(common,side="SELL",notional_usd="20",markout_bps="3",mid_move_bps="2",net_markout_bps="3"),
              dict(common,notional_usd="1",markout_bps="NaN",mid_move_bps="0",net_markout_bps="0"),
              dict(time_ns=2,event="markout_missing"),dict(time_ns=3,event="markout_skipped")]
        with tempfile.TemporaryDirectory() as root:
            p=Path(root,"log.jsonl");p.write_text("\n".join(json.dumps(r) for r in rows),encoding="utf-8")
            result=summarize(p)
        hour=result["hours"][0];buy,sell=hour["markouts"]
        self.assertEqual(result["invalid_lines_or_records"],1)
        self.assertEqual(buy["samples"],2);self.assertEqual(buy["mean_markout_bps"],D(5))
        self.assertEqual(buy["mean_mid_move_bps"],D("-2.5"));self.assertEqual(buy["mean_net_markout_bps"],D(4))
        self.assertEqual(sell["samples"],1);self.assertEqual(hour["markout_missing"],1)
        self.assertEqual(hour["markout_skipped"],1)

    def test_malformed_event_and_negative_delay_are_rejected(self):
        rows=[dict(time_ns=1,event=["fill"]),dict(time_ns=1,event={"bad":1}),dict(time_ns=1,event="markout",side="BUY",horizon_seconds=5,spread_bucket="1-2",notional_usd="10",markout_bps="0",mid_move_bps="0",net_markout_bps="0",sample_lag_seconds="-1")]
        with tempfile.TemporaryDirectory() as root:
            p=Path(root,"log.jsonl");p.write_text("\n".join(json.dumps(r) for r in rows),encoding="utf-8")
            result=summarize(p)
        self.assertEqual(result["invalid_lines_or_records"],3)
        self.assertTrue(all(not hour["markouts"] for hour in result["hours"]))
