"""Persistent single-position live/dry Spot execution. No automatic POST retries."""
import argparse
import copy
import hashlib
import json
import os
import time
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal as D
from pathlib import Path
from eth_utils import keccak
from arcus import Blocked, ReconcileError, read, write, now, validate, session, exit_reason, round_limit_reached, decimal
from spot_api import Spot, Quote, TransportError, FeePolicyError, atomic, human, addr, uint

@contextmanager
def lock():
    path=Path('spot.lock')
    try:fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY)
    except FileExistsError:raise Blocked('spot.lock exists: confirm previous process stopped before removing')
    try:
        os.write(fd,str(os.getpid()).encode());os.fsync(fd)
        yield
    finally:
        os.close(fd);path.unlink()

def event(**fields):
    print(json.dumps({'time':now().isoformat(),**fields},default=str),flush=True)

class Runtime:
    def __init__(self,c,spot,live=False):
        validate({**c,'dry_run':True})
        if D(c['slippage_bps'])!=int(D(c['slippage_bps'])):raise Blocked('integer slippage bps required')
        self.c=copy.deepcopy(c);self.spot=spot;self.live=live
        self.path=Path('live_state.json' if live else 'dry_spot_state.json')
        self.calendar=read('calendar.json')
        self.day=now().date().isoformat()
        fingerprint=hashlib.sha256(json.dumps(c,sort_keys=True).encode()).hexdigest()
        spot.verify_chain()
        if self.path.exists():
            self.s=read(self.path)
            if self.s['wallet']!=spot.wallet:raise Blocked('state belongs to another wallet')
            if self.s.get('contracts')!=spot.contracts:raise Blocked('deployment changed; review before signing')
            if self.s['fingerprint']!=fingerprint:
                # Keep original risk settings for an existing position; don't strand it.
                if not self.s['position'] and not self.s['pending']:raise Blocked('intraday config changed; restore original settings')
                self.c=self.s['config'];self.spot.c=self.c;self.s['halt']=True
        else:
            balances=spot.snapshot()
            if balances['NVDA']:raise ReconcileError('untracked NVDA holding; cannot infer its entry cost')
            if human(balances['USDG'],spot.tokens['USDG']['decimals'])<D(c['capital']):raise Blocked('wallet has less than configured 100 USDG capital')
            self.s={'version':1,'wallet':spot.wallet,'contracts':copy.deepcopy(spot.contracts),'date':self.day,'config':copy.deepcopy(c),'fingerprint':fingerprint,
                'balances':balances,'cash':c['capital'],'position':None,'pending':None,'halt':False,
                'pnl':'0','rounds':0,'failures':0,'bad_exits':0,'last_px':None,'last_sell_px':None,
                'trades':[],'calibration':[],'dry_verified':False,'round_trip_bps':None}
            self.save()
        if self.s['date']!=self.day:self.s['halt']=True
        self.save()
        if self.s['pending']:self.resolve()
        self.reconcile(check_gas=not bool(self.s['position']))
    def save(self):write(self.path,self.s)
    def log(self,**fields):
        event(mode='LIVE' if self.live else 'DRY_RUN',**fields)
        # State contains authoritative trade history; JSONL is a replayable view.
        with open('live_trades.jsonl' if self.live else 'dry_trades.jsonl','a',encoding='utf-8') as f:
            f.write(json.dumps({'time':now().isoformat(),**fields},default=str)+'\n');f.flush();os.fsync(f.fileno())
    def reconcile(self,check_gas=True):
        if self.s['pending']:raise ReconcileError('pending submit unresolved')
        try:
            observed=self.spot.snapshot()
            for token in ('USDG','NVDA','SPY','QQQ'):
                if observed[token]!=self.s['balances'][token]:raise ReconcileError('unexpected token balance delta')
            if check_gas and human(observed['eth'],18)<decimal(self.c.get('min_eth_balance','0.0001'),positive=True):raise ReconcileError('separate ETH gas reserve insufficient')
        except (Blocked,OSError,ValueError):
            self.s['halt']=True;self.save()
            raise ReconcileError('balance/pending/gas check failed; entries halted') from None
        return observed
    def preview(self,q,reason,loss=None):
        target=None
        if self.s['position'] and self.s['round_trip_bps'] is not None:
            target=D(self.s['position']['usdg_spent'])*(1+(D(self.s['round_trip_bps'])+D(self.c['extra_bps']))/10000)
        elif q.side=='BUY' and self.s['round_trip_bps'] is not None:
            target=D(self.c['notional'])*(1+(D(self.s['round_trip_bps'])+D(self.c['extra_bps']))/10000)
        self.log(status='PRE_SUBMIT' if self.live else 'PRE_SIMULATION',direction=q.side,
            quantity=human(q.sell_raw,self.spot.meta[q.sell_token]['decimals']),quote=self.spot.output(q),
            minimum_output=self.spot.output(q,True),target_usdg=target,estimated_loss_bps=loss,
            fees=q.fees,exit_reason=reason)
    def target_usdg(self):
        if self.s['round_trip_bps'] is None:raise Blocked('target requires measured round-trip cost')
        return D(self.s['position']['usdg_spent'])*(1+(D(self.s['round_trip_bps'])+D(self.c['extra_bps']))/10000)
    def check_exit(self,q,reason):
        if q.side!='SELL':return reason
        p=self.s['position']
        if not p:raise Blocked('no position to sell')
        if q.sell_token!=addr(self.spot.tokens['NVDA']['address']) or q.buy_token!=addr(self.spot.tokens['USDG']['address']):raise Blocked('wrong exit pair')
        if q.sell_raw!=atomic(p['token_amount'],self.spot.tokens['NVDA']['decimals']):raise Blocked('exit quantity differs from actual holding')
        if time.time()-p['opened_epoch']>=self.c['max_hold_sec']:reason='timeout'
        if reason=='target' and self.spot.output(q,True)<self.target_usdg():raise Blocked('minimum net proceeds below target: do not sign')
        if reason not in ('target','stop','timeout','calibration'):raise Blocked('invalid exit reason')
        if reason=='calibration' and not p['calibration']:raise Blocked('not a calibration position')
        return reason
    def execute(self,q,reason=None,loss=None):
        if self.s['pending']:raise ReconcileError('pending submit blocks new submission')
        if q.expiry<=time.time()+2:raise Blocked('quote expired before execution')
        if q.side=='BUY':
            if self.s['position'] or self.s['halt']:raise Blocked('entry blocked')
            self.spot.fee_policy(q)
        elif not self.s['position']:raise Blocked('no position to sell')
        reason=self.check_exit(q,reason)
        before=self.reconcile(check_gas=q.side=='BUY')
        if self.live:
            self.spot.ensure_allowance(q)
            reason=self.check_exit(q,reason)  # last check immediately before signing
            body=self.spot.signed_body(q)
        if q.expiry<=time.time()+2:raise Blocked('quote expired during preflight')
        self.preview(q,reason,loss)
        pending={'side':q.side,'sell_token':q.sell_token,'buy_token':q.buy_token,'sell_raw':q.sell_raw,
            'buy_raw':q.buy_raw,'min_raw':q.min_raw,'expiry':q.expiry,'typed_data':q.typed_data,
            'before':before,'reason':reason,'txHash':None,'stage':'SUBMITTING','created_epoch':time.time()}
        self.s['pending']=pending;self.save()
        if not self.live:
            # Conservative simulated fill, never a real calibration sample.
            self.commit(q.sell_raw,q.min_raw,None,before,simulated=True)
            return
        try:
            if q.expiry<=time.time()+1:raise Blocked('quote expired immediately before POST')
            result=self.spot.router.submit(body)
            if result.get('venue')!='arcus' or result.get('status')!='submitted':raise ReconcileError('unexpected submit response')
            tx=result['txHash']
            if not isinstance(tx,str) or len(tx)!=66:raise ReconcileError('invalid submit tx hash')
            pending['txHash']=tx;pending['stage']='SUBMITTED';self.save()
        except Exception:
            # The POST may have reached the server even if its response was lost.
            self.s['halt']=True;self.save()
            raise ReconcileError('submit outcome uncertain; no retry; use recover') from None
        self.resolve()
    def resolve(self,txhash=None):
        pending=self.s['pending']
        if not pending:return
        if not self.live:raise ReconcileError('interrupted simulation: inspect state before resume')
        if txhash:
            if pending.get('txHash') and pending['txHash'].lower()!=txhash.lower():raise Blocked('cannot replace known pending tx hash')
            pending['txHash']=txhash;self.save()
        tx=pending.get('txHash')
        if not tx:raise ReconcileError('submit has no tx hash; obtain actual hash and use recover --tx-hash; never resubmit')
        cutoff=time.monotonic()+int(self.c.get('status_timeout_sec',30))
        while True:
            status=self.spot.status(tx)
            receipt=self.spot.rpc.call('eth_getTransactionReceipt',[tx])
            state=status.get('status')
            if status.get('txHash','').lower()!=tx.lower() or status.get('venue')!='arcus':raise ReconcileError('status identity mismatch')
            if receipt and state in ('confirmed','failed') and int(self.spot.rpc.call('eth_blockNumber',[]),16)-int(receipt['blockNumber'],16)+1>=int(self.c.get('confirmations',2)):
                self.finish_receipt(pending,status,receipt)
                return
            if time.monotonic()>=cutoff:
                self.s['halt']=True;self.save()
                raise ReconcileError('transaction pending/unknown; no resubmit')
            time.sleep(2)
    def finish_receipt(self,p,status,receipt):
        if receipt['transactionHash'].lower()!=p['txHash'].lower():raise ReconcileError('receipt hash mismatch')
        if addr(receipt['to']) not in {addr(self.spot.contracts[k]) for k in ('arcusSettlement','swapShell')}:raise ReconcileError('unexpected settlement recipient')
        height=int(receipt['blockNumber'],16)
        latest=int(self.spot.rpc.call('eth_blockNumber',[]),16)
        if latest-height+1<int(self.c.get('confirmations',2)):raise ReconcileError('receipt awaiting confirmations; recover later')
        canonical=self.spot.rpc.call('eth_getBlockByNumber',[receipt['blockNumber'],False])
        if canonical['hash'].lower()!=receipt['blockHash'].lower():raise ReconcileError('receipt reorg detected')
        deltas=self.spot.transfer_deltas(receipt)
        after=self.spot.snapshot()
        for token in ('USDG','NVDA','SPY','QQQ'):
            if after[token]-p['before'][token]!=deltas[token]:raise ReconcileError('balance deltas differ from receipt; unknown concurrent activity')
        swap=status.get('swap')
        success=status['status']=='confirmed' and int(receipt['status'],16)==1 and isinstance(swap,dict) and swap.get('success') is True
        if not success:
            if any(deltas.values()):raise ReconcileError('failed/soft-failed transaction changed balances')
            self.s['pending']=None;self.s['halt']=True;self.s['balances']=after;self.save()
            self.log(status='FAILED',txid=p['txHash'],exit_reason=p['reason'])
            return
        if addr(swap['taker'])!=self.spot.wallet or addr(swap['tokenIn'])!=p['sell_token'] or addr(swap['tokenOut'])!=p['buy_token']:raise ReconcileError('swap identity mismatch')
        sell,buy=('USDG','NVDA') if p['side']=='BUY' else ('NVDA','USDG')
        spent,received=-deltas[sell],deltas[buy]
        if spent!=p['sell_raw'] or received<p['min_raw'] or uint(swap['amountIn'])!=spent or uint(swap['amountOut'])!=received or uint(swap['minAmountOut'])!=p['min_raw']:raise ReconcileError('settled amounts mismatch signed intent')
        self.commit(spent,received,p['txHash'],after)
    def commit(self,spent,received,txhash,after,simulated=False):
        p=self.s['pending'];side=p['side'];c=self.c
        in_dec=self.spot.meta[p['sell_token']]['decimals'];out_dec=self.spot.meta[p['buy_token']]['decimals']
        incoming,outgoing=human(spent,in_dec),human(received,out_dec)
        trade={'time':now().isoformat(),'txid':txhash,'status':'SIMULATED' if simulated else 'CONFIRMED',
            'direction':side,'amount_in':str(incoming),'amount_out':str(outgoing),'exit_reason':p['reason']}
        if side=='BUY':
            self.s['cash']=str(D(self.s['cash'])-incoming)
            self.s['position']={'usdg_spent':str(incoming),'token_amount':str(outgoing),'entry_px':str(incoming/outgoing),
                'opened_epoch':time.time(),'buy_txid':txhash,'exit_reason':None,'calibration':p['reason']=='calibration'}
            trade.update(spent=str(incoming),received=None,bp=None)
        else:
            old=self.s['position'];cost=D(old['usdg_spent']);pnl=outgoing-cost
            self.s['cash']=str(D(self.s['cash'])+outgoing);self.s['pnl']=str(D(self.s['pnl'])+pnl)
            self.s['rounds']+=1
            self.s['bad_exits']=self.s['bad_exits']+1 if p['reason'] in ('stop','timeout') else 0
            if old['calibration'] and not simulated:
                self.s['calibration'].append({'usdg_spent':str(cost),'usdg_received':str(outgoing),
                    'buy_txid':old['buy_txid'],'sell_txid':txhash,'date_et':self.day})
            self.s['position']=None
            trade.update(spent=str(cost),received=str(outgoing),bp=str(pnl/cost*10000))
        if not simulated:self.s['balances']=after
        self.s['pending']=None
        if D(self.s['pnl'])<=-D(c['daily_loss_limit']) or self.s['bad_exits']>=3:self.s['halt']=True
        self.s['trades'].append(trade);self.save()
        self.log(**trade)
    def threshold(self,calibrate=False):
        if calibrate:
            cap=self.c.get('calibration_max_loss_bps')
            if cap is None:raise Blocked('set calibration_max_loss_bps after inspecting dry-run round trip')
            return decimal(cap,positive=True)
        if self.s['round_trip_bps'] is None:
            rows=self.s['calibration']
            if self.live:
                if not 3<=len(rows)<=5 or any(r['date_et']!=self.day for r in rows):raise Blocked('3-5 confirmed same-day calibration rounds required')
                rt=sum((1-D(r['usdg_received'])/D(r['usdg_spent']))*10000 for r in rows)/len(rows)
            else:
                # Dry-run uses only explicitly supplied provisional threshold.
                rt=self.c.get('dry_run_round_trip_bps')
                if rt is None:raise Blocked('dry_run_round_trip_bps is unset; use watch to inspect quotes first')
                rt=decimal(rt)
            if rt<0:raise Blocked('negative measured loss; repeat calibration rather than invent threshold')
            self.s['round_trip_bps']=str(rt);self.save()
        return D(self.s['round_trip_bps'])
    def step(self,calibrate=False):
        s,c=self.s,self.c
        if now().date().isoformat()!=s['date']:s['halt']=True
        if s['pending']:self.resolve();return
        p=s['position']
        if p:
            age=time.time()-p['opened_epoch'];spent=D(p['usdg_spent']);amount=D(p['token_amount'])
            reason='timeout' if age>=c['max_hold_sec'] else p.get('exit_reason')
            if not reason and p['calibration']:reason='calibration'
            if not reason:
                proceeds=self.spot.price('SELL',amount)
                unit=proceeds/amount
                if s['last_sell_px'] and abs(unit/D(s['last_sell_px'])-1)*10000>D(c['jump_bps']):s['halt']=True
                s['last_sell_px']=str(unit)
                reason=exit_reason(spent,proceeds,age,c,D(s['round_trip_bps']))
                if D(s['pnl'])+proceeds-spent<=-D(c['daily_loss_limit']):s['halt']=True;reason='stop'
                if not reason:
                    target=self.target_usdg()
                    gap=(target-proceeds)/target*10000
                    if gap<=D(c.get('near_target_bps','8')):reason='target'
            if not reason:return
            p['exit_reason']=reason;self.save()
            q=self.spot.quote('SELL',amount)
            age=time.time()-p['opened_epoch']
            if age>=c['max_hold_sec']:reason='timeout'
            elif D(s['pnl'])+self.spot.output(q,True)-spent<=-D(c['daily_loss_limit']):
                s['halt']=True;reason='stop'
            if reason=='target':
                reason=exit_reason(spent,self.spot.output(q,True),age,c,D(s['round_trip_bps']))
                if not reason:
                    p['exit_reason']=None;self.save()
                    self.log(status='WAIT_TARGET',minimum_usdg=self.spot.output(q,True),target_usdg=self.target_usdg())
                    return
            p['exit_reason']=reason;self.save()
            self.execute(q,reason,(1-self.spot.output(q)/spent)*10000)
            return
        if s['halt'] or round_limit_reached(c,s) or D(s['pnl'])<=-D(c['daily_loss_limit']):return
        if not session(c,self.calendar,now()):return
        rt=self.threshold(calibrate)
        self.reconcile()
        if D(s['cash'])<D(c['notional']):raise Blocked('insufficient strategy cash')
        probe=self.spot.measure_round_trip(D(c.get('probe_usdg','10')))
        ceiling=min(D(c.get('max_round_trip_bps','40')),D(c['stop_bps']))
        if probe['round_trip_bps']>=ceiling:
            self.log(status='SKIP_PRICE_ROUND_TRIP',**probe);return
        buy=self.spot.quote('BUY',D(c['notional']))
        self.spot.fee_policy(buy)
        reverse=self.spot.quote('SELL',self.spot.output(buy))
        self.spot.fee_policy(reverse)
        loss=(1-self.spot.output(reverse)/D(c['notional']))*10000
        if buy.expiry<=time.time()+2:raise Blocked('buy quote expired during round-trip precheck')
        px=D(c['notional'])/self.spot.output(buy)
        if s['last_px'] and abs(px/D(s['last_px'])-1)*10000>D(c['jump_bps']):s['halt']=True;return
        s['last_px']=str(px)
        if loss>=ceiling:
            self.log(status='SKIP_IMMEDIATE_STOP',loss_bps=loss,stop_bps=c['stop_bps']);return
        if loss>rt:
            self.log(status='SKIP_ROUND_TRIP',loss_bps=loss,limit_bps=rt);return
        if self.live:
            # Exit approval must exist BEFORE buying. Use quoted amount plus slippage buffer.
            required=atomic(self.spot.output(buy),self.spot.tokens['NVDA']['decimals'])
            if self.spot.rpc.allowance(self.spot.tokens['NVDA']['address'],self.spot.wallet,self.spot.contracts['permit2'])<required:
                raise Blocked('NVDA exit allowance missing; approve NVDA before opening')
        self.execute(buy,'calibration' if calibrate else None,loss)
    def run(self,calibrate=False,rounds=3):
        if calibrate and not self.live:raise Blocked('calibrate requires explicit live command')
        if not self.s['position']:self.threshold(calibrate)
        if calibrate and not self.s['position']:
            evidence=Path('dry_evidence.json')
            if not evidence.exists():raise Blocked('run watch before real calibration')
            e=read(evidence)
            if e.get('date_et')!=self.day or e.get('wallet')!=self.spot.wallet:raise Blocked('same-day same-wallet dry evidence required')
        while True:
            if calibrate and len(self.s['calibration'])>=rounds and not self.s['position']:break
            attempted=self.s['position'] is not None or session(self.c,self.calendar,now())
            try:
                self.step(calibrate)
                if attempted:self.s['failures']=0
            except FeePolicyError:
                self.s['halt']=True;self.save();raise
            except ReconcileError:
                self.s['halt']=True;self.save()
                # Pending uncertainties must be resolved before any other trade.
                if self.s['pending']:raise
                if not self.s['position']:raise
                self.log(status='RECONCILE_FAILED_CLOSE_ONLY')
            except (Blocked,TransportError,OSError,ValueError):
                self.s['failures']+=1
                if self.s['failures']>=3:self.s['halt']=True
                self.log(status='QUOTE_OR_EXECUTION_FAILED',failures=self.s['failures'])
            self.save()
            if (self.s['halt'] or round_limit_reached(self.c,self.s)) and not self.s['position']:break
            time.sleep(self.c['poll_sec'])
        if calibrate and 3<=len(self.s['calibration'])<=5:
            rows=self.s['calibration'];value=sum((1-D(r['usdg_received'])/D(r['usdg_spent']))*10000 for r in rows)/len(rows)
            write('calibration.json',{'date_et':self.day,'symbol':'NVDA','samples':rows,'mean_bps':str(value)})
            self.log(status='CALIBRATION_COMPLETE',mean_bps=value,samples=len(rows))


