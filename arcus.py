"""Spot-only scaffold: live signing and submit deliberately unavailable."""
import argparse
import hashlib
import json
import math
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal as D, InvalidOperation
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

BASE = 'https://router.spot.arcus.xyz'
ET = ZoneInfo('America/New_York')
ALLOWED = {'SPY', 'NVDA', 'QQQ'}
BPS = D(10000)

class Blocked(RuntimeError):
    pass

class ReconcileError(Blocked):
    """Uncertain balances/status immediately latch entry halt."""
    pass

def decimal(value, name='value', positive=False):
    try:
        result = D(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise Blocked('invalid numeric setting: ' + name) from None
    if not result.is_finite() or (positive and result <= 0):
        raise Blocked('invalid numeric setting: ' + name)
    return result

def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))

def write(path, value):
    tmp = str(path) + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(value, f, indent=2, default=str)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)

def now():
    return datetime.now(ET)

def get(endpoint, params):
    req = Request(BASE + '/v1/' + endpoint + '?' + urlencode(params), headers={'Accept':'application/json'})
    try:
        with urlopen(req, timeout=10) as r:
            return {'http':r.status, 'body':json.load(r)}
    except HTTPError as e:
        raw = e.read().decode('utf-8', errors='replace')
        try:
            raw = json.loads(raw)
        except ValueError:
            pass
        return {'http':e.code, 'body':raw}

def tokens():
    response = get('tokens', {'chainId':4663})
    if response['http'] != 200 or not isinstance(response['body'], list):
        raise Blocked('invalid tokens response')
    result = {}
    for symbol in ALLOWED | {'USDG'}:
        matches = [t for t in response['body'] if t.get('symbol') == symbol and t.get('chainId') == 4663 and t.get('verified') is True]
        if len(matches) != 1:
            raise Blocked('missing/ambiguous token: ' + symbol)
        result[symbol] = matches[0]
    return result

@dataclass(frozen=True)
class Estimate:
    # Internal normalized model; these are NOT invented Router response fields.
    amount_in: D
    amount_out: D
    expires_at: float

class VerifiedSpotAdapter:
    """TODO after successful real JSON capture.
    price/quote return exact-input Estimate in human decimal units.
    Validate chain, wallet, pair, slippage, expiry and original/wrapped assets.
    Reconcile USDG, ALL allowed token balances, ETH reserve, pending status and
    receipt deltas. Unknown states MUST block. No secrets in errors or logs.
    Future live submit must journal intent BEFORE network, then resolve status;
    never repeat an ambiguous POST. Validate EIP-712 domain/nonce/deadline.
    """
    def reconcile(self, state):
        raise Blocked('wallet/RPC/balance/pending-status adapter not verified')
    def price(self, symbol, side, amount):
        raise Blocked('successful price JSON required')
    def quote(self, symbol, side, amount):
        raise Blocked('successful quote JSON required')

def validate(c):
    if c['dry_run'] is not True:
        raise Blocked('LIVE LOCKED: signing/submit/status/receipts unimplemented')
    if c['chain_id'] != 4663 or c['symbol'] != 'NVDA':
        raise Blocked('only mainnet NVDA spot enabled')
    capital, size = [decimal(c[k], k, True) for k in ('capital','notional')]
    if not D('.1') <= size / capital <= D('.2') or not 10 <= size <= 1000000:
        raise Blocked('notional must be 10%-20% of capital, >=10 USDG')
    for key in ('poll_sec','open_delay_min','max_hold_sec','max_failures','max_bad_exits'):
        if type(c[key]) is not int or c[key] <= 0:
            raise Blocked('positive integer required: ' + key)
    if not 1 <= c['poll_sec'] <= 3 or not 5 <= c['open_delay_min'] <= 10:
        raise Blocked('invalid polling/open delay')
    if not 50 <= decimal(c['jump_bps'],'jump_bps') <= 100:
        raise Blocked('invalid jump/rounds')
    if c['max_rounds'] is not None and (type(c['max_rounds']) is not int or c['max_rounds'] <= 0):
        raise Blocked('max_rounds must be null or a positive integer')
    for key in ('stop_bps','slippage_bps'):
        if not decimal(c[key],key,True) < BPS:
            raise Blocked('risk bps must be less than 10000')
    if not decimal(c['daily_loss_limit'],'daily_loss_limit',True) <= capital:
        raise Blocked('daily loss limit exceeds capital')
    if decimal(c['extra_bps'],'extra_bps') < 0 or c['max_failures'] != 3 or c['max_bad_exits'] != 3:
        raise Blocked('invalid extra/failure limits')
    if decimal(c.get('near_target_bps','8'))<0:raise Blocked('near target bps must be nonnegative')
    if not 0<decimal(c.get('max_round_trip_bps','40'))<=decimal(c['stop_bps']):raise Blocked('round-trip ceiling must not exceed stop')
    if decimal(c.get('probe_usdg','10'))!=10:raise Blocked('validation probe must remain 10 USDG')
    if c['round_trip_bps'] is not None and not 0 <= decimal(c['round_trip_bps']) < BPS:
        raise Blocked('invalid round-trip threshold')


