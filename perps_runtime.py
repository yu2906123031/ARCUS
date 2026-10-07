"""Arcus perpetual public data and conservative paper execution. No POSTs."""
import argparse
import json
import time
from collections import deque
from decimal import Decimal as D, ROUND_DOWN
from pathlib import Path
from urllib.request import Request, urlopen
from arcus import Blocked, decimal, read, write
BASE = 'https://api.arcus.xyz'
BPS = D(10000)

def get(path):
    try:
        with urlopen(Request(BASE + path, headers={'Accept':'application/json'}), timeout=10) as r:
            return json.load(r)
    except Exception:
        raise Blocked('perps GET failed; no fill assumed') from None

def validate(c):
    if c['product'] != 'perpetual' or c['dry_run'] is not True:
        raise Blocked('only verified paper execution is available')
    if c['symbol'] not in ('BTC-USD','ETH-USD'):
        raise Blocked('unsupported market')
    for k in ('capital','leverage','notional','daily_loss_limit','stop_loss_usd','net_profit_buffer_bps',
              'slippage_per_side_bps','max_entry_cost_bps','momentum_bps'):
        decimal(c[k],k,positive=True)
    if D(c['leverage']) > 3 or D(c['notional']) > (D(c['capital'])-D(c['daily_loss_limit']))*D(c['leverage']):
        raise Blocked('exposure exceeds 3x or reserved loss budget')
    if not D(c['stop_loss_usd']) < D(c['daily_loss_limit']) < D(c['capital']):
        raise Blocked('invalid loss budget')
    for k in ('signal_samples','poll_sec','max_book_age_sec','max_hold_sec','max_bad_exits'):
        if type(c[k]) is not int or c[k] <= 0: raise Blocked('invalid '+k)
    if c['signal_samples'] < 2: raise Blocked('signal needs two samples')
    if c['max_rounds'] is not None and (type(c['max_rounds']) is not int or c['max_rounds'] <= 0):
        raise Blocked('invalid round limit')

def normalize(raw, max_age, epoch=None):
    epoch = D(str(time.time() if epoch is None else epoch))
    age = epoch-decimal(raw['timestamp'])/D(1000000)
    if age < -2 or age > max_age: raise Blocked('stale/future book')
    b={}
    for side in ('bids','asks'):
        rows=[(decimal(p,positive=True),decimal(q,positive=True)) for p,q in raw[side]]
        if not rows or rows != sorted(rows,reverse=side=='bids'): raise Blocked('empty/unsorted book')
        b[side]=rows
    if b['bids'][0][0] >= b['asks'][0][0]: raise Blocked('crossed book')
    return b

def vwap(levels,qty):
    if qty <= 0: raise Blocked('invalid quantity')
    left,total=qty,D(0)
    for px,size in levels:
        take=min(left,size); total+=take*px; left-=take
        if left==0: return total/qty
    raise Blocked('insufficient depth')

def rate(raw):
    rows=[r for r in raw['tiers'] if r['level']==0]
    if len(rows)!=1: raise Blocked('missing fee tier')
    r=decimal(rows[0]['taker_fee_ppm'])/D(1000000)
    if not 0<=r<D('0.01'): raise Blocked('invalid fee')
    return r

def quantity(m,c,px,equity=None):
    if m['status']!='ONLINE' or m['type']!='PERPETUAL': raise Blocked('market unavailable')
    equity=D(c['capital']) if equity is None else equity
    limit=min(D(c['notional']),max(D(0),equity-D(c['daily_loss_limit']))*D(c['leverage']))
    step=decimal(m['stepSize'],positive=True)
    q=(limit/px/step).to_integral_value(rounding=ROUND_DOWN)*step
    if q<D(m['minOrderSize']) or q*px<D(m['minOrderNotional']): raise Blocked('below minimum order')
    if q>D(m['maxOrderSize']): raise Blocked('above maximum order')
    imf=decimal(m['initialMarginFraction'],positive=True)
    mmf=decimal(m['maintenanceMarginFraction'],positive=True)
    if q*px*imf+D(c['daily_loss_limit'])>equity or equity-q*px*mmf<=D(c['daily_loss_limit']):
        raise Blocked('insufficient margin cushion')
    return q