def dump_responses(spot):
    # GET responses only. Never accepts signed submit bodies or environment data.
    sensitive={'signature','privatekey','mnemonic','authorization','apikey','access_token'}
    def redact(value):
        if isinstance(value,dict):return {k:('[REDACTED]' if k.lower() in sensitive else redact(v)) for k,v in value.items()}
        if isinstance(value,list):return [redact(v) for v in value]
        return value
    data=redact(spot.captures)
    Path('captures').mkdir(exist_ok=True)
    path=Path('captures')/('spot-schema-'+str(time.time_ns())+'.json')
    write(path,data)
    event(status='RAW_GET_RESPONSES',path=str(path),responses=data)


def watch(spot,count,dump_json=False):
    rows=[]
    for _ in range(count):
        buy=spot.quote('BUY',D(spot.c['notional']))
        sell=spot.quote('SELL',spot.output(buy))
        loss=(1-spot.output(sell)/D(spot.c['notional']))*10000
        row={'time':now().isoformat(),'buy_usdg':spot.c['notional'],'token_amount':str(spot.output(buy)),
            'sell_usdg':str(spot.output(sell)),'round_trip_bps':str(loss),'buy_fees':buy.fees,'sell_fees':sell.fees}
        rows.append(row);event(status='READ_ONLY_ROUND_TRIP',**row)
        if dump_json and len(rows)==1:dump_responses(spot)
        time.sleep(spot.c['poll_sec'])
    write('dry_evidence.json',{'wallet':spot.wallet,'date_et':now().date().isoformat(),'rows':rows,'simulated':True})


