"""Offline UTC-hour event and fill summaries; never calls an exchange API."""
import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal as D
from pathlib import Path

ALERTS={"execution_error","cleanup_error","ws_disconnected","halt","fatal","account_sync_wait","mutation_recovery_failed",
        "software_cancel_failed","protection_quota_exhausted","protection_retained","clock_unavailable"}

def percentile(values,p):
    if not values:return None
    ordered=sorted(values);rank=(len(ordered)-1)*p;low=int(rank);high=min(low+1,len(ordered)-1)
    return ordered[low]+(ordered[high]-ordered[low])*(rank-low)

def summarize(path):
    hours={};invalid=0;session=0
    with Path(path).open(encoding="utf-8") as source:
        for line in source:
            try:
                row=json.loads(line)
                stamp=int(row["time_ns"])
                event=row["event"]
                if not isinstance(event,str):raise ValueError()
                hour=datetime.fromtimestamp(stamp/1e9,timezone.utc).strftime("%Y-%m-%dT%H:00:00Z")
            except (ValueError,KeyError,TypeError,OverflowError):
                invalid+=1;continue
            if event=="session": session+=1
            key=(session,hour)
            h=hours.setdefault(key,dict(session=session,hour_utc=hour,fill_count=0,volume_usd=D(0),
                maker_volume_usd=D(0),fees_usd=D(0),cancel_requests=0,confirmed_paper_cancels=0,
                cancel_reasons=Counter(),order_lifetimes=[],alerts=Counter(),last_net_pnl_usd=None,
                last_wear_per_dollar=None,last_funding_cumulative_usd=None,markout_stats={},markout_missing=0,markout_skipped=0))
            if event=="fill":
                try:
                    quantity=D(str(row["quantity"]));price=D(str(row["price"]));fee=D(str(row["fee"]))
                    if not all(n.is_finite() for n in (quantity,price,fee)) or quantity<=0 or price<=0: raise ValueError()
                except (ValueError,KeyError,ArithmeticError): invalid+=1;continue
                h["fill_count"]+=1;h["volume_usd"]+=quantity*price;h["fees_usd"]+=fee
                if row.get("maker") is True or row.get("role")=="MAKER": h["maker_volume_usd"]+=quantity*price
            if event=="markout":
                try:
                    side=row["side"];horizon=int(row["horizon_seconds"]);bucket=str(row["spread_bucket"])
                    notional=D(str(row["notional_usd"]))
                    values={name:D(str(row[name])) for name in ("markout_bps","mid_move_bps","net_markout_bps","sample_lag_seconds")}
                    if side not in ("BUY","SELL") or horizon not in (1,3,5,10,30) or not notional.is_finite() or notional<=0 or not all(n.is_finite() for n in values.values()) or values["sample_lag_seconds"]<0:raise ValueError()
                except (KeyError,ValueError,TypeError,ArithmeticError):invalid+=1;continue
                group=h["markout_stats"].setdefault((side,horizon,bucket),dict(side=side,horizon_seconds=horizon,spread_bucket=bucket,samples=0,notional_usd=D(0),sums={name:D(0) for name in values}))
                group["samples"]+=1;group["notional_usd"]+=notional
                for name,value in values.items():group["sums"][name]+=value*notional
            if event=="markout_missing":h["markout_missing"]+=1
            if event=="markout_skipped":h["markout_skipped"]+=1
            if event=="cancel_request":
                h["cancel_requests"]+=1;h["cancel_reasons"][str(row.get("reason","unspecified"))]+=1
                try:
                    lifetime=D(str(row["order_lifetime_seconds"]))
                    if not lifetime.is_finite() or lifetime<0:raise ValueError()
                    h["order_lifetimes"].append(lifetime)
                except (ValueError,KeyError,ArithmeticError):invalid+=1
            if event=="cancel": h["confirmed_paper_cancels"]+=1
            if event in ALERTS: h["alerts"][event]+=1
            if event in ("position","final","ab_summary"):
                try:
                    pnl=D(str(row["realized_pnl"]))-D(str(row["fees"]))+D(str(row["unrealized_pnl"]))
                    wear=None if row.get("wear_per_dollar") is None else D(str(row["wear_per_dollar"]))
                    funding=None if row.get("funding_cumulative_usd") is None else D(str(row["funding_cumulative_usd"]))
                    if not pnl.is_finite() or (wear is not None and not wear.is_finite()) or (funding is not None and not funding.is_finite()): raise ValueError()
                    h["last_net_pnl_usd"]=pnl;h["last_wear_per_dollar"]=wear;h["last_funding_cumulative_usd"]=funding
                except (ValueError,KeyError,ArithmeticError): invalid+=1
    result=[]
    for _,h in sorted(hours.items()):
        volume=h["volume_usd"];maker=h["maker_volume_usd"]
        h["maker_volume_pct"]=maker/volume*100 if volume else None
        h["cancel_requests_per_1000_maker_usd"]=D(h["cancel_requests"])*1000/maker if maker else None
        h["cancel_reasons"]=dict(sorted(h["cancel_reasons"].items()))
        h["order_lifetime_samples"]=len(h["order_lifetimes"])
        h["order_lifetime_p50_seconds"]=percentile(h["order_lifetimes"],D(".5"))
        h["order_lifetime_p90_seconds"]=percentile(h["order_lifetimes"],D(".9"))
        h.pop("order_lifetimes")
        h["markouts"]=[]
        for _,group in sorted(h.pop("markout_stats").items()):
            sums=group.pop("sums")
            group.update({"mean_"+name:value/group["notional_usd"] for name,value in sums.items()})
            h["markouts"].append(group)
        result.append(h)
    return dict(source=Path(path).name,invalid_lines_or_records=invalid,hours=result,
        caveat="Fill totals are per UTC hour. Last PnL, funding and wear are cumulative snapshots within the recorded session. Live cancel requests are not confirmations. Alerts are logged events, not external notifications. Markouts are not realized PnL; horizons overlap and must not be summed. Missing samples are excluded, not zero.")

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