def calibrated(c, data, day):
    rows = data['samples']
    if data['date_et'] != day or data['symbol'] != c['symbol'] or not 3 <= len(rows) <= 5:
        raise Blocked('need 3-5 real same-day, same-symbol immediate reversals')
    values, ids = [], set()
    for row in rows:
        spent, received = decimal(row['usdg_spent'],positive=True), decimal(row['usdg_received'],positive=True)
        if spent <= 0 or received <= 0 or not row['buy_txid'] or not row['sell_txid'] or row['sell_txid'] in ids or row['buy_txid'] in ids or row['sell_txid'] == row['buy_txid']:
            raise Blocked('invalid/duplicate calibration receipt')
        ids.update((row['sell_txid'], row['buy_txid']))
        values.append((1 - received / spent) * BPS)
    mean = sum(values) / len(values)
    if c['round_trip_bps'] is None or D(c['round_trip_bps']) < 0 or abs(D(c['round_trip_bps']) - mean) > D('.01'):
        raise Blocked('set ROUND_TRIP_BPS to measured mean, precision 0.01 bp')
    return D(c['round_trip_bps'])

def session(c, calendar, stamp):
    row = calendar['sessions'].get(stamp.date().isoformat())
    if stamp.weekday() >= 5 or not row or row.get('verified') is not True or c['symbol'] not in row.get('earnings_clear_symbols', []):
        return False
    opening, closing = [datetime.fromisoformat(row[k]) for k in ('open_et','close_et')]
    if opening.tzinfo is None or closing.tzinfo is None:
        return False
    opening, closing = opening.astimezone(ET), closing.astimezone(ET)
    if not opening.date() == closing.date() == stamp.date() or (opening.hour,opening.minute) != (9,30) or (closing.hour,closing.minute) > (16,0):
        return False
    return opening + timedelta(minutes=c['open_delay_min']) <= stamp < closing - timedelta(seconds=c['max_hold_sec'])

def round_limit_reached(c, state):
    return c['max_rounds'] is not None and state['rounds'] >= c['max_rounds']

def exit_reason(spent, proceeds, age, c, rt):
    if age >= c['max_hold_sec']:
        return 'timeout'
    if proceeds <= spent * (1 - D(c['stop_bps']) / BPS):
        return 'stop'
    if proceeds >= spent * (1 + (rt + D(c['extra_bps'])) / BPS):
        return 'target'
    return None