def approve(spot,symbol,amount):
    # Explicit bounded approval. Never infinite allowance; signed tx hash known pre-send.
    if symbol not in ('USDG','NVDA'):raise Blocked('approval only for USDG/NVDA')
    value=atomic(amount,spot.tokens[symbol]['decimals'])
    if symbol=='NVDA' and spot.price('SELL',D(amount))>D(spot.c['capital']):raise Blocked('NVDA approval notional exceeds capital')
    if symbol=='USDG' and D(amount)>D(spot.c['capital']):raise Blocked('USDG approval exceeds capital')
    spot.verify_chain()
    journal=Path('approval_pending.json')
    if journal.exists():
        old=read(journal)
        if old['wallet']!=spot.wallet:raise Blocked('approval journal belongs to another wallet')
        receipt=spot.rpc.call('eth_getTransactionReceipt',[old['txHash']])
        if not receipt:raise Blocked('approval pending; never rebroadcast automatically')
        if int(receipt['status'],16)!=1:raise Blocked('approval failed; inspect journal before retry')
        if addr(receipt['to'])!=addr(old['token']) or receipt['transactionHash'].lower()!=old['txHash'].lower():raise Blocked('approval receipt mismatch')
        if int(spot.rpc.call('eth_blockNumber',[]),16)-int(receipt['blockNumber'],16)+1<spot.c['confirmations']:raise Blocked('approval awaiting confirmations')
        if spot.rpc.allowance(old['token'],spot.wallet,spot.contracts['permit2'])<old['value']:raise Blocked('approval allowance not confirmed')
        write('approval_last.json',old);journal.unlink()
        event(status='APPROVAL_CONFIRMED',txid=old['txHash']);return
    spot.snapshot()
    token=spot.tokens[symbol]['address'];spender=spot.contracts['permit2']
    data='0x095ea7b3'+addr(spender)[2:].zfill(64)+hex(value)[2:].zfill(64)
    tx={'from':spot.wallet,'to':token,'data':data,'value':'0x0'}
    gas=int(spot.rpc.call('eth_estimateGas',[tx]),16)*12//10
    gas_price=int(spot.rpc.call('eth_gasPrice',[]),16)
    balance=int(spot.rpc.call('eth_getBalance',[spot.wallet,'latest']),16)
    reserve=atomic(spot.c['min_eth_balance'],18)
    if balance<gas*gas_price+reserve:raise Blocked('not enough separate ETH for approval and reserve')
    signed=spot.signer().sign_transaction({'chainId':4663,'nonce':int(spot.rpc.call('eth_getTransactionCount',[spot.wallet,'pending']),16),
        'to':__import__('eth_utils').to_checksum_address(token),'data':data,'value':0,'gas':gas,'gasPrice':gas_price})
    txhash='0x'+bytes(signed.hash).hex()
    event(status='PRE_APPROVAL',token=symbol,amount=amount,spender=spender,max_eth_cost=human(gas*gas_price,18))
    write(journal,{'txHash':txhash,'wallet':spot.wallet,'token':token,'value':value})
    result=spot.rpc.call('eth_sendRawTransaction',['0x'+bytes(signed.raw_transaction).hex()])
    if result.lower()!=txhash.lower():raise ReconcileError('approval hash mismatch')
    event(status='APPROVAL_SUBMITTED',txid=txhash)


