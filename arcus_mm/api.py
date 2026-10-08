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
class RequestForbidden(ExchangeError): pass
class UnclassifiedRejection(ExchangeError): pass
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
        self.on_book=lambda book:None
        self.on_private=lambda msg: None
        self.on_disconnect=lambda: None
        self.market=None
        self.funding_rate=D(0)
        self.next_funding_at=0
        self.signer=None
        self.cooldown=0.0
        self.limit_streak=0
        self.last_limit=0.0
        self.clock_fault=False
        self.clock_pending=False
        self.clock_anchor=None
        self.channels={"bbo","trades"}

    def rate_limit_wait(self,response,minimum=5):
        now=time.monotonic()
        if now-self.last_limit>120: self.limit_streak=0
        self.limit_streak=min(5,self.limit_streak+1);self.last_limit=now
        try: payload=response.json()
        except ValueError: payload={}
        if not isinstance(payload,dict): payload={}
        def delay(value,scale=1):
            try:
                parsed=float(value)/scale
                return parsed if 0<=parsed<float("inf") else minimum
            except (ValueError,TypeError): return minimum
        wait=max(minimum,min(60,minimum*2**(self.limit_streak-1)),delay(response.headers.get("Retry-After",minimum)),delay(payload.get("retryAfterMs",minimum*1000),1000))
        self.cooldown=max(self.cooldown,now+wait)
        return wait,payload

    async def get(self,path,params=None):
        # Retry reads only. Never automatically resend signed mutations.
        for attempt in range(3):
            try:
                r=await self.http.get(path,params=params)
                if r.status_code==429:
                    wait,_=self.rate_limit_wait(r)
                    self.log("rate_limit_pause",action="read",retry_seconds=wait)
                    raise RateLimited("read-only request HTTP 429")
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
        row=next((r for r in rows if r.get("marketDisplayName")==self.c.market or r.get("symbol")==self.c.market),None)
        if row is None: raise ExchangeError("market not found")
        self.market=Market.parse(row)
        self.funding_rate=number(row.get("fundingRate",row.get("currentFundingRate",0)))
        self.next_funding_at=int(row.get("nextFundingAt",0) or 0)
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
            except RateLimited:
                self.clock_pending=True
                raise
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
                            if self.fresh():self.on_book(b)
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

def rejection_details(response):
    """Fixed diagnostic categories only; never echo response text or credentials."""
    try: payload=response.json()
    except ValueError:
        kind="html" if "text/html" in response.headers.get("content-type","").lower() else "non_json"
        return dict(response_kind=kind,rejection_class="gateway_or_non_api_denial")
    if not isinstance(payload,dict): return dict(response_kind="json",rejection_class="unclassified_api_denial")
    error=payload.get("error","")
    if not isinstance(error,str): error=""
    error=error[:2048].lower()
    if any(x in error for x in ("rate limit","rate-limit","too many requests","temporarily banned")):
        category="rate_limit_or_temporary_ban"
    elif any(x in error for x in ("accountindex","subaccount","address mismatch","address does not match","not authorized","permission")):
        category="account_or_subaccount_permission"
    elif any(x in error for x in ("expired","revoked","invalid api key","inactive api key")):
        category="api_key_status"
    elif any(x in error for x in ("signature","timestamp")):
        category="signature_or_timestamp"
    else: category="unclassified_api_denial"
    return dict(response_kind="json",rejection_class=category)