class Engine:
    def __init__(self, c, adapter):
        validate(c)
        self.c, self.a = c, adapter
        self.calendar, calibration = read('calendar.json'), read('calibration.json')
        self.day = now().date().isoformat()
        self.rt = calibrated(c, calibration, self.day)
        fingerprint = hashlib.sha256(json.dumps([c,self.calendar,calibration], sort_keys=True).encode()).hexdigest()
        self.s = read('dry_state.json') if Path('dry_state.json').exists() else {
            'date':self.day, 'fingerprint':fingerprint, 'cash':c['capital'], 'position':None,
            'pending':None, 'pnl':'0', 'rounds':0, 'failures':0, 'bad_exits':0,
            'halt':False, 'last_buy_px':None}
        if self.s['date'] != self.day or self.s['fingerprint'] != fingerprint:
            raise Blocked('day/config changed: reconcile and archive state before restart')
        self.reconcile()
        if self.s['pending']:
            raise Blocked('unresolved submit: query status before any new entry')
        self.save()
    def save(self):
        write('dry_state.json', self.s)
    def log(self, **fields):
        with open('trades.jsonl','a',encoding='utf-8') as f:
            f.write(json.dumps({'time':now().isoformat(),'mode':'DRY_RUN',**fields},default=str)+'\n')
    def reconcile(self):
        try:
            if self.s.get('pending'):
                raise ReconcileError('pending submit requires status resolution')
            # Explicit success required; a missing return cannot authorize entry.
            if self.a.reconcile(self.s) is not True:
                raise ReconcileError('balance/status reconciliation not confirmed')
        except (Blocked, OSError, ValueError, TimeoutError) as error:
            self.s['halt'] = True
            self.save()
            raise ReconcileError('balance/status uncertain; entries halted') from None
    def check(self, q, amount):
        if not isinstance(q, Estimate):
            raise Blocked('invalid estimate type')
        incoming = decimal(q.amount_in,positive=True)
        outgoing = decimal(q.amount_out,positive=True)
        if incoming != amount or not isinstance(q.expires_at,(int,float)) or not math.isfinite(q.expires_at) or q.expires_at <= time.time():
            raise Blocked('mismatched or expired estimate')
        return Estimate(incoming,outgoing,q.expires_at)
    def cycle(self):
        try:
            active = self.step()
            # Idle/out-of-session cycles are not successful quote attempts.
            if active:
                self.s['failures'] = 0
        except ReconcileError:
            self.s['halt'] = True
            self.log(status='RECONCILE_UNCERTAIN')
        except (Blocked,OSError,ValueError,TimeoutError,InvalidOperation):
            self.s['failures'] += 1
            if self.s['failures'] >= self.c['max_failures']:
                self.s['halt'] = True
            self.log(status='QUOTE_FAILED',failures=self.s['failures'])
        self.save()

    def preview(self, side, q, spent, loss):
        print(json.dumps({'direction':side,'quantity':str(q.amount_in),'quoted_output':str(q.amount_out),
            'target_usdg':str(spent*(1+(self.rt+D(self.c['extra_bps']))/BPS)),
            'estimated_loss_bps':str(loss),'mode':'DRY_RUN'}),flush=True)
    def step(self):
        c,s = self.c,self.s
        stamp = now()
        if stamp.date().isoformat() != self.day:
            s['halt'] = True
        if s.get('pending'):
            s['halt'] = True
            self.save()
            raise ReconcileError('pending status must be resolved before any trade')
        p = s['position']
        if p:
            spent,amount = D(p['usdg_spent']),D(p['token_amount'])
            age = time.time()-p['opened_epoch']
            reason = 'timeout' if age >= c['max_hold_sec'] else p.get('exit_reason')
            if not reason:
                estimate = self.check(self.a.price(c['symbol'],'SELL',amount),amount)
                reason = exit_reason(spent,estimate.amount_out,age,c,self.rt)
                if D(s['pnl']) + estimate.amount_out - spent <= -D(c['daily_loss_limit']):
                    s['halt'],reason = True,'stop'
            if not reason:
                return True
            p['exit_reason'] = reason
            self.save()
            q = self.check(self.a.quote(c['symbol'],'SELL',amount),amount)
            if reason == 'target':
                reason = exit_reason(spent,q.amount_out,age,c,self.rt)
                if not reason:
                    p['exit_reason'] = None
                    self.save()
                    return True
            p['exit_reason'] = reason
            self.save()
            self.preview('SELL',q,spent,(1-q.amount_out/spent)*BPS)
            self.check(q,amount)
            pnl = q.amount_out-spent
            s['cash'],s['pnl'] = str(D(s['cash'])+q.amount_out),str(D(s['pnl'])+pnl)
            s['rounds'] += 1
            s['bad_exits'] = s['bad_exits']+1 if reason in {'stop','timeout'} else 0
            s['position'] = None
            if D(s['pnl']) <= -D(c['daily_loss_limit']) or s['bad_exits'] >= 3:
                s['halt'] = True
            self.save()
            self.log(txid=None,status='SIMULATED',spent=spent,received=q.amount_out,bp=pnl/spent*BPS,exit_reason=reason)
            return True
        if s['halt'] or round_limit_reached(c,s) or D(s['pnl']) <= -D(c['daily_loss_limit']) or not session(c,self.calendar,stamp):
            return
        self.reconcile()
        amount = D(c['notional'])
        if D(s['cash']) < amount:
            raise Blocked('insufficient USDG')
        buy = self.check(self.a.quote(c['symbol'],'BUY',amount),amount)
        reverse = self.check(self.a.quote(c['symbol'],'SELL',buy.amount_out),buy.amount_out)
        self.check(buy,amount)
        loss,px = (1-reverse.amount_out/amount)*BPS,amount/buy.amount_out
        previous,s['last_buy_px'] = s['last_buy_px'],str(px)
        self.save()
        if previous and abs(px/D(previous)-1)*BPS > D(c['jump_bps']):
            s['halt'] = True
            self.save()
            return
        if loss > self.rt:
            return True
        self.preview('BUY',buy,amount,loss)
        self.check(buy,amount)
        s['cash'] = str(D(s['cash'])-amount)
        s['position'] = {'usdg_spent':str(amount),'token_amount':str(buy.amount_out),
            'entry_px':str(px),'opened_epoch':time.time(),'exit_reason':None}
        self.save()
        self.log(txid=None,status='SIMULATED',spent=amount,received=None,bp=None,exit_reason=None)
        return True
    def run(self):
        while True:
            self.cycle()
            if (self.s['halt'] or round_limit_reached(self.c,self.s)) and not self.s['position']:
                return
            time.sleep(self.c['poll_sec'])

