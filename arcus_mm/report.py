"""Offline UTC-hour event and fill summaries; never calls an exchange API."""
import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal as D
from pathlib import Path

ALERTS={"ws_disconnected","halt","fatal","account_sync_wait","mutation_recovery_failed",
        "software_cancel_failed","protection_quota_exhausted","protection_retained","clock_unavailable"}

def summarize(path):
    hours={};invalid=0;session=0
    with Path(path).open(encoding="utf-8") as source:
        for line in source:
            try:
                row=json.loads(line)
                stamp=int(row["time_ns"])
                event=row["event"]
                hour=datetime.fromtimestamp(stamp/1e9,timezone.utc).strftime("%Y-%m-%dT%H:00:00Z")
            except (ValueError,KeyError,TypeError,OverflowError):
                invalid+=1;continue
            if event=="session": session+=1
            key=(session,hour)
            h=hours.setdefault(key,dict(session=session,hour_utc=hour,fill_count=0,volume_usd=D(0),
                maker_volume_usd=D(0),fees_usd=D(0),cancel_requests=0,confirmed_paper_cancels=0,
                alerts=Counter(),last_net_pnl_usd=None,last_wear_per_dollar=None))
            if event=="fill":
                try:
                    quantity=D(str(row["quantity"]));price=D(str(row["price"]));fee=D(str(row["fee"]))
                    if not all(n.is_finite() for n in (quantity,price,fee)) or quantity<=0 or price<=0: raise ValueError()
                except (ValueError,KeyError,ArithmeticError): invalid+=1;continue
                h["fill_count"]+=1;h["volume_usd"]+=quantity*price;h["fees_usd"]+=fee
                if row.get("maker") is True or row.get("role")=="MAKER": h["maker_volume_usd"]+=quantity*price
            if event=="cancel_request": h["cancel_requests"]+=1
            if event=="cancel": h["confirmed_paper_cancels"]+=1
            if event in ALERTS: h["alerts"][event]+=1
            if event in ("position","final","ab_summary"):
                try:
                    pnl=D(str(row["realized_pnl"]))-D(str(row["fees"]))+D(str(row["unrealized_pnl"]))
                    wear=None if row.get("wear_per_dollar") is None else D(str(row["wear_per_dollar"]))
                    if not pnl.is_finite() or (wear is not None and not wear.is_finite()): raise ValueError()
                    h["last_net_pnl_usd"]=pnl;h["last_wear_per_dollar"]=wear
                except (ValueError,KeyError,ArithmeticError): invalid+=1
    result=[]
    for _,h in sorted(hours.items()):
        volume=h["volume_usd"];maker=h["maker_volume_usd"]
        h["maker_volume_pct"]=maker/volume*100 if volume else None
        h["cancel_requests_per_1000_maker_usd"]=D(h["cancel_requests"])*1000/maker if maker else None
        result.append(h)
    return dict(source=Path(path).name,invalid_lines_or_records=invalid,hours=result,
        caveat="Fill totals are per UTC hour. Last PnL and wear are cumulative within the recorded session, not hourly PnL. Funding is not included. Live cancel requests are not confirmations. Alerts are logged events, not external notifications.")

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("log")
    p.add_argument("--output")
    a=p.parse_args(argv)
    result=json.dumps(summarize(a.log),default=str,ensure_ascii=False,indent=2)
    if a.output: Path(a.output).write_text(result+"\n",encoding="utf-8")
    else: print(result)
    return 0

if __name__=="__main__": raise SystemExit(main())