def execution(b,q,side,c):
    slip=D(c['slippage_per_side_bps'])/BPS
    return vwap(b['asks'] if side=='BUY' else b['bids'],q)*(1+slip if side=='BUY' else 1-slip)

def analyze(m,b,r,c):
    mid=(b['bids'][0][0]+b['asks'][0][0])/2
    q=quantity(m,c,b['asks'][0][0]*(1+D(c['slippage_per_side_bps'])/BPS))
    buy,sell=execution(b,q,'BUY',c),execution(b,q,'SELL',c)
    loss=q*(buy-sell+(buy+sell)*r)
    cost=loss/(q*buy)*BPS
    funding=max(abs(D(m['nextFundingRate'])),abs(D(m['fundingRate'])))*BPS
    return {'symbol':m['marketDisplayName'],'quantity':str(q),'notional':str(q*buy),
            'mid':str(mid),'spread_bps':str((b['asks'][0][0]-b['bids'][0][0])/mid*BPS),
            'taker_round_trip_fee_bps':str(2*r*BPS),'round_trip_cost_bps':str(cost),
            'immediate_round_trip_loss_usd':str(loss),
            'target_move_bps_with_funding_reserve':str(cost+funding+D(c['net_profit_buffer_bps'])),
            'volume24h_usd':m['volume24hNotional'],'trades24h':m['trades24h'],
            'eligible':cost<=D(c['max_entry_cost_bps'])}

def market_data():
    return {m['marketDisplayName']:m for m in get('/v1/markets')['markets']}

def snapshot(symbol,c):
    return normalize(get('/v1/l2OrderBook/'+symbol+'?nLevels=100'),c['max_book_age_sec'])

def compare(c):
    ms,r=market_data(),rate(get('/v1/feetiers'))
    rows=[analyze(ms[s],snapshot(s,c),r,c) for s in ('BTC-USD','ETH-USD')]
    eligible=[x for x in rows if x['eligible']]
    best=None
    if eligible:
        cheapest=min(D(x['round_trip_cost_bps']) for x in eligible)
        best=max((x for x in eligible if D(x['round_trip_cost_bps'])<=cheapest+D('0.5')),
                 key=lambda x:D(x['volume24h_usd']))['symbol']
    out={'mode':'READ_ONLY','captured_epoch':time.time(),'recommended':best,'markets':rows}
    write('research/perps_comparison.json',out)
    print(json.dumps(out,indent=2))
    return out

def net_exit(pos,out,r,funding):
    sign=1 if pos['side']=='LONG' else -1
    return sign*(out-D(pos['entry_px']))*D(pos['quantity'])-D(pos['entry_fee'])-out*D(pos['quantity'])*r-funding