def main():
    import sys
    if len(sys.argv)>1 and sys.argv[1]=='spot':
        from spot_runtime import main as spot_main
        spot_main(sys.argv[2:])
        return
    if len(sys.argv)>1 and (sys.argv[1]=='perps' or sys.argv[1] in {'compare','probe','doctor','watch','dry','live','preflight'}):
        from perps_runtime import main as perps_main
        perps_main(sys.argv[2:] if sys.argv[1]=='perps' else sys.argv[1:])
        return
    if len(sys.argv)>1 and sys.argv[1] in {'probe','doctor','new-day','preflight','watch','dry','live','calibrate','recover','approve'}:
        from spot_runtime import main as spot_main
        spot_main(sys.argv[1:])
        return
    parser = argparse.ArgumentParser()
    parser.add_argument('command',choices=['tokens','capture','run'])
    parser.add_argument('endpoint',nargs='?',choices=['price','quote','status'])
    args = parser.parse_args()
    if args.command == 'tokens':
        result = tokens()
        write('tokens.snapshot.json',result)
        print(json.dumps(result,indent=2))
    elif args.command == 'capture':
        if not args.endpoint:
            raise Blocked('capture needs an endpoint')
        params = read('requests.json')[args.endpoint]
        response = get(args.endpoint,params or {})
        Path('captures').mkdir(exist_ok=True)
        path = Path('captures')/(args.endpoint+'-'+str(time.time_ns())+'.json')
        write(path,{'captured_at':now().isoformat(),**response})
        print(str(path),'HTTP',response['http'])
    else:
        # Single-process lock: stale lock requires manual reconciliation.
        lock = Path('dry_state.json.lock')
        try:
            fd = os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY)
        except FileExistsError:
            raise Blocked('existing process/stale lock: reconcile before removal')
        try:
            Engine(read('config.json'),VerifiedSpotAdapter()).run()
        finally:
            os.close(fd)
            lock.unlink()

if __name__ == '__main__':
    import sys
    sys.modules.setdefault('arcus',sys.modules[__name__])
    try:
        main()
    except Blocked as e:
        raise SystemExit('BLOCKED: '+str(e))

