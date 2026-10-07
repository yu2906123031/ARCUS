import json
import tempfile
import unittest
from pathlib import Path
from decimal import Decimal as D
from arcus_mm.report import summarize

class ReportTests(unittest.TestCase):
    def test_hour_boundaries_fees_roles_alerts_and_partial_line(self):
        rows=[dict(time_ns=0,event="session"),dict(time_ns=1,event="fill",quantity="2",price="10",fee="-.1",role="MAKER",private_key="must not appear"),
              dict(time_ns=2,event="cancel_request"),dict(time_ns=3,event="ws_disconnected"),
              dict(time_ns=3600*10**9,event="fill",quantity="1",price="11",fee=".2",maker=False),
              dict(time_ns=3600*10**9+1,event="position",realized_pnl="1",fees=".1",unrealized_pnl="-.2",wear_per_dollar=".01")]
        with tempfile.TemporaryDirectory() as root:
            p=Path(root,"log.jsonl");p.write_text("\n".join(json.dumps(r) for r in rows)+"\n{",encoding="utf-8")
            r=summarize(p)
        self.assertEqual(r["invalid_lines_or_records"],1)
        first,second=r["hours"]
        self.assertEqual(first["volume_usd"],D(20));self.assertEqual(first["fees_usd"],D("-.1"))
        self.assertEqual(first["maker_volume_pct"],100)
        self.assertEqual(first["cancel_requests_per_1000_maker_usd"],50)
        self.assertEqual(first["alerts"]["ws_disconnected"],1)
        self.assertEqual(second["last_net_pnl_usd"],D(".7"))
        self.assertIsNone(second["cancel_requests_per_1000_maker_usd"])
        self.assertNotIn("must not appear",json.dumps(r,default=str))