class Paper:
    def __init__(self,c):
        self.c=c; self.history=deque(maxlen=c['signal_samples'])
        self.path=Path('perps_paper_state.json')
        self.s=read(self.path) if self.path.exists() else {
            'mode':'PAPER','config':c,'equity':c['capital'],'position':None,'rounds':0,
            'gross_losses':'0','bad_exits':0,'halt':None,'simulated_volume_usd':'0',
            'real_volume_usd':'0','trades':[]}
        if self.s['config']!=c: raise Blocked('archive paper state before changing config')
        write(self.path,self.s)
    def cycle(self):
        c,s=self.c,self.s
        m=market_data()[c['symbol']]; r=rate(get('/v1/feetiers')); b=snapshot(c['symbol'],c)
        mid=(b['bids'][0][0]+b['asks'][0][0])/2; self.history.append(mid)
        pos=s['position']
        if pos:
            q,entry=D(pos['quantity']),D(pos['entry_px'])
            out=execution(b,q,'SELL' if pos['side']=='LONG' else 'BUY',c)
            fr=max(D(pos['funding_rate']),abs(D(m['nextFundingRate'])),abs(D(m['fundingRate'])))
            pos['funding_rate']=str(fr)
            # Conservative paper reserve: one extra hourly interval. Not actual funding.
            intervals=int((time.time()-pos['opened_epoch'])//3600)+1
            funding=max(entry,out)*q*fr*intervals
            net=net_exit(pos,out,r,funding)
            buffer=entry*q*D(c['net_profit_buffer_bps'])/BPS
            reason=('target' if net>=buffer else 'stop' if net<=-D(c['stop_loss_usd']) else
                    'timeout' if time.time()-pos['opened_epoch']>=c['max_hold_sec'] else 'observation_end' if s['halt']=='observation duration reached' else None)
            if reason:
                s['equity']=str(D(s['equity'])+net)
                s['gross_losses']=str(D(s['gross_losses'])+max(D(0),-net))
                s['simulated_volume_usd']=str(D(s['simulated_volume_usd'])+q*out)
                s['rounds']+=1; s['bad_exits']=s['bad_exits']+1 if reason!='target' else 0
                trade={'mode':'SIMULATED','side':pos['side'],'quantity':str(q),'entry_px':str(entry),
                       'exit_px':str(out),'net_pnl':str(net),'funding_reserve':str(funding),
                       'reason':reason,'epoch':time.time()}
                s['trades'].append(trade); s['position']=None
                print(json.dumps(trade))
            else: print(json.dumps({'mode':'PAPER','net_exit_estimate':str(net),'side':pos['side']}))
        elif not s['halt'] and len(self.history)==self.history.maxlen:
            if D(c['daily_loss_limit'])-D(s['gross_losses'])<D(c['stop_loss_usd']):
                s['halt']='insufficient remaining loss budget'
            elif c['max_rounds'] is not None and s['rounds']>=c['max_rounds']:
                s['halt']='round limit'
            else:
                trend=(mid/self.history[0]-1)*BPS; a=analyze(m,b,r,c)
                if a['eligible'] and abs(trend)>=D(c['momentum_bps']):
                    side='LONG' if trend>0 else 'SHORT'
                    q=quantity(m,c,b['asks'][0][0]*(1+D(c['slippage_per_side_bps'])/BPS),D(s['equity']))
                    px=execution(b,q,'BUY' if side=='LONG' else 'SELL',c)
                    if D(a['immediate_round_trip_loss_usd'])<D(c['stop_loss_usd']):
                        s['position']={'side':side,'quantity':str(q),'entry_px':str(px),
                            'entry_fee':str(px*q*r),'opened_epoch':time.time(),
                            'funding_rate':str(max(abs(D(m['fundingRate'])),abs(D(m['nextFundingRate']))))}
                        s['simulated_volume_usd']=str(D(s['simulated_volume_usd'])+q*px)
                        print(json.dumps({'mode':'SIMULATED_ENTRY',**s['position']}))
        if D(s['gross_losses'])>=D(c['daily_loss_limit']) or s['bad_exits']>=c['max_bad_exits']:
            s['halt']='loss limit or repeated bad exits'
        write(self.path,s)

def main(argv=None):
    p=argparse.ArgumentParser()
    p.add_argument('command',choices=['compare','probe','doctor','dry','watch','live','preflight'])
    p.add_argument('--count',type=int,default=3)
    p.add_argument('--seconds',type=int,default=0,help='stop opening after this duration; keep managing an open position')
    a=p.parse_args(argv); c=read('perps_config.json'); validate(c)
    if a.command in ('live','preflight'):
        raise Blocked('live adapter requires verified API key, WebSocket orders/fills and account reconciliation; currently paper only')
    if a.command=='doctor': print(json.dumps({'product':'perpetual','live_ready':False,'config':c})); return
    if a.command in ('compare','probe'): compare(c); return
    if a.command=='watch':
        if not 1<=a.count<=30: raise Blocked('watch count must be 1..30')
        for i in range(a.count):
            compare(c)
            if i+1<a.count: time.sleep(c['poll_sec'])
        return
    if a.seconds<0: raise Blocked('seconds must be nonnegative')
    lock=Path('perps_paper.lock')
    try:
        with lock.open('x'): pass
    except FileExistsError: raise Blocked('paper process/stale lock exists') from None
    try:
        runtime=Paper(c); started=time.monotonic()
        while True:
            if a.seconds and time.monotonic()-started>=a.seconds:
                runtime.s['halt']='observation duration reached'
            runtime.cycle()
            if runtime.s['halt'] and not runtime.s['position']: break
            time.sleep(c['poll_sec'])
    finally: lock.unlink()

if __name__=='__main__':
    try: main()
    except (Blocked,KeyError,ValueError,OSError) as e:
        raise SystemExit('BLOCKED: '+(str(e) if isinstance(e,Blocked) else 'invalid perps data/state')) from None
