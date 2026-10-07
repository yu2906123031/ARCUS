"""One-hour virtual-capital simulation using real Arcus GET quotes only."""
import argparse
import copy
import json
import time
from collections import Counter
from decimal import Decimal as D
from pathlib import Path
from arcus import Blocked, read, write, now
from spot_api import Spot, Router, TransportError, atomic, addr
from spot_runtime import Runtime
import spot_runtime

class ReadOnlyRouter(Router):
    def submit(self, body):
        raise Blocked('paper simulation cannot submit')

class PaperSpot(Spot):
    def signer(self):
        raise Blocked('paper simulation cannot sign')
    def ensure_allowance(self, q):
        raise Blocked('paper simulation cannot approve')
    def snapshot(self):
        return copy.deepcopy(self.virtual_snapshot)
    def fee_policy(self, q):
        # Fees are already represented by Router output; do not double deduct.
        return

class PaperRuntime(Runtime):
    def __init__(self, c, spot, folder):
        self.c=copy.deepcopy(c)
        self.spot=spot
        self.live=False
        self.path=folder/'state.json'
        self.folder=folder
        self.calendar={'sessions':{}}
        self.day=now().date().isoformat()
        self.counts=Counter()
        self.s={'version':1,'wallet':spot.wallet,'contracts':copy.deepcopy(spot.contracts),
            'date':self.day,'config':copy.deepcopy(c),'balances':spot.snapshot(),
            'cash':c['capital'],'position':None,'pending':None,'halt':False,
            'pnl':'0','rounds':0,'failures':0,'bad_exits':0,'last_px':None,
            'last_sell_px':None,'trades':[],'calibration':[],
            'dry_verified':False,'round_trip_bps':c['dry_run_round_trip_bps']}
        self.save()
    def log(self, **fields):
        self.counts[fields.get('status','EVENT')]+=1
        row={'time':now().isoformat(),'mode':'PAPER_REAL_QUOTES',**fields}
        with (self.folder/'events.jsonl').open('a',encoding='utf-8') as f:
            f.write(json.dumps(row,default=str)+'\n')
        print(json.dumps(row,default=str),flush=True)

def summary(runtime, folder, start, end, status, error=None):
    state=runtime.s if runtime else {}
    report={'status':status,'started_at':start,'ended_at':end,
        'capital_usdg':'100','notional_usdg':'50','allow_after_hours':True,
        'fill_model':'validated quote minimum output, fees included; no chain gas deducted',
        'realized_pnl_usdg':state.get('pnl','0'),'cash_usdg':state.get('cash','100'),
        'rounds':state.get('rounds',0),'position':state.get('position'),
        'halt':state.get('halt',False),'events':dict(runtime.counts) if runtime else {},
        'error':error}
    if state.get('position'):
        report['unrealized_pnl_usdg']=None
        report['note']='Open position preserved; no fabricated closing fill.'
    write(folder/'summary.json',report)
    return report

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--wallet',required=True)
    p.add_argument('--duration',type=int,default=3600)
    p.add_argument('--output',required=True)
    args=p.parse_args()
    if args.duration<=0:raise Blocked('positive duration required')
    wallet=addr(args.wallet)
    folder=Path(args.output)
    folder.mkdir(parents=True,exist_ok=False)
    c=read('config.json')
    c.update(dry_run=True,capital='100',notional='50',dry_run_round_trip_bps='39')
    # This standalone process has no live entry point. Override only its entry
    # calendar; the live/dry commands and their configurations stay unchanged.
    spot_runtime.session=lambda config, calendar, stamp: True
    start=now().isoformat()
    deadline=time.monotonic()+args.duration
    runtime=None
    last_heartbeat=0
    init_failures=0
    print(json.dumps({'status':'STARTED','time':start,'wallet':wallet,'duration_sec':args.duration,
        'capital_usdg':'100','notional_usdg':'50','entry_cost_ceiling_bps':'39',
        'allow_after_hours':True}),flush=True)
    write(folder/'summary.json',{'status':'INITIALIZING','started_at':start,
        'capital_usdg':'100','notional_usdg':'50','duration_sec':args.duration})
    try:
        while time.monotonic()<deadline:
            try:
                if runtime is None:
                    spot=PaperSpot(c,router=ReadOnlyRouter(),wallet=wallet)
                    spot.virtual_snapshot={k:0 for k in ('USDG','NVDA','SPY','QQQ','eth','block')}
                    spot.virtual_snapshot.update(USDG=atomic(D('100'),spot.tokens['USDG']['decimals']),
                        eth=atomic(D('1'),18))
                    runtime=PaperRuntime(c,spot,folder)
                if not runtime.s['halt'] or runtime.s['position']:
                    runtime.s['date']=now().date().isoformat()  # Continuous paper session across midnight.
                    runtime.step()
                    runtime.s['failures']=0
                    runtime.save()
            except (Blocked,OSError,ValueError,KeyError,TypeError) as exc:
                if runtime:
                    runtime.s['failures']+=1
                    if runtime.s['failures']>=c['max_failures']:runtime.s['halt']=True
                    runtime.save()
                    runtime.log(status='QUOTE_OR_EXECUTION_FAILED',error=str(exc),
                        failures=runtime.s['failures'])
                else:
                    init_failures+=1
                    print(json.dumps({'status':'INITIALIZATION_FAILED','time':now().isoformat(),
                        'error':str(exc),'attempt':init_failures}),flush=True)
            if time.monotonic()-last_heartbeat>=60:
                report=summary(runtime,folder,start,now().isoformat(),'RUNNING')
                report['remaining_sec']=max(0,int(deadline-time.monotonic()))
                report['initialization_failures']=init_failures
                write(folder/'summary.json',report)
                print(json.dumps({'status':'HEARTBEAT',**report},default=str),flush=True)
                last_heartbeat=time.monotonic()
            time.sleep(min(c['poll_sec'] if runtime else 15,max(0,deadline-time.monotonic())))
    finally:
        report=summary(runtime,folder,start,now().isoformat(),'COMPLETED')
        report['initialization_failures']=init_failures
        write(folder/'summary.json',report)
        print(json.dumps(report,default=str),flush=True)

if __name__=='__main__':
    main()

