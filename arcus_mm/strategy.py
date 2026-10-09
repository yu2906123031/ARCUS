from decimal import Decimal as D
from collections import deque
from dataclasses import replace
from datetime import datetime, time
import secrets
from zoneinfo import ZoneInfo
from .models import BPS, Quote
from .features import EWMAVolatility

def fair_value(c,book):
    total=book.bid_size+book.ask_size
    imbalance=(book.bid_size-book.ask_size)/total
    microprice=(book.ask*book.bid_size+book.bid*book.ask_size)/total
    raw=(microprice/book.mid-1)*BPS*D(c.microprice_weight)
    bound=D(c.max_fair_shift_bps)
    shift=max(-bound,min(bound,raw))
    return dict(microprice=microprice,l1_imbalance=imbalance,fair_shift_bps=shift,
                fair_price=book.mid*(1+shift/BPS))

def inventory_shift(c,ratio):
    ratio=max(D(-1),min(D(1),ratio))
    return -D(c.inventory_skew_bps)*ratio-D(c.inventory_cubic_bps)*ratio**3

class QuotePolicy:
    """Causal RMS/EWMA volatility, side-specific toxicity, and slow spread decay."""
    def __init__(self,clock=None):
        self.samples=deque(maxlen=31)
        self.state={}
        self.ewma=EWMAVolatility()
        self.last_spread_time=None
        self.side_spreads={}
        self.micro_direction=0
        self.micro_since=0.
        self.micro_last_frame=-1.
        self.micro_confirmed=0
        self.clock=clock or (lambda:datetime.now(ZoneInfo("America/New_York")))

    def session_base(self,c):
        if not c.session_spread_enabled or c.market!="SPY-USD": return D(c.spread_bps),"DISABLED"
        now=self.clock().astimezone(ZoneInfo("America/New_York"))
        if now.weekday()>=5:return D(c.session_off_hours_spread_bps),"OFF_HOURS"
        current=now.time().replace(tzinfo=None)
        if time(9,25)<=current<time(10):return D(c.session_open_spread_bps),"OPEN"
        if time(10)<=current<time(15,45):return D(c.session_regular_spread_bps),"REGULAR"
        if time(15,45)<=current<time(16,5):return D(c.session_close_spread_bps),"CLOSE"
        if time(4)<=current<time(9,25) or time(16,5)<=current<time(20):
            return D(c.session_extended_spread_bps),"EXTENDED"
        return D(c.session_off_hours_spread_bps),"OFF_HOURS"

    def observe(self,c,book):
        self.ewma.observe(book,c.disconnect_seconds)

    def confirmed_microprice_config(self,c,book):
        shift=fair_value(c,book)["fair_shift_bps"]
        direction=1 if shift>0 else -1 if shift<0 else 0
        stamp=float(book.received)
        if stamp!=self.micro_last_frame:
            if direction==0 or direction!=self.micro_direction or (self.micro_last_frame>=0 and stamp-self.micro_last_frame>c.disconnect_seconds):
                self.micro_direction=direction
                self.micro_since=stamp
                self.micro_confirmed=0
            elif stamp-self.micro_since>=c.microprice_confirm_seconds:
                self.micro_confirmed=direction
            self.micro_last_frame=stamp
        enabled=bool(direction and direction==self.micro_confirmed)
        return replace(c,microprice_weight=c.microprice_weight if enabled else "0")

    def effective(self,c,book,maker_fee=D(0),taker_fee=D(0),toxicity=None):
        self.observe(c,book)
        now=book.received
        base,market_session=self.session_base(c)
        while self.samples and now-self.samples[0][0]>30: self.samples.popleft()
        if not self.samples or now-self.samples[-1][0]>=1:
            self.samples.append((now,book.mid))
        moves=[(b[1]/a[1]-1)*BPS for a,b in zip(self.samples,list(self.samples)[1:])]
        anchors=[sample for sample in self.samples if now-sample[0]>=c.momentum_window_seconds]
        anchor=anchors[-1][1] if anchors else None
        momentum_bps=(book.mid/anchor-1)*BPS if anchor else D(0)
        momentum_threshold=(D(c.session_open_momentum_threshold_bps) if market_session=="OPEN"
                            else D(c.momentum_threshold_bps))
        blocked_sides=[]
        if c.momentum_filter_enabled and abs(momentum_bps)>=momentum_threshold:
            blocked_sides=["SELL" if momentum_bps>0 else "BUY"]
        volatility=(sum((x*x for x in moves),D(0))/D(len(moves))).sqrt() if len(moves)>=4 else D(0)
        ewma=self.ewma.snapshot()
        if c.volatility_mode=="ewma":volatility=max(ewma.values())
        fee_floor=(max(D(0),maker_fee)+max(D(0),taker_fee)*D(c.exit_fee_reserve_fraction))*BPS
        book_half=(book.ask-book.bid)/book.mid*BPS/2
        vol_premium=volatility*D(c.volatility_multiplier) if c.adaptive_spread else D(0)
        if c.adaptive_spread:
            if c.volatility_mode=="ewma":spread=max(base,fee_floor)+vol_premium
            else:spread=max(base,vol_premium)
            spread=min(D(c.max_spread_bps),max(spread,book_half))
        else:spread=base
        spread=max(spread,fee_floor)
        elapsed=max(0,now-self.last_spread_time) if self.last_spread_time is not None else 0
        if elapsed>c.disconnect_seconds:self.side_spreads={}
        premiums={};counts={};sides={}
        for side in ("BUY","SELL"):
            quality=(toxicity or {}).get(side,{})
            counts[side]=quality.get("samples",0)
            premium=min(D(c.toxicity_max_premium_bps),D(quality.get("adverse_bps",0))*D(c.toxicity_multiplier)) if c.toxicity_enabled and quality.get("ready") else D(0)
            premiums[side]=premium
            target=max(fee_floor,min(D(c.max_spread_bps),spread+premium)) if premium else spread
            if D(c.spread_decay_bps_per_second)>0 and side in self.side_spreads:
                target=max(target,self.side_spreads[side]-D(str(elapsed))*D(c.spread_decay_bps_per_second))
            sides[side]=target
        self.side_spreads=sides;self.last_spread_time=now
        spread=max(sides.values())
        reprice=min(D(c.max_reprice_bps),D(c.reprice_bps)*spread/base) if c.adaptive_spread else D(c.reprice_bps)
        strategy="mid" if c.adaptive_spread and spread>=base*2 else c.strategy
        regime="TOXIC" if max(premiums.values())>0 else "FAST" if c.adaptive_spread and vol_premium>=base else "NORMAL"
        effective=self.confirmed_microprice_config(c,book)
        self.state=dict(spread_bps=spread,reprice_bps=reprice,volatility_bps=volatility,ewma_bps=ewma,
                        volatility_mode=c.volatility_mode,volatility_premium_bps=vol_premium,
                        side_spreads_bps=sides,toxicity_premiums_bps=premiums,toxicity_samples=counts,regime=regime,
                        fee_floor_bps=fee_floor,samples=len(self.samples),strategy=strategy,
                        microprice_direction=self.micro_direction,microprice_confirmed=self.micro_confirmed,
                        momentum_bps=momentum_bps,momentum_threshold_bps=momentum_threshold,
                        blocked_sides=blocked_sides,market_session=market_session,
                        session_base_spread_bps=base,
                        **fair_value(effective,book))
        return replace(effective,spread_bps=str(spread),reprice_bps=str(reprice),strategy=strategy)



