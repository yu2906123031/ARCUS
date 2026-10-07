import asyncio
import json
import time
import uuid
from decimal import Decimal as D
import httpx
from websockets.asyncio.client import connect
from .models import Book, Market, Order, fmt, number
from .signing import Signer

TERMINAL = {"FILLED", "CANCELED", "REJECTED", "MARGIN_CANCELED", "TPSL_CANCELED", "LIQUIDATED", "ADL"}

class ExchangeError(RuntimeError): pass
class UncertainOrder(ExchangeError): pass
class RateLimited(ExchangeError): pass
class ClockUnavailable(ExchangeError): pass

def daily_protection_quota(payload,server_seconds):
    """Use the server UTC day; never echo an arbitrary error response."""
    error=payload.get("error","") if isinstance(payload,dict) else ""
    if isinstance(error,str) and "schedule cancel trigger limit reached" in error.lower() and "utc day" in error.lower():
        reset=(int(server_seconds)//86400+1)*86400
        return max(0,reset-server_seconds)+5,reset
    return None


class PublicAPI:
    def __init__(self,c,log):
        self.c,self.log=c,log
        self.http=httpx.AsyncClient(base_url=c.api_url,timeout=8,follow_redirects=False)
        self.book=None
        self.connected=False
        self.ready=set()
        self.error=None
        self.offset_ns=0
        self.clock_checked=0.0
        self.last_stamp=0
        self.on_trades=lambda rows,book: None
        self.on_private=lambda msg: None
        self.on_disconnect=lambda: None
        self.market=None
        self.signer=None
        self.cooldown=0.0
        self.clock_fault=False
        self.clock_pending=False
        self.clock_anchor=None
        self.channels={"bbo","trades"}

    async def get(self,path,params=None):
        # Retry reads only. Never automatically resend signed mutations.
        for attempt in range(3):
            try:
                r=await self.http.get(path,params=params)
                r.raise_for_status()
                return r.json()
            except httpx.TransportError:
                self.log("read_retry",attempt=attempt+1,reason="transport_error")
                if attempt==2:
                    raise ExchangeError("read-only request unavailable after 3 attempts") from None
                await asyncio.sleep(.25*(attempt+1))
            except httpx.HTTPStatusError as exc:
                raise ExchangeError("read-only request HTTP "+str(exc.response.status_code)) from None

    async def metadata(self):
        rows=(await self.get("/v1/markets"))["markets"]
        row=next(x for x in rows if x["marketDisplayName"]==self.c.market)
        self.market=Market.parse(row)
        tier=next(x for x in (await self.get("/v1/feetiers"))["tiers"] if x["level"]==0)
        maker,taker=number(tier["maker_fee_ppm"])/D(1000000),number(tier["taker_fee_ppm"])/D(1000000)
        if not -D("0.01")<maker<D("0.01") or not 0<=taker<D("0.01"): raise ValueError("invalid fee table")
        return self.market,maker,taker

    async def clock(self):
        # Slow/asymmetric HTTP trips are not evidence of local clock drift.
        # Use the lowest-RTT valid sample; never widen the skew threshold.
        # https://docs.arcus.xyz/api-reference/public/get-current-server-time
        self.clock_pending = self.clock_pending or not self.clock_checked or time.monotonic()-self.clock_checked>=45
        samples=[]
        for attempt in range(self.c.clock_samples):
            before=time.time_ns()
            mono_before=time.monotonic_ns()
            try:
                raw=await asyncio.wait_for(self.get("/v1/time"),timeout=8) # Complete cold TLS connections; still reject slow RTT samples.
            except (httpx.TransportError, httpx.HTTPStatusError, asyncio.TimeoutError, ExchangeError):
                self.log("clock_sample_discarded",sample=attempt+1,reason="request_timeout_or_error")
                continue
            after=time.time_ns()
            rtt=time.monotonic_ns()-mono_before
            if abs((after-before)-rtt)>100000000:
                self.clock_fault=True
                raise ExchangeError("local wall clock jumped during synchronization")
            ns=int(raw["timeNs"])
            if rtt>self.c.clock_max_rtt_ms*1000000:
                self.log("clock_sample_discarded",sample=attempt+1,reason="high_rtt",rtt_ms=rtt/1000000)
                continue
            samples.append((rtt,ns-(before+after)//2,after,mono_before+rtt))
        if not samples:
            self.clock_pending=True
            # Keep the last successful offset, but block all new quotes until
            # a reliable sample succeeds. The disconnect watchdog still stops
            # the session if this condition persists beyond N seconds.
            self.log("clock_unavailable",samples=self.c.clock_samples)
            raise ClockUnavailable("no reliable server-time sample; new quotes paused")
        rtt,offset,anchor_wall,anchor_mono=min(samples,key=lambda sample:sample[0])
        uncertainty=rtt//2
        self.log("clock",offset_ms=offset/1000000,uncertainty_ms=uncertainty/1000000,
                 valid_samples=len(samples),sample_count=self.c.clock_samples)
        if abs(offset)+uncertainty>self.c.max_clock_skew_ms*1000000:
            self.clock_fault=True
            raise ExchangeError("clock skew exceeds configured bound with a reliable sample")
        self.offset_ns=offset
        self.clock_checked=time.monotonic()
        self.clock_anchor=(anchor_wall,anchor_mono)
        self.clock_pending=False

    def stamp(self):
        stamp=max(time.time_ns()+self.offset_ns,self.last_stamp+1)
        self.last_stamp=stamp
        return stamp

    def check_wall_clock(self):
        if self.clock_anchor is not None:
            wall,mono=self.clock_anchor
            if abs((time.time_ns()-wall)-(time.monotonic_ns()-mono))>100000000:
                self.clock_fault=True
        return not self.clock_fault

    def fresh(self):
        self.check_wall_clock()
        if self.clock_fault or self.clock_pending or not self.connected or not self.channels.issubset(self.ready) or not self.book: return False
        wall=(time.time_ns()+self.offset_ns)/1e9
        age=wall-self.book.timestamp_us/1e6
        return (0<=time.monotonic()-self.book.received<self.c.disconnect_seconds and
                -self.c.max_clock_skew_ms/1000<=age<self.c.disconnect_seconds and
                time.monotonic()-self.clock_checked<45)

    async def stream(self):
        while True:
            try:
                async with connect(self.c.ws_url,ping_interval=15,ping_timeout=10,open_timeout=8,max_queue=1024) as ws:
                    self.connected=True; self.ready.clear(); self.book=None
                    for channel in ("bbo","trades"):
                        await ws.send(json.dumps(dict(type="subscribe",channel=channel,id=self.c.market)))
                    if self.signer:
                        for channel in ("orders","userFills"):
                            await ws.send(json.dumps(dict(type="subscribe",channel=channel,id=self.signer.address,
                                accountIndex=self.signer.account_index,market=self.c.market)))
                    self.log("ws_connected")
                    async for raw in ws:
                        msg=json.loads(raw)
                        if msg.get("type") in ("error","degraded"):
                            raise ExchangeError("WebSocket error/degraded channel")
                        channel=msg.get("channel")
                        if channel not in self.channels: continue
                        if channel in ("orders","userFills"):
                            if msg.get("id","").lower()!=self.signer.address or msg.get("accountIndex")!=self.signer.account_index:
                                continue
                        elif msg.get("id")!=self.c.market: continue
                        if msg.get("type")=="subscribed": self.ready.add(channel)
                        if "contents" not in msg: continue
                        if channel=="bbo":
                            b=Book.parse(msg["contents"])
                            if self.book and b.timestamp_us<self.book.timestamp_us: continue
                            self.book=b
                        elif channel=="trades":
                            if self.book and self.fresh(): self.on_trades(msg["contents"],self.book)
                        else: self.on_private(msg)
            except asyncio.CancelledError: raise
            except Exception as exc:
                self.error=type(exc).__name__
                self.log("ws_disconnected",reason=self.error)
            finally:
                self.connected=False; self.ready.clear()
                self.on_disconnect()
            await asyncio.sleep(1)

class Live:
    def __init__(self,api,ledger,log):
        self.api,self.c,self.ledger,self.log=api,api.c,ledger,log
        self.signer=Signer(); api.signer=self.signer
        api.channels |= {"orders","userFills"}
        api.on_private=self.event
        self.orders={}; self.history={}; self.seen=set(); self.fill_journal={}
        self.fill_cursor=time.time_ns()//1000
        self.last_fill_time=-1
        self.started_us=self.api.stamp()//1000
        self.fill_cursor=self.started_us
        self.failures=0
        self.fatal=None
        self.recovering=False
        self.equity=D(0)
        self.account_time=0.0
        self.last_account_seq=-1
        self.account_qty=D(0)
        self.last_fill_seq=-1
        self.arm_time=0.0
        self.free_collateral=D(0)
        self.resume_info=None
        self.leverage_confirmed=False
        self.protection_cooldown=0.0
        self.protection_block_reason=None
        self.protection_retry_at_ms=None

    def scope(self): return dict(address=self.signer.address,accountIndex=self.signer.account_index)

    async def post(self,action,body):
        if action=="scheduleCancel" and body.get("time") is not None and time.monotonic()<self.protection_cooldown:
            raise RateLimited("daily service protection quota exhausted")
        if time.monotonic()<self.api.cooldown: raise RateLimited("rate-limit cooldown")
        stamp=self.api.stamp()
        if action in ("placeOrder","cancelOrder"):
            body=dict(body,timestamp=stamp)
            if action=="placeOrder": body["clientTime"]=str(stamp)
        headers=self.signer.headers(action,body,stamp,self.api.market)
        for attempt in range(2):
            try:
                r=await self.api.http.post("/v1/"+action,params={"address":self.signer.address},json=body,headers=headers)
                break
            except (httpx.ConnectError,httpx.ConnectTimeout,httpx.PoolTimeout) as exc:
                # These fail before sending the HTTP request; retrying cannot
                # duplicate an order. Read/write failures follow verification.
                self.log("mutation_not_sent",action=action,error_type=type(exc).__name__,attempt=attempt+1)
                if attempt==1:
                    raise ExchangeError("mutation connection failed before sending: "+action) from None
                await asyncio.sleep(.25)
            except httpx.TransportError as exc:
                recovered=await self.recover_mutation(action,body,type(exc).__name__)
                if recovered is not None: return recovered
                self.fatal="uncertain mutation transport result: "+action
                raise UncertainOrder(self.fatal) from None
        if r.status_code==429:
            try: payload=r.json()
            except ValueError: payload={}
            if not isinstance(payload,dict): payload={}
            def delay(value,scale=1):
                try:
                    parsed=float(value)/scale
                    return parsed if 0<=parsed<float("inf") else 5
                except (ValueError,TypeError): return 5
            wait=max(5,delay(r.headers.get("Retry-After",5)),delay(payload.get("retryAfterMs",5000),1000))
            self.api.cooldown=time.monotonic()+wait
            quota=daily_protection_quota(payload,time.time()+self.api.offset_ns/1e9) if action=="scheduleCancel" else None
            if quota is not None:
                quota_wait,reset=quota
                self.protection_cooldown=time.monotonic()+quota_wait
                self.protection_block_reason="daily_trigger_limit"
                self.protection_retry_at_ms=int((reset+5)*1000)
                self.log("protection_quota_exhausted",reason="daily_trigger_limit",retry_seconds=quota_wait,
                         retry_at_ms=self.protection_retry_at_ms)
            else:
                self.log("rate_limit_pause",action=action,retry_seconds=wait)
            raise RateLimited("mutation HTTP 429: "+action)
        if r.status_code>=500:
            recovered=await self.recover_mutation(action,body,"HTTP_"+str(r.status_code))
            if recovered is not None: return recovered
            self.fatal="uncertain server mutation result: "+action
            raise UncertainOrder(self.fatal)
        if not r.is_success:
            # Do not log signed requests/headers or echo potentially sensitive responses.
            self.log("mutation_rejected",action=action,status_code=r.status_code)
            raise ExchangeError("mutation HTTP "+str(r.status_code)+": "+action)
        return r.json()

    async def recover_mutation(self,action,body,error_type):
        # Resolve the original request only. Never resend a possibly executed order.
        self.log("mutation_uncertain",action=action,error_type=error_type)
        if self.fatal or action not in ("placeOrder","cancelOrder"):
            return None
        cid=body.get("clientId")
        order=self.history.get(cid) if cid else next((o for o in self.history.values() if o.order_id==body.get("orderId")),None)
        if order is None: return None
        self.recovering=True
        deadline=time.monotonic()+self.c.confirmation_seconds
        try:
            terminal=action=="cancelOrder" or body.get("timeInForce")=="IOC"
            await self.confirmed(order,terminal=terminal)
            # Confirm cost basis and account after any fills that raced the request.
            while time.monotonic()<deadline:
                remaining=deadline-time.monotonic()
                if await asyncio.wait_for(self.reconcile(),timeout=remaining):
                    if self.fatal: return None
                    self.log("mutation_recovered",action=action,client_id=order.client_id,
                             order_id=order.order_id,status=order.status)
                    return {"orderId":order.order_id,"status":order.status}
                await asyncio.sleep(.1)
        except (ExchangeError,asyncio.TimeoutError):
            pass
        finally:
            self.recovering=False
        self.log("mutation_recovery_failed",action=action,client_id=order.client_id)
        return None

    async def preflight(self,resume=None,auto_resume_dir=None):
        keys=(await self.api.get("/v1/apiKeys",{"address":self.signer.address}))["apiKeys"]
        key=next((k for k in keys if k["apiKey"].removeprefix("0x").lower()==self.signer.public),None)
        if not key or key["status"]!="ACTIVE": raise ExchangeError("registered active API key not found")
        if not key.get("allSubaccounts",False) and key.get("accountIndex")!=self.signer.account_index:
            raise ExchangeError("API key subaccount mismatch")
        if key["validUntil"] and int(key["validUntil"])<=time.time_ns()//1000000: raise ExchangeError("expired API key")
        a=await self.api.get("/v1/account",self.scope())
        orders=(await self.api.get("/v1/openOrders",self.scope()))["orders"]
        if orders: raise ExchangeError("startup requires no open orders")
        if resume is None and auto_resume_dir is not None and any(number(p["size"]) for p in a["positions"].values()):
            from .recovery import latest_resume
            path,resume=latest_resume(auto_resume_dir,self.c.market,self.signer.account_index)
            self.log("auto_resume_selected",resume_log=str(path),market=resume.market,account_index=resume.account_index)
        if resume is None:
            if any(number(p["size"]) for p in a["positions"].values()):
                raise ExchangeError("startup requires a dedicated flat subaccount with no open orders")
        else:
            if resume.market!=self.c.market or resume.account_index!=self.signer.account_index:
                raise ExchangeError("resume log market/subaccount mismatch")
            if any(number(p["size"]) for m,p in a["positions"].items() if int(m)!=self.api.market.id):
                raise ExchangeError("resume cannot take over another market position")
            self.started_us=resume.since_us
            self.fill_cursor=resume.since_us
        self.equity=number(a["equity"])
        self.free_collateral=number(a["freeCollateral"])
        if self.equity<=0: raise ExchangeError("account has no positive equity")
        self.ledger.initial=min(self.equity,D(self.c.capital_cap)) if self.c.capital_cap is not None else self.equity
        if resume is not None:
            self.resume_info=dict(since_us=resume.since_us,initial_equity=resume.initial_equity)
            self.ledger.initial=number(resume.initial_equity)
            if self.c.capital_cap is not None and self.ledger.initial>D(self.c.capital_cap):
                raise ExchangeError("resume capital exceeds configured cap")
        synced=await self.reconcile()
        if not synced or self.fatal: raise ExchangeError("startup account/fills did not reconcile")
        self.log("preflight",equity=self.equity,initial_equity=self.ledger.initial,
                 resume_since_us=resume.since_us if resume is not None else None,
                 account_index=self.signer.account_index,live_orders_sent=False)

    async def enable(self):
        if not self.leverage_confirmed:
            body=dict(self.scope(),marketId=self.api.market.id,leverage=self.c.leverage_cap,isolated=False)
            await self.post("setLeverage",body)
            deadline=time.monotonic()+self.c.confirmation_seconds
            while True:
                rows=(await self.api.get("/v1/leverages",self.scope()))["leverages"]
                row=next(x for x in rows if int(x["marketId"])==self.api.market.id)
                if row["leverage"]==self.c.leverage_cap and row["marginMode"]=="CROSS": break
                if time.monotonic()>deadline: raise ExchangeError("leverage change not confirmed")
                await asyncio.sleep(.25)
            self.leverage_confirmed=True
        if self.c.server_protection:
            await self.arm()
        else:
            self.protection_cooldown=0.0
            self.protection_block_reason=None
            self.protection_retry_at_ms=None
            self.log("software_protection_enabled",disconnect_seconds=self.c.disconnect_seconds,
                     server_fallback=False)

    async def arm(self):
        if not self.c.server_protection: return
        # https://docs.arcus.xyz/api-reference/exchange/schedule-cancel-all-dead-mans-switch
        # Keep headroom above the server minimum after network transit.
        lead=self.c.protection_seconds
        sent_at=time.monotonic()
        await self.post("scheduleCancel",dict(self.scope(),marketId=self.api.market.id,time=(self.api.stamp()//1000)+int(lead*1000000)))
        self.arm_time=sent_at
        self.log("protection_armed",lead_seconds=lead,refresh_seconds=self.c.protection_refresh_seconds)
        self.protection_block_reason=None
        self.protection_retry_at_ms=None
        self.protection_cooldown=0.0

    async def disarm_if_clear(self):
        """Disarm only after REST and every local lifecycle prove no orders remain."""
        if not self.arm_time: return True
        def clear():
            return not self.fatal and not self.orders and all(o.status in TERMINAL for o in self.history.values())
        if not clear():
            self.log("protection_retained",reason="unconfirmed_orders_or_fault")
            return False
        try:
            rows=(await self.api.get("/v1/openOrders",self.scope()))["orders"]
            if rows or not clear():
                self.log("protection_retained",reason="open_orders_or_fault")
                return False
            response=await self.post("scheduleCancel",dict(self.scope(),marketId=self.api.market.id))
            if response.get("status")!="disarmed":
                self.log("protection_retained",reason="disarm_not_confirmed")
                return False
        except ExchangeError as exc:
            self.log("protection_retained",reason="disarm_not_confirmed",error_type=type(exc).__name__)
            return False
        self.arm_time=0
        self.log("protection_disarmed",orders_confirmed_clear=True)
        return True

    async def reconcile(self):
        a=await self.api.get("/v1/account",self.scope())
        rows=(await self.api.get("/v1/fills",dict(self.scope(),market=self.c.market,limit=1000,**{"from":self.fill_cursor})))["fills"]
        if len(rows)>=1000:
            self.fatal="fill catch-up window overflow; cannot prove complete ledger"
        for row in sorted(rows,key=lambda f:(int(f["createdAt"]),str(f["tradeId"]))): self.apply_fill(row)
        if rows: self.fill_cursor=max(self.started_us,max(int(f["createdAt"]) for f in rows)-30000000)
        seq=int(a["sequenceNumber"])
        if seq<self.last_account_seq: raise ExchangeError("account sequence regressed")
        self.last_account_seq=seq
        if any(number(p["size"]) for k,p in a["positions"].items() if int(k)!=self.api.market.id):
            self.fatal="unexpected position in another market"
        p=a["positions"].get(str(self.api.market.id))
        self.account_qty=number(p["size"]) if p else D(0)
        self.equity=number(a["equity"])
        self.free_collateral=number(a["freeCollateral"])
        self.account_time=time.monotonic()
        # Fill ledger and authoritative snapshot must converge before new quotes.
        return self.account_qty==self.ledger.qty and seq>=self.last_fill_seq

    def event(self,msg):
        data=msg["contents"]
        if msg.get("type")=="subscribed" or (isinstance(data,dict) and data.get("isSnapshot",False)):
            # Startup is flat. Historical fills must never be replayed into this session.
            return
        if msg["channel"]=="userFills":
            rows=data if isinstance(data,list) else data.get("fills",[data])
            for f in rows:
                if int(f["marketId"])!=self.api.market.id: continue
                self.apply_fill(f)
        else:
            rows=data if isinstance(data,list) else data.get("orders",[data])
            for row in rows: self.update_order(row)

    def apply_fill(self,f):
        key=(str(f["tradeId"]),str(f["orderId"]),f["side"])
        stamp=int(f["createdAt"])
        if key in self.seen or stamp<self.started_us: return
        self.seen.add(key); self.fill_journal[key]=f
        if len(self.seen)>100000: self.fatal="fill deduplication bound reached"
        self.last_fill_seq=max(self.last_fill_seq,int(f.get("sequenceNumber",-1)))
        if stamp<self.last_fill_time:
            # REST catches delayed fills; rebuild cost basis in event order.
            capital=self.ledger.initial
            from .models import Ledger
            rebuilt=Ledger(capital)
            for x in sorted(self.fill_journal.values(),key=lambda x:(int(x["createdAt"]),int(x.get("sequenceNumber",0)),str(x["tradeId"]))):
                rebuilt.fill(x["side"],x["size"],x["price"],x["fee"],x["role"]=="MAKER")
            self.ledger.__dict__.update(rebuilt.__dict__)
        else:
            self.ledger.fill(f["side"],f["size"],f["price"],f["fee"],f["role"]=="MAKER")
        self.last_fill_time=max(stamp,self.last_fill_time)
        self.log("fill",trade_id=f["tradeId"],order_id=f["orderId"],side=f["side"],quantity=f["size"],
                 price=f["price"],fee=f["fee"],role=f["role"],position=self.ledger.qty,realized_pnl=self.ledger.realized)
        if f.get("liquidation"): self.fatal="forced liquidation or ADL"

    def update_order(self,row):
        if "orderId" not in row: return
        cid=row.get("clientId")
        o=self.history.get(cid) or next((x for x in self.history.values() if x.order_id==row["orderId"]),None)
        if not o:
            self.fatal="unmanaged order appeared on dedicated subaccount"
            return
        seq=int(row.get("sequenceNumber",-1))
        if seq>=0 and seq<=o.sequence: return
        o.sequence=max(o.sequence,seq)
        o.order_id=row["orderId"]
        previous_status=o.status
        o.status=row["status"]
        o.rejection_reason=row.get("rejectionReason",o.rejection_reason)
        if "remainingSize" in row: o.remaining=number(row["remainingSize"])
        self.log("order_state",client_id=o.client_id,order_id=o.order_id,status=o.status,remaining=o.remaining,
                 rejection_reason=row.get("rejectionReason"))
        if o.status=="REJECTED" and previous_status!="REJECTED" and o.rejection_reason!="POST_ONLY_WOULD_CROSS": self.failures+=1
        elif o.status in ("OPEN","FILLED"): self.failures=0
        if row.get("rejectionReason")=="SELF_TRADE": self.fatal="exchange self-trade prevention triggered"
        if o.status in TERMINAL and self.orders.get(o.quote.slot) is o: self.orders.pop(o.quote.slot,None)

    async def confirmed(self,o,terminal=False):
        deadline=time.monotonic()+self.c.confirmation_seconds
        next_poll=0.0
        while time.monotonic()<deadline:
            if o.status in TERMINAL: return
            if not terminal and o.status=="OPEN": return
            now=time.monotonic()
            # A live socket can still miss/delay one event. Poll REST even when
            # connected, but never interpret an absent order as a rejection.
            if o.order_id and now>=next_poll:
                try:
                    raw=await asyncio.wait_for(self.api.get("/v1/order/"+o.order_id,self.scope()),
                                               timeout=min(2,deadline-now))
                    row=raw.get("order",raw)
                    if row.get("orderId")==o.order_id:
                        self.update_order(row)
                    if o.status in TERMINAL or (not terminal and o.status=="OPEN"): return
                except (ExchangeError,asyncio.TimeoutError):
                    self.log("order_confirmation_read_failed",client_id=o.client_id)
                next_poll=time.monotonic()+.5
            await asyncio.sleep(.05)
        self.fatal="order lifecycle confirmation timed out"
        raise UncertainOrder(self.fatal)

    async def place(self,quote,book,ioc=False):
        if not ioc and (not self.api.fresh() or self.fatal or self.recovering or self.failures>=self.c.max_order_failures): raise ExchangeError("unsafe to place new orders")
        if any((quote.side=="BUY" and quote.price>=o.quote.price) or (quote.side=="SELL" and quote.price<=o.quote.price)
               for o in self.orders.values() if o.quote.side!=quote.side):
            raise ExchangeError("local self-cross prevention blocked order")
        cid="mm-"+uuid.uuid4().hex[:28]
        o=Order(quote,cid,quote.qty,time.monotonic())
        self.orders[quote.slot]=o; self.history[cid]=o
        if len(self.history)>20000: self.fatal="order history bound reached"; raise ExchangeError(self.fatal)
        # All orders, even IOC, require >=one month expiry. Use 40 days.
        # https://docs.arcus.xyz/api-reference/authentication
        body=dict(self.scope(),marketId=self.api.market.id,orderSide=quote.side,orderType="LIMIT",
                  timeInForce="IOC" if ioc else "ALO",goodTilTime=str(self.api.stamp()//1000+40*86400*1000000),
                  quantity=fmt(quote.qty),price=fmt(quote.price),reduceOnly=quote.reduce,clientId=cid)
        self.log("order",client_id=cid,side=quote.side,price=quote.price,quantity=quote.qty,reduce_only=quote.reduce,tif=body["timeInForce"])
        try:
            response=await self.post("placeOrder",body)
        except UncertainOrder: raise
        except RateLimited:
            self.orders.pop(quote.slot,None); o.status="REJECTED"
            raise
        except ExchangeError:
            self.orders.pop(quote.slot,None); o.status="REJECTED"; self.failures+=1
            raise
        o.order_id=response.get("orderId",o.order_id)
        # HTTP ACK is not terminal state; keep reserved exposure until WS confirms.
        await self.confirmed(o,terminal=ioc)
        if o.status=="REJECTED":
            if not ioc and o.rejection_reason=="POST_ONLY_WOULD_CROSS":
                self.log("quote_retry",client_id=o.client_id,reason=o.rejection_reason)
                return None
            raise ExchangeError("engine rejected order")
        return o

    async def cancel(self,slot):
        o=self.orders.get(slot)
        if not o: return
        body=dict(self.scope(),marketId=self.api.market.id,kind="clientId",clientId=o.client_id)
        self.log("cancel_request",client_id=o.client_id)
        await self.post("cancelOrder",body)
        await self.confirmed(o,terminal=True)

    async def cancel_all(self):
        await self.post("cancelAllOrders",dict(self.scope(),marketId=self.api.market.id))
        # With a disconnected WS, REST is a fallback; an empty open-order list
        # alone cannot prove that an in-flight place/cancel has completed.
        deadline=time.monotonic()+self.c.confirmation_seconds
        while time.monotonic()<deadline:
            pending=False
            for o in list(self.orders.values()):
                if not o.order_id: pending=True; continue
                raw=await self.api.get("/v1/order/"+o.order_id,self.scope())
                row=raw.get("order",raw)
                if row["status"] in TERMINAL:
                    o.status=row["status"]; self.orders.pop(o.quote.slot,None)
                else: pending=True
            rows=(await self.api.get("/v1/openOrders",dict(self.scope(),market=self.c.market)))["orders"]
            if not rows and not pending:
                self.log("cancel_all_confirmed")
                return
            await asyncio.sleep(.25)
        raise UncertainOrder("cancel-all did not reach confirmed terminal state; dead man's switch remains armed")