def main(argv=None):
    parser=argparse.ArgumentParser()
    parser.add_argument('command',choices=['probe','doctor','new-day','preflight','watch','dry','live','calibrate','recover','approve'])
    parser.add_argument('--dump-json',action='store_true')
    parser.add_argument('--count',type=int,default=3)
    parser.add_argument('--rounds',type=int,choices=[3,4,5],default=3)
    parser.add_argument('--tx-hash')
    parser.add_argument('--token',choices=['USDG','NVDA'])
    parser.add_argument('--amount')
    args=parser.parse_args(argv)
    c=read('config.json');validate({**c,'dry_run':True})
    for key in ('confirmations','status_timeout_sec'):
        if type(c[key]) is not int or c[key]<=0:raise Blocked('invalid '+key)
    if type(c['accept_quote_fees']) is not bool:raise Blocked('invalid fee policy')
    if args.command=='doctor':
        from spot_ops import doctor
        event(**doctor(c));return
    dry_value=os.getenv('DRY_RUN','1' if c['dry_run'] else '0')
    if dry_value not in ('0','1'):raise Blocked('DRY_RUN must be 0 or 1')
    if args.command in ('live','calibrate','approve') and dry_value!='0':
        raise Blocked('DRY_RUN is enabled: signing/submission disabled; explicitly set DRY_RUN=0 for live actions')
    spot=Spot(c)
    with lock():
        if args.command=='probe':
            event(status='READ_ONLY_PRICE_PROBE',**spot.measure_round_trip(D(c['probe_usdg'])))
            if args.dump_json:dump_responses(spot)
            return
        if args.command=='new-day':
            from spot_ops import new_day
            event(**new_day(c,spot));return
        if args.command=='watch':
            if not 1<=args.count<=30:raise Blocked('watch count must be 1-30')
            watch(spot,args.count,args.dump_json);return
        if args.command=='preflight':
            spot.verify_chain();balances=spot.snapshot()
            event(status='PREFLIGHT',wallet=spot.wallet,balances=balances,signer_configured=bool(os.getenv('ARCUS_PRIVATE_KEY') or os.getenv('ARCUS_MNEMONIC')),
                pending_exists=Path('approval_pending.json').exists() or (Path('live_state.json').exists() and bool(read('live_state.json')['pending'])))
            return
        if args.command=='approve':
            if not args.token or not args.amount:raise Blocked('approve requires --token and --amount')
            if Path('live_state.json').exists():
                state=read('live_state.json')
                if state['pending']:raise Blocked('resolve pending trade before approval')
                if state['position'] and (args.token!='NVDA' or D(args.amount)>D(state['position']['token_amount'])):raise Blocked('with a position, only exact-or-smaller NVDA exit approval is allowed')
            approve(spot,args.token,args.amount);return
        if Path('approval_pending.json').exists():raise Blocked('resolve approval journal before trading')
        if args.command=='recover' and args.tx_hash:
            state=read('live_state.json')
            if not state['pending']:raise Blocked('no pending submit')
            if state['pending'].get('txHash') and state['pending']['txHash'].lower()!=args.tx_hash.lower():raise Blocked('cannot replace known hash')
            state['pending']['txHash']=args.tx_hash;write('live_state.json',state)
        runtime=Runtime(c,spot,live=args.command!='dry')
        if args.command=='recover':event(status='RECOVERY_CHECK_COMPLETE',halt=runtime.s['halt']);return
        if runtime.live:spot.signer()
        runtime.run(calibrate=args.command=='calibrate',rounds=args.rounds)

if __name__=='__main__':
    try:main()
    except (Blocked,KeyError,ValueError,OSError) as error:
        if isinstance(error,Blocked):raise SystemExit('BLOCKED: '+str(error))
        raise SystemExit('BLOCKED: invalid configuration or local state') from None
