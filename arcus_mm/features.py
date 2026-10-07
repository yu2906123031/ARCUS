"""Timestamp-normalized multi-scale variance of fresh BBO mid returns."""
import math
from decimal import Decimal as D
from .models import BPS

class EWMAVolatility:
    def __init__(self):
        self.last=None
        self.variance={2:D(0),10:D(0),60:D(0)}
        self.updates=0
    def observe(self,book,max_gap):
        if self.last is None:
            self.last=book;return
        dt=(book.timestamp_us-self.last.timestamp_us)/1000000
        if dt<=0:return
        gap=book.received-self.last.received
        if dt>max_gap or gap>max_gap:
            self.variance={h:D(0) for h in self.variance};self.updates=0
            self.last=book;return
        # Aggregate ultra-short frames; never multiply burst jitter by 1/dt.
        if dt<.05:return
        move=(book.mid/self.last.mid-1)*BPS
        rate=move*move/D(str(dt))
        for half in self.variance:
            decay=D(str(math.exp(-math.log(2)*dt/half)))
            self.variance[half]=decay*self.variance[half]+(1-decay)*rate
        self.last=book;self.updates+=1
    def snapshot(self):return {str(h):v.sqrt() for h,v in self.variance.items()}
