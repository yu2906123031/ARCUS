from decimal import Decimal as D
from collections import deque
from dataclasses import replace
from .models import BPS, Quote

class QuotePolicy:
    """One-second BBO samples; bounded 30-second RMS return estimate."""
    def __init__(self):
        self.samples=deque(maxlen=31)
        self.state={}

    def effective(self,c,book,maker_fee=D(0),taker_fee=D(0)):
        now=book.received
        while self.samples and now-self.samples[0][0]>30: self.samples.popleft()
        if not self.samples or now-self.samples[-1][0]>=1:
            self.samples.append((now,book.mid))
        moves=[(b[1]/a[1]-1)*BPS for a,b in zip(self.samples,list(self.samples)[1:])]
        volatility=(sum((x*x for x in moves),D(0))/D(len(moves))).sqrt() if len(moves)>=4 else D(0)
        spread=D(c.spread_bps)
        if c.adaptive_spread:
            spread=min(D(c.max_spread_bps),max(spread,(book.ask-book.bid)/book.mid*BPS/2,volatility*D(c.volatility_multiplier)))
        # Rebates never narrow quotes; funding is not inferred from price moves.
        fee_floor=(max(D(0),maker_fee)+max(D(0),taker_fee)*D(c.exit_fee_reserve_fraction))*BPS
        spread=max(spread,fee_floor)
        reprice=min(D(c.max_reprice_bps),D(c.reprice_bps)*spread/D(c.spread_bps)) if c.adaptive_spread else D(c.reprice_bps)
        strategy="mid" if c.adaptive_spread and spread>=D(c.spread_bps)*2 else c.strategy
        self.state=dict(spread_bps=spread,reprice_bps=reprice,volatility_bps=volatility,
                        fee_floor_bps=fee_floor,samples=len(self.samples),strategy=strategy)
        return replace(c,spread_bps=str(spread),reprice_bps=str(reprice),strategy=strategy)


def targets(c, market, book, equity, position):
    """Reserve each side's worst-case fills separately; never net open orders."""
    if equity <= 0: return []
    cap = min(D(c.max_position_equity_fraction),D(c.leverage_cap))*equity/book.mid
    over = abs(position) >= cap
    buy_budget, sell_budget = max(D(0),cap-position), max(D(0),cap+position)
    inventory_ratio=max(D(-1),min(D(1),position/cap))
    center = book.mid*(1+(D(c.bias_bps)-D(c.inventory_skew_bps)*inventory_ratio)/BPS)
    result, used = [], set()
    for level in range(1,3 if c.strategy == "grid" else 2):
        for side in ("BUY","SELL"):
            if over and ((position>0 and side=="BUY") or (position<0 and side=="SELL")): continue
            distance = D(c.spread_bps)*level/BPS
            px = market.price(center*(1-distance if side=="BUY" else 1+distance),side)
            # An inventory shift must not remove the reducing side by crossing BBO.
            reducing=(position>0 and side=="SELL") or (position<0 and side=="BUY")
            if reducing and ((side=="BUY" and px>=book.ask) or (side=="SELL" and px<=book.bid)):
                px=market.price(book.bid if side=="BUY" else book.ask,side)
            if px<=0 or (side=="BUY" and px>=book.ask) or (side=="SELL" and px<=book.bid) or (side,px) in used: continue
            budget = buy_budget if side=="BUY" else sell_budget
            if over: budget = min(budget,abs(position)-sum(q.qty for q in result))
            qty = market.quantity(min(equity*D(c.order_equity_fraction)/px,budget))
            if qty<market.min_size or (not over and qty*px<market.min_notional): continue
            result.append(Quote(f"{side}-{level}",side,px,qty,over)); used.add((side,px))
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
