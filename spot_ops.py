"""Read-only readiness and explicit, reconciled daily rollover."""
import copy
import hashlib
import json
import os
from datetime import date
from pathlib import Path
from decimal import Decimal as D
from arcus import Blocked, ReconcileError, now, read, write, validate
from spot_api import addr, human


def doctor(c):
    checks={}
    try:
        validate({**c,'dry_run':True});checks['risk_config']=True
    except (Blocked,KeyError,ValueError):checks['risk_config']=False
    try:
        addr(os.getenv('ARCUS_WALLET_ADDRESS',''));checks['public_wallet']=True
    except Blocked:checks['public_wallet']=False
    checks['one_local_signer']=sum(bool(os.getenv(k)) for k in ('ARCUS_PRIVATE_KEY','ARCUS_MNEMONIC'))==1
    checks['quote_fees_accepted']=c.get('accept_quote_fees') is True
    checks['calibration_cap_configured']=c.get('calibration_max_loss_bps') is not None
    checks['dry_threshold_configured']=c.get('dry_run_round_trip_bps') is not None
    today=now().date().isoformat()
    try:
        row=read('calendar.json')['sessions'].get(today,{})
        checks['today_calendar_verified']=row.get('verified') is True and 'NVDA' in row.get('earnings_clear_symbols',[])
    except (OSError,KeyError,ValueError):checks['today_calendar_verified']=False
    checks['no_approval_pending']=not Path('approval_pending.json').exists()
    if Path('live_state.json').exists():
        try:
            s=read('live_state.json')
            checks['no_trade_pending']=not bool(s['pending'])
            checks['no_entry_halt']=not s['halt']
            checks['state_date_current']=s['date']==today
            checks['real_calibration_complete']=3<=len(s['calibration'])<=5 and all(r['date_et']==today for r in s['calibration'])
        except (KeyError,ValueError):checks['state_valid']=False
    else:
        checks.update(no_trade_pending=True,no_entry_halt=True,state_date_current=True,real_calibration_complete=False)
    return {'read_only':True,'network_checked':False,'date_et':today,'checks':checks,
            'missing':[key for key,value in checks.items() if not value],
            'note':'Local configuration only. Preflight must verify RPC, balances, allowance, and pending transactions.'}


def new_day(c,spot,path=Path('live_state.json')):
    """Never reset on the same ET day or with uncertain holdings/submissions."""
    validate({**c,'dry_run':True})
    if not path.exists():raise Blocked('no prior live state to roll over')
    old=read(path)
    previous=date.fromisoformat(old['date'])
    current=now().date()
    if previous>=current:raise Blocked('new-day only after a completed earlier ET day')
    if old['position'] or old['pending'] or Path('approval_pending.json').exists():raise ReconcileError('flatten and resolve every pending transaction before new-day')
    if old['wallet']!=spot.wallet or old['contracts']!=spot.contracts:raise ReconcileError('wallet/deployment mismatch')
    if D(c['capital'])!=D(old['config']['capital']):raise Blocked('capital changes need separate accounting; no automatic reset')
    spot.verify_chain()
    balances=spot.snapshot()
    if any(balances[token] for token in ('NVDA','SPY','QQQ')):raise ReconcileError('wallet not flat')
    for token in ('USDG','NVDA','SPY','QQQ'):
        if balances[token]!=old['balances'][token]:raise ReconcileError('balances differ from previous ledger')
    if human(balances['eth'],18)<D(c['min_eth_balance']):raise Blocked('ETH reserve insufficient')
    if D(old['cash'])<D(c['notional']):raise Blocked('remaining capital cannot fund one round')
    # Persist immutable previous-day evidence before replacing active state.
    encoded=json.dumps(old,sort_keys=True)
    digest=hashlib.sha256(encoded.encode()).hexdigest()
    folder=path.parent/'state_archive';folder.mkdir(exist_ok=True)
    archive=folder/(previous.isoformat()+'-'+digest[:16]+'.json')
    if archive.exists() and read(archive)!=old:raise Blocked('archive collision')
    if not archive.exists():write(archive,old)
    new=copy.deepcopy(old)
    new.update(date=current.isoformat(),config=copy.deepcopy(c),fingerprint=hashlib.sha256(json.dumps(c,sort_keys=True).encode()).hexdigest(),
        balances=balances,pnl='0',rounds=0,failures=0,bad_exits=0,last_px=None,last_sell_px=None,halt=False,
        trades=[],calibration=[],dry_verified=False,round_trip_bps=None,
        lifetime_pnl=str(D(old.get('lifetime_pnl','0'))+D(old['pnl'])),previous_archive=str(archive))
    # cash remains the real remaining budget; never refill to configured principal.
    write(path,new)
    return {'status':'NEW_DAY_READY_FOR_CALIBRATION','date_et':new['date'],'remaining_usdg':new['cash'],'archive':str(archive)}
