"""Bounded causal maker-fill markouts from fresh, timestamped BBO observations."""
from collections import deque
from decimal import Decimal as D
from .models import BPS

HORIZONS=(1,3,5,10,30)

def spread_bucket(value):
    if value is None: return "unknown"
    value=D(str(value))
    return "0-1" if value<1 else "1-2" if value<2 else "2-4" if value<4 else "4+"

class Markouts:
    def __init__(self,c,log):
        self.c,self.log=c,log
        self.books=deque(maxlen=20000)
        self.pending=deque()
        self.recent=deque(maxlen=2000)
        self.toxicity_rows={side:deque(maxlen=2000) for side in ("BUY","SELL")}
        self.completed=self.missing=self.skipped=0
        self.last_stamp=-1

    def observe(self,book):
        if book.timestamp_us<=self.last_stamp: return
        self.last_stamp=book.timestamp_us
        self.books.append(book)
        cutoff=book.timestamp_us-40000000
        while self.books and self.books[0].timestamp_us<cutoff:self.books.popleft()
        kept=deque()
        for fill in self.pending:
            self._settle(fill,book)
            if fill["remaining"]:kept.append(fill)
        self.pending=kept

    def add_fill(self,fill):
        if not fill.get("maker"): return
        stamp=int(fill["timestamp_us"])
        reference=next((b for b in reversed(self.books) if b.timestamp_us<=stamp),None)
        if reference is None or stamp-reference.timestamp_us>self.c.markout_max_lag_seconds*1000000:
            self.skipped+=1
            self.log("markout_skipped",trade_id=fill["trade_id"],reason="no_fresh_prefill_reference")
            return
        item=dict(fill,reference_mid=reference.mid,spread_bucket=spread_bucket(fill.get("spread_bps")),remaining=set(HORIZONS))
        # Late fill notifications can still use already-observed valid future frames.
        for book in self.books:
            if book.timestamp_us>=stamp+1000000:self._settle(item,book)
        if item["remaining"]:
            if len(self.pending)>=1000:self._missing(self.pending.popleft(),"pending_bound")
            self.pending.append(item)

    def _settle(self,fill,book):
        for horizon in sorted(tuple(fill["remaining"])):
            target=fill["timestamp_us"]+horizon*1000000
            if book.timestamp_us<target:continue
            lag=(book.timestamp_us-target)/1000000
            fill["remaining"].remove(horizon)
            if lag>self.c.markout_max_lag_seconds:
                self.missing+=1
                self.log("markout_missing",trade_id=fill["trade_id"],side=fill["side"],horizon_seconds=horizon,reason="sample_too_late",sample_lag_seconds=lag)
                continue
            price=D(str(fill["price"]));qty=D(str(fill["quantity"]));fee=D(str(fill["fee"]))
            sign=D(1) if fill["side"]=="BUY" else D(-1)
            gross=sign*(book.mid/price-1)*BPS
            move=sign*(book.mid/fill["reference_mid"]-1)*BPS
            row=dict(trade_id=fill["trade_id"],side=fill["side"],horizon_seconds=horizon,
                fill_timestamp_us=fill["timestamp_us"],sample_timestamp_us=book.timestamp_us,
                sample_lag_seconds=lag,reference_mid=fill["reference_mid"],sample_mid=book.mid,
                price=price,quantity=qty,notional_usd=price*qty,spread_bucket=fill["spread_bucket"],
                markout_bps=gross,mid_move_bps=move,net_markout_bps=gross-fee/(price*qty)*BPS,
                gross_markout_usd=sign*(book.mid-price)*qty,net_markout_usd=sign*(book.mid-price)*qty-fee)
            self.completed+=1
            observed=dict(row,received=book.received)
            self.recent.append(observed)
            if horizon==5:self.toxicity_rows[fill["side"]].append(observed)
            self.log("markout",**row)

    def _missing(self,fill,reason):
        for horizon in sorted(fill["remaining"]):
            self.missing+=1
            self.log("markout_missing",trade_id=fill["trade_id"],side=fill["side"],horizon_seconds=horizon,reason=reason)
        fill["remaining"].clear()

    def finish(self):
        for fill in self.pending:self._missing(fill,"session_closed")
        self.pending.clear()

    def toxicity(self,now):
        result={}
        for side in ("BUY","SELL"):
            rows=[r for r in self.toxicity_rows[side] if 0<=now-r["received"]<=self.c.toxicity_window_seconds]
            notional=sum((r["notional_usd"] for r in rows),D(0))
            mean=sum((r["mid_move_bps"]*r["notional_usd"] for r in rows),D(0))/notional if notional else None
            ready=len(rows)>=self.c.toxicity_min_samples
            adverse=max(D(0),-mean-D(self.c.toxicity_threshold_bps)) if ready else D(0)
            result[side]=dict(samples=len(rows),ready=ready,mid_move_bps=mean,adverse_bps=adverse)
        return result

    def snapshot(self,now):
        return dict(completed=self.completed,missing=self.missing,skipped=self.skipped,pending=len(self.pending),toxicity=self.toxicity(now))