def order_notional(c,equity):
    if c.order_notional_min is None:return equity*D(c.order_equity_fraction)
    assert c.order_notional_max is not None
    low,high=D(c.order_notional_min),D(c.order_notional_max)
    scale=D("0.01")
    low_cents=int((low/scale).to_integral_value(rounding="ROUND_CEILING"))
    high_cents=int((high/scale).to_integral_value(rounding="ROUND_FLOOR"))
    return D(low_cents+secrets.randbelow(high_cents-low_cents+1))*scale


def targets(c, market, book, equity, position, side_spreads=None, funding_reduce_bps=D(0), blocked_sides=None):
    """Reserve each side's worst-case fills separately; never net open orders."""
    if equity <= 0: return []
    cap = min(D(c.max_position_equity_fraction),D(c.leverage_cap))*equity/book.mid
    over = abs(position) >= cap
    inventory_exit = abs(position) >= cap * D("0.5")
    inventory_one_sided = abs(position) >= cap * D(c.inventory_one_sided_ratio)
    buy_budget, sell_budget = max(D(0),cap-position), max(D(0),cap+position)
    inventory_ratio=max(D(-1),min(D(1),position/cap))
    fair=fair_value(c,book)
    center = book.mid*(1+(fair["fair_shift_bps"]+D(c.bias_bps)+inventory_shift(c,inventory_ratio))/BPS)
    result, used = [], set()
    for level in range(1,3 if c.strategy == "grid" else 2):
        for side in ("BUY","SELL"):
            reducing=(position>0 and side=="SELL") or (position<0 and side=="BUY")
            if side in (blocked_sides or ()) and not reducing: continue
            if inventory_one_sided and position and not reducing: continue
            if inventory_exit and ((position>0 and side=="BUY") or (position<0 and side=="SELL")): continue
            half_spread=D((side_spreads or {}).get(side,c.spread_bps))
            distance = half_spread*level/BPS
            px = market.price(center*(1-distance if side=="BUY" else 1+distance),side)
            # An inventory shift must not remove the reducing side by crossing BBO.
            if reducing and inventory_exit:
                # Keep the inventory exit passive while moving it to the front of the BBO.
                px=market.price(min(book.bid+market.tick,book.ask-market.tick) if side=="BUY"
                                else max(book.ask-market.tick,book.bid+market.tick),side)
                if funding_reduce_bps:
                    move=px*D(funding_reduce_bps)/BPS
                    px=market.price(min(px+move,book.ask-market.tick) if side=="BUY"
                                    else max(px-move,book.bid+market.tick),side)
            if reducing and ((side=="BUY" and px>=book.ask) or (side=="SELL" and px<=book.bid)):
                px=market.price(book.bid if side=="BUY" else book.ask,side)
            if px<=0 or (side=="BUY" and px>=book.ask) or (side=="SELL" and px<=book.bid) or (side,px) in used: continue
            budget = buy_budget if side=="BUY" else sell_budget
            if inventory_exit: budget = min(budget,abs(position)-sum(q.qty for q in result))
            qty = market.quantity(min(order_notional(c,equity)/px,budget))
            if qty<market.min_size or (not over and qty*px<market.min_notional): continue
            result.append(Quote(f"{side}-{level}",side,px,qty,inventory_exit,half_spread)); used.add((side,px))
            if side=="BUY": buy_budget -= qty
            else: sell_budget -= qty
    if max((q.price for q in result if q.side=="BUY"),default=D(0)) >= min((q.price for q in result if q.side=="SELL"),default=D("Infinity")):
        raise ValueError("crossed strategy quotes")
    return result

def replace_needed(old,new,tick,reprice_bps="0"):
    threshold=max(tick,old.price*D(reprice_bps)/BPS)
    return old.side!=new.side or old.reduce!=new.reduce or abs(old.price-new.price)>threshold

def safe_resting(c,equity,position,book,orders):
    cap = equity*min(D(c.max_position_equity_fraction),D(c.leverage_cap))/book.mid
    buys = sum((o.remaining for o in orders if o.quote.side=="BUY" and not o.quote.reduce),D(0))
    sells = sum((o.remaining for o in orders if o.quote.side=="SELL" and not o.quote.reduce),D(0))
    if abs(position)>cap:
        return all(o.quote.reduce and ((position>0 and o.quote.side=="SELL") or (position<0 and o.quote.side=="BUY")) for o in orders)
    return position+buys<=cap and position-sells>=-cap
