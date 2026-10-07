from decimal import Decimal as D
from .models import BPS, Quote

def targets(c, market, book, equity, position):
    """Reserve each side's worst-case fills separately; never net open orders."""
    if equity <= 0: return []
    cap = min(D(c.max_position_equity_fraction),D(c.leverage_cap))*equity/book.mid
    over = abs(position) >= cap
    buy_budget, sell_budget = max(D(0),cap-position), max(D(0),cap+position)
    center = book.mid*(1+D(c.bias_bps)/BPS)
    result, used = [], set()
    for level in range(1,3 if c.strategy == "grid" else 2):
        for side in ("BUY","SELL"):
            if over and ((position>0 and side=="BUY") or (position<0 and side=="SELL")): continue
            distance = D(c.spread_bps)*level/BPS
            px = market.price(center*(1-distance if side=="BUY" else 1+distance),side)
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
