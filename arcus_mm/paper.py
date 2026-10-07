import time
import uuid
from decimal import Decimal as D
from .models import BPS, Order

class Paper:
    def __init__(self,c,market,ledger,log,maker_fee,taker_fee):
        self.c,self.market,self.ledger,self.log = c,market,ledger,log
        self.maker_fee,self.taker_fee = maker_fee,taker_fee
        self.orders,self.seen = {},set()
    async def place(self,quote,book,ioc=False):
        if ioc:
            self.log("order",side=quote.side,price=quote.price,quantity=quote.qty,reduce_only=True,tif="IOC")
            px = book.ask if quote.side=="BUY" else book.bid
            px *= 1+D(self.c.paper_exit_slippage_bps)/BPS if quote.side=="BUY" else 1-D(self.c.paper_exit_slippage_bps)/BPS
            if (quote.side=="BUY" and px>quote.price) or (quote.side=="SELL" and px<quote.price): return None
            qty = self.market.quantity(min(quote.qty,abs(self.ledger.qty),book.ask_size if quote.side=="BUY" else book.bid_size))
            if qty: self.fill(quote,qty,px,False)
            return None
        if (quote.side=="BUY" and quote.price>=book.ask) or (quote.side=="SELL" and quote.price<=book.bid): raise ValueError("post-only would cross")
        o = Order(quote,"mm-"+uuid.uuid4().hex[:28],quote.qty,time.monotonic(),status="OPEN")
        self.orders[quote.slot] = o
        self.log("order",side=quote.side,price=quote.price,quantity=quote.qty,client_id=o.client_id,reduce_only=quote.reduce,tif="ALO")
        return o
    async def cancel(self,slot):
        o = self.orders.pop(slot,None)
        if o: self.log("cancel",client_id=o.client_id)
    async def cancel_all(self):
        for slot in list(self.orders): await self.cancel(slot)
    def fill(self,quote,qty,px,maker):
        fee = qty*px*(self.maker_fee if maker else self.taker_fee)
        self.ledger.fill(quote.side,qty,px,fee,maker)
        self.log("fill",side=quote.side,quantity=qty,price=px,fee=fee,maker=maker,position=self.ledger.qty,realized_pnl=self.ledger.realized)
    def trades(self,rows,book):
        # No taker-side field exists in the official public-trades schema.
        # Strict trade-through plus opposite BBO avoids invented fills.
        # https://docs.arcus.xyz/api-reference/market-data/trades
        for row in rows:
            key = str(row["tradeId"])
            if key in self.seen: continue
            self.seen.add(key)
            if len(self.seen)>100000: raise RuntimeError("paper trade-id bound reached; start a fresh session")
            available,trade_px,stamp = D(row["size"]),D(row["price"]),int(row["timestamp"])
            if stamp<book.timestamp_us or stamp-book.timestamp_us>1000000: continue
            for slot,o in sorted(list(self.orders.items()),key=lambda x:x[1].created):
                if available<=0: break
                if time.monotonic()-o.created<self.c.paper_latency_ms/1000: continue
                buy = o.quote.side=="BUY"
                eligible = (trade_px<o.quote.price and book.ask<=o.quote.price) if buy else (trade_px>o.quote.price and book.bid>=o.quote.price)
                if not eligible: continue
                qty = self.market.quantity(min(o.remaining,available))
                if o.quote.reduce:
                    if (buy and self.ledger.qty>=0) or (not buy and self.ledger.qty<=0): continue
                    qty = min(qty,abs(self.ledger.qty))
                if qty<=0: continue
                # Limit execution is equal/worse than contemporaneous opponent BBO.
                self.fill(o.quote,qty,o.quote.price,True)
                available -= qty; o.remaining -= qty
                if not o.remaining: self.orders.pop(slot,None)