class Live:
    def __init__(self,api,ledger,log):
        self.api,self.c,self.ledger,self.log=api,api.c,ledger,log
        self.signer=Signer(); api.signer=self.signer
        api.channels |= {"orders","userFills"}
        api.on_private=self.event
        self.orders={}; self.history={}; self.seen=set(); self.fill_journal={}
        self.on_fill=None
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
        self.funding_cumulative=D(0)
        self.resume_info=None
        self.checkpoint_dir=None
        self.checkpoint_trade_id=None
        self.checkpoint_boundary_seq=-1
        self.checkpoint_base=None
        self.checkpoint_recent={}
        self.has_checkpoint=False
        self.leverage_confirmed=False
        self.leverage_denial_pending=False
        self.protection_cooldown=0.0
        self.protection_block_reason=None
        self.protection_retry_at_ms=None
        self.mutation_lock=asyncio.Lock()
        self.last_mutation=0.0
        self.pool_ready={}
        self.forbidden_actions={}
        self.order_revision=0
        self.bulk_cancel_disabled_until=0.0

    def scope(self): return dict(address=self.signer.address,accountIndex=self.signer.account_index)

    async def post(self,action,body):
        async with self.mutation_lock:
            if action in self.forbidden_actions: raise self.forbidden_actions[action]
            if time.monotonic()<self.api.cooldown: raise RateLimited("rate-limit cooldown")
            pool="cancel" if action in ("cancelOrder","cancelAllOrders") else "order"
            wait=max(self.last_mutation+self.c.mutation_interval_seconds,self.pool_ready.get(pool,0))-time.monotonic()
            if wait>0: await asyncio.sleep(wait)
            if action=="placeOrder" and body.get("timeInForce")!="IOC" and (self.fatal or not self.api.fresh()):
                raise ExchangeError("unsafe to place after request pacing")
            self.last_mutation=time.monotonic()
            return await self._post(action,body)

    async def _post(self,action,body):
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
            wait,payload=self.api.rate_limit_wait(r,minimum=10 if action in ("cancelOrder","cancelAllOrders") else 5)
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
        if r.status_code==403:
            details=rejection_details(r)
            self.log("mutation_rejected",action=action,status_code=403,**details)
            if details["rejection_class"]=="rate_limit_or_temporary_ban":
                wait,_=self.api.rate_limit_wait(r,minimum=30)
                self.log("rate_limit_pause",action=action,retry_seconds=wait)
                raise RateLimited("explicit temporary ban: "+action)
            if details["rejection_class"] not in ("account_or_subaccount_permission","api_key_status","signature_or_timestamp"):
                if action in ("cancelOrder","cancelAllOrders"):
                    self.api.rate_limit_wait(r,minimum=10)
                raise UnclassifiedRejection("unclassified HTTP 403: "+action)
            exc=RequestForbidden("exchange request forbidden: "+action+" ("+details["rejection_class"]+")")
            self.forbidden_actions[action]=exc
            self.fatal=str(exc)
            raise exc
        if r.status_code>=500:
            recovered=await self.recover_mutation(action,body,"HTTP_"+str(r.status_code))
            if recovered is not None: return recovered
            self.fatal="uncertain server mutation result: "+action
            raise UncertainOrder(self.fatal)
        if not r.is_success:
            # Do not log signed requests/headers or echo potentially sensitive responses.
            self.log("mutation_rejected",action=action,status_code=r.status_code,**rejection_details(r))
            raise ExchangeError("mutation HTTP "+str(r.status_code)+": "+action)
        payload=r.json()
        limit=payload.get("rateLimit") if isinstance(payload,dict) else None
        if isinstance(limit,dict) and limit.get("pool") in ("order","cancel") and type(limit.get("remaining")) is int:
            self.log("request_pool",pool=limit["pool"],remaining=limit["remaining"])
            if limit["remaining"]<=0 and limit["remaining"]!=-1:
                self.pool_ready[limit["pool"]]=time.monotonic()+2
        return payload

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

    async def cancel_startup_orders(self,orders,owned):
        for row in orders:
            if row.get("marketId")!=self.api.market.id or row.get("clientId") not in owned or not row.get("orderId"):
                raise ExchangeError("startup contains orders not proven to belong to this bot")
        self.log("startup_cancel_begin",count=len(orders))
        for row in orders:
            await self.post("cancelOrder",dict(self.scope(),marketId=self.api.market.id,kind="clientId",clientId=row["clientId"]))
            deadline=time.monotonic()+self.c.confirmation_seconds
            while True:
                raw=await self.api.get("/v1/order/"+row["orderId"],self.scope())
                state=raw.get("order",raw)
                if state.get("orderId")==row["orderId"] and state.get("status") in TERMINAL: break
                if time.monotonic()>=deadline:
                    raise UncertainOrder("startup cancel did not reach confirmed terminal state")
                await asyncio.sleep(.25)
        remaining=(await self.api.get("/v1/openOrders",self.scope()))["orders"]
        if remaining: raise ExchangeError("startup still has open orders after confirmed cancellation")
        self.log("startup_cancel_confirmed",count=len(orders))

    async def preflight(self,resume=None,auto_resume_dir=None,allow_cancel=False):
        keys=(await self.api.get("/v1/apiKeys",{"address":self.signer.address}))["apiKeys"]
        key=next((k for k in keys if k["apiKey"].removeprefix("0x").lower()==self.signer.public),None)
        if not key or key["status"]!="ACTIVE": raise ExchangeError("registered active API key not found")
        if not key.get("allSubaccounts",False) and key.get("accountIndex")!=self.signer.account_index:
            raise ExchangeError("API key subaccount mismatch")
        if key["validUntil"] and int(key["validUntil"])<=time.time_ns()//1000000: raise ExchangeError("expired API key")
        a=await self.api.get("/v1/account",self.scope())
        orders=(await self.api.get("/v1/openOrders",self.scope()))["orders"]
        if orders and not (allow_cancel and auto_resume_dir is not None): raise ExchangeError("startup requires no open orders")
        resume_path=None
        checkpoint=None
        if auto_resume_dir is not None:
            from .recovery import load_checkpoint,Resume
            self.checkpoint_dir=auto_resume_dir
            checkpoint=load_checkpoint(auto_resume_dir,self.c.market,self.signer.account_index)
            if checkpoint is not None:
                self.has_checkpoint=True
                for name in ("initial","qty","entry","realized","fees","volume","maker_volume"):
                    setattr(self.ledger,name,number(getattr(checkpoint,name)))
                self.last_fill_seq=checkpoint.sequence_number
                self.checkpoint_boundary_seq=checkpoint.sequence_number
                self.last_fill_time=checkpoint.created_at
                self.checkpoint_trade_id=checkpoint.trade_id
                self.checkpoint_recent={trade_id:(created_at,sequence) for trade_id,created_at,sequence in checkpoint.recent_fills}
                self.started_us=max(0,checkpoint.created_at-6000000)
                self.fill_cursor=self.started_us
                resume=Resume(self.started_us,checkpoint.initial,self.c.market,self.signer.account_index)
                self.log("checkpoint_selected",trade_id=checkpoint.trade_id,created_at=checkpoint.created_at,
                         sequence_number=checkpoint.sequence_number)
        if orders and auto_resume_dir is not None:
            from .recovery import latest_resume
            resume_path,_=latest_resume(auto_resume_dir,self.c.market,self.signer.account_index)
        if resume is None and auto_resume_dir is not None and (orders or any(number(p["size"]) for p in a["positions"].values())):
            from .recovery import latest_resume
            path,resume=latest_resume(auto_resume_dir,self.c.market,self.signer.account_index)
            resume_path=path
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
        if orders:
            if resume_path is None: raise ExchangeError("startup cancellation requires matching automatic recovery log")
            if self.c.capital_cap is not None and number(resume.initial_equity)>D(self.c.capital_cap):
                raise ExchangeError("resume capital exceeds configured cap")
            from .recovery import logged_client_ids
            await self.cancel_startup_orders(orders,logged_client_ids(resume_path))
            a=await self.api.get("/v1/account",self.scope())
        self.equity=number(a["equity"])
        self.free_collateral=number(a["freeCollateral"])
        position=a["positions"].get(str(self.api.market.id))
        if position and position.get("cumulativeFunding") is not None:
            funding=position["cumulativeFunding"]
            self.funding_cumulative=number(funding.get("allTime",0) if isinstance(funding,dict) else funding)
        if self.equity<=0: raise ExchangeError("account has no positive equity")
        self.ledger.initial=min(self.equity,D(self.c.capital_cap)) if self.c.capital_cap is not None else self.equity
        if resume is not None:
            self.resume_info=dict(since_us=resume.since_us,initial_equity=resume.initial_equity)
            self.ledger.initial=number(resume.initial_equity)
            if self.c.capital_cap is not None and self.ledger.initial>D(self.c.capital_cap):
                raise ExchangeError("resume capital exceeds configured cap")
        self.checkpoint_base=dict(self.ledger.__dict__)
        synced=await self.reconcile()
        if not synced or self.fatal: raise ExchangeError("startup account/fills did not reconcile")
        self.log("preflight",equity=self.equity,initial_equity=self.ledger.initial,
                 resume_since_us=resume.since_us if resume is not None else None,
                 account_index=self.signer.account_index,live_orders_sent=False)

    async def leverage_state(self):
        rows=(await self.api.get("/v1/leverages",self.scope()))["leverages"]
        matches=[r for r in rows if int(r["marketId"])==self.api.market.id]
        if len(matches)!=1:raise ExchangeError("cannot verify unique market leverage state")
        row=matches[0]
        leverage=number(row["leverage"])
        mode=row["marginMode"]
        if leverage<=0 or leverage!=leverage.to_integral_value() or mode not in ("CROSS","ISOLATED"):
            raise ExchangeError("invalid market leverage state")
        if "isolated" in row and (type(row["isolated"]) is not bool or row["isolated"]!=(mode=="ISOLATED")):
            raise ExchangeError("inconsistent market margin mode")
        return leverage,mode

    async def enable(self):
        if self.fatal:raise ExchangeError(self.fatal)
        if not self.leverage_confirmed:
            expected=(D(self.c.leverage_cap),"CROSS")
            current=await self.leverage_state()
            if current==expected:
                self.log("leverage_adopted",leverage=self.c.leverage_cap,margin_mode="CROSS",write_sent=False)
            else:
                if self.leverage_denial_pending:
                    self.fatal="leverage setup rejected: expected "+str(self.c.leverage_cap)+"x CROSS; observed "+str(current[0])+"x "+current[1]
                    raise ExchangeError(self.fatal)
                body=dict(self.scope(),marketId=self.api.market.id,leverage=self.c.leverage_cap,isolated=False)
                try:
                    await self.post("setLeverage",body)
                except UnclassifiedRejection:
                    self.leverage_denial_pending=True
                    # Never repeat this write just because its denial is unclassified.
                    current=await self.leverage_state()
                    if current!=expected:
                        self.fatal="leverage setup rejected: expected "+str(self.c.leverage_cap)+"x CROSS; observed "+str(current[0])+"x "+current[1]
                        self.log("leverage_setup_blocked",expected_leverage=self.c.leverage_cap,actual_leverage=current[0],actual_margin_mode=current[1])
                        raise ExchangeError(self.fatal) from None
                    self.log("leverage_setup_recovered",leverage=self.c.leverage_cap,margin_mode="CROSS",reason="verified_after_denial")
                else:
                    deadline=time.monotonic()+self.c.confirmation_seconds
                    while await self.leverage_state()!=expected:
                        if time.monotonic()>deadline:
                            self.fatal="leverage change not confirmed"
                            raise UncertainOrder(self.fatal)
                        await asyncio.sleep(.25)
                    self.log("leverage_confirmed",leverage=self.c.leverage_cap,margin_mode="CROSS")
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

    async def fills_since(self,from_us):
        """Fetch the complete inclusive fill window, paging newest-first by timestamp."""
        rows=[]
        seen=set()
        to_us=None
        while True:
            params=dict(self.scope(),market=self.c.market,limit=1000,**{"from":from_us})
            if to_us is not None: params["to"]=to_us
            page=(await self.api.get("/v1/fills",params))["fills"]
            for row in page:
                trade_id=str(row["tradeId"])
                if trade_id not in seen:
                    seen.add(trade_id);rows.append(row)
            if len(rows)>20000: raise ExchangeError("fill catch-up safety bound reached")
            if len(page)<1000: return rows
            oldest=min(int(row["createdAt"]) for row in page)
            if to_us is not None and oldest>=to_us:
                raise ExchangeError("fill catch-up pagination did not advance")
            to_us=oldest

    async def reconcile(self):
        a=await self.api.get("/v1/account",self.scope())
        rows=await self.fills_since(self.fill_cursor)
        ordered=sorted(rows,key=lambda f:(int(f["createdAt"]),int(f.get("sequenceNumber",-1)),str(f["tradeId"])))
        if self.has_checkpoint:
            unknown_old=[f for f in ordered if int(f.get("sequenceNumber",-1))>=0
                         and int(f["sequenceNumber"])<=self.checkpoint_boundary_seq
                         and str(f["tradeId"]) not in self.checkpoint_recent]
            if unknown_old:
                self.fatal="unknown fill at or below checkpoint sequence"
        if self.fatal: return False
        for row in ordered:
            self.apply_fill(row)
        seq=int(a["sequenceNumber"])
        if seq<self.last_account_seq: raise ExchangeError("account sequence regressed")
        self.last_account_seq=seq
        if any(number(p["size"]) for k,p in a["positions"].items() if int(k)!=self.api.market.id):
            self.fatal="unexpected position in another market"
        p=a["positions"].get(str(self.api.market.id))
        self.account_qty=number(p["size"]) if p else D(0)
        if p and p.get("cumulativeFunding") is not None:
            funding=p["cumulativeFunding"]
            raw=funding.get("allTime",0) if isinstance(funding,dict) else funding
            self.funding_cumulative=number(raw)
        self.equity=number(a["equity"])
        self.free_collateral=number(a["freeCollateral"])
        self.account_time=time.monotonic()
        # Fill ledger and authoritative snapshot must converge before new quotes.
        synced=self.account_qty==self.ledger.qty and seq>=self.last_fill_seq
        if synced and self.checkpoint_dir is not None and self.fill_journal:
            newest=max(self.fill_journal.values(),key=lambda f:(int(f["createdAt"]),int(f.get("sequenceNumber",-1)),str(f["tradeId"])))
            from .recovery import save_checkpoint
            cutoff=int(newest["createdAt"])-6000000
            recent=[dict(trade_id=trade_id,created_at=created_at,sequence_number=sequence)
                    for trade_id,(created_at,sequence) in self.checkpoint_recent.items() if created_at>=cutoff]
            recent.extend(dict(trade_id=str(f["tradeId"]),created_at=int(f["createdAt"]),
                               sequence_number=int(f.get("sequenceNumber",-1)))
                          for f in self.fill_journal.values() if int(f["createdAt"])>=cutoff)
            newest_fill_seq=int(newest.get("sequenceNumber",-1))
            confirmed_fill_seq=max(self.checkpoint_boundary_seq,newest_fill_seq)
            save_checkpoint(self.checkpoint_dir,self.c.market,self.signer.account_index,self.ledger,
                            trade_id=newest["tradeId"],created_at=int(newest["createdAt"]),
                            sequence_number=confirmed_fill_seq,recent_fills=recent)
            self.checkpoint_trade_id=str(newest["tradeId"])
            self.checkpoint_boundary_seq=confirmed_fill_seq
            self.checkpoint_recent={row["trade_id"]:(row["created_at"],row["sequence_number"]) for row in recent}
            self.has_checkpoint=True
            self.started_us=max(0,int(newest["createdAt"])-6000000)
            self.fill_cursor=self.started_us
            self.checkpoint_base=dict(self.ledger.__dict__)
            self.fill_journal.clear()
        return synced

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
        key=str(f["tradeId"])
        stamp=int(f["createdAt"])
        sequence=int(f.get("sequenceNumber",-1))
        if key in self.seen or key in self.checkpoint_recent or stamp<self.started_us: return
        if self.has_checkpoint and sequence>=0 and sequence<=self.checkpoint_boundary_seq:
            self.fatal="unknown fill at or below checkpoint sequence"
            return
        self.seen.add(key); self.fill_journal[key]=f
        if len(self.seen)>100000: self.fatal="fill deduplication bound reached"
        self.last_fill_seq=max(self.last_fill_seq,sequence)
        if stamp<self.last_fill_time:
            # Rebuild the post-checkpoint delta in exchange sequence order.
            from .models import Ledger
            rebuilt=Ledger(self.ledger.initial)
            if self.checkpoint_base is not None: rebuilt.__dict__.update(self.checkpoint_base)
            for x in sorted(self.fill_journal.values(),key=lambda x:(int(x.get("sequenceNumber",-1)),int(x["createdAt"]),str(x["tradeId"]))):
                rebuilt.fill(x["side"],x["size"],x["price"],x["fee"],x["role"]=="MAKER")
            self.ledger.__dict__.update(rebuilt.__dict__)
        else:
            self.ledger.fill(f["side"],f["size"],f["price"],f["fee"],f["role"]=="MAKER")
        self.last_fill_time=max(stamp,self.last_fill_time)
        self.log("fill",trade_id=f["tradeId"],order_id=f["orderId"],side=f["side"],quantity=f["size"],
                 price=f["price"],fee=f["fee"],role=f["role"],position=self.ledger.qty,realized_pnl=self.ledger.realized)
        if self.on_fill and f["role"]=="MAKER":
            order=next((o for o in self.history.values() if o.order_id==f["orderId"]),None)
            self.on_fill(dict(trade_id=str(f["tradeId"]),side=f["side"],quantity=f["size"],price=f["price"],fee=f["fee"],
                              maker=True,timestamp_us=stamp,spread_bps=order.quote.spread_bps if order else None))
        if f.get("liquidation"): self.fatal="forced liquidation or ADL"

    def update_order(self,row):
        if "orderId" not in row: return
        cid=row.get("clientId")
        o=self.history.get(cid) or next((x for x in self.history.values() if x.order_id==row["orderId"]),None)
        if not o:
            self.fatal="unmanaged order appeared on dedicated subaccount"
            return
        if o.order_id and o.order_id!=row["orderId"]:
            self.fatal="owned order identity mismatch"
            return
        if o.status in TERMINAL and row["status"] not in TERMINAL:
            self.log("order_state_ignored",client_id=o.client_id,reason="terminal_state_regression")
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
                next_poll=max(time.monotonic()+.5,self.api.cooldown)
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
        self.order_revision+=1
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

    async def cancel(self,slot,reason="unspecified"):
        o=self.orders.get(slot)
        if not o: return
        if not o.order_id: raise UncertainOrder("cannot cancel an unacknowledged order without confirming its identity")
        await self.cancel_owned_individually([dict(clientId=o.client_id,orderId=o.order_id,marketId=self.api.market.id)],reason=reason)

    async def owned_open_orders(self):
        rows=(await self.api.get("/v1/openOrders",self.scope()))["orders"]
        for row in rows:
            order=self.history.get(row.get("clientId"))
            if (row.get("marketId")!=self.api.market.id or order is None or not row.get("orderId") or
                    (order.order_id and order.order_id!=row["orderId"])):
                raise ExchangeError("cleanup contains orders not proven to belong to this bot")
            if not order.order_id: order.order_id=row["orderId"]
        return rows

    async def verify_cleanup(self):
        deadline=time.monotonic()+self.c.confirmation_seconds
        while time.monotonic()<deadline:
            rows=await self.owned_open_orders()
            pending=False
            for order in list(self.orders.values()):
                if not order.order_id:
                    raise UncertainOrder("cleanup cannot prove an unacknowledged order terminal")
                raw=await self.api.get("/v1/order/"+order.order_id,self.scope())
                state=raw.get("order",raw)
                if state.get("orderId")==order.order_id and state.get("status") in TERMINAL:
                    order.status=state["status"]
                    if self.orders.get(order.quote.slot) is order: self.orders.pop(order.quote.slot,None)
                else: pending=True
            if not rows and not pending:
                self.log("cancel_all_confirmed")
                return
            await asyncio.sleep(.25)
        raise UncertainOrder("cancel-all did not reach confirmed terminal state; cleanup remains unverified")

    async def cancel_owned_individually(self,rows,reason="cleanup"):
        for row in rows:
            order=self.history[row["clientId"]]
            if order.status in TERMINAL: continue
            self.log("cancel_request",client_id=order.client_id,reason=reason,
                     order_lifetime_seconds=max(D(0),D(str(time.monotonic()-order.created))))
            try:
                await self.post("cancelOrder",dict(self.scope(),marketId=self.api.market.id,kind="clientId",clientId=order.client_id))
            except (UnclassifiedRejection,RateLimited):
                # A fill can race the rejection. Verify the original order before any retry.
                raw=await self.api.get("/v1/order/"+order.order_id,self.scope())
                state=raw.get("order",raw)
                if state.get("orderId")!=order.order_id or state.get("status") not in TERMINAL:
                    raise RateLimited("individual cleanup waiting for cancel cooldown")
                order.status=state["status"]
                if self.orders.get(order.quote.slot) is order: self.orders.pop(order.quote.slot,None)
                continue
            await self.confirmed(order,terminal=True)

    async def cancel_all(self,reason="risk_cleanup"):
        rows=await self.owned_open_orders()
        if not rows:
            await self.verify_cleanup()
            self.log("cancel_all_not_needed")
            return
        # Ordinary sessions have one or two proven orders. Account-wide cancellation is optional.
        if len(rows)<=2 or time.monotonic()<self.bulk_cancel_disabled_until:
            self.log("cancel_cleanup_mode",cleanup_mode="individual",count=len(rows))
            await self.cancel_owned_individually(rows,reason=reason)
            await self.verify_cleanup()
            return
        try:
            await self.post("cancelAllOrders",dict(self.scope(),marketId=self.api.market.id))
        except (UnclassifiedRejection,RateLimited) as exc:
            self.bulk_cancel_disabled_until=time.monotonic()+60
            self.log("cancel_all_fallback",reason=type(exc).__name__)
            rows=await self.owned_open_orders()
            if rows: await self.cancel_owned_individually(rows,reason=reason)
            await self.verify_cleanup()
            self.log("cancel_all_recovered",reason="verified_after_bulk_rejection")
            return
        await self.verify_cleanup()
