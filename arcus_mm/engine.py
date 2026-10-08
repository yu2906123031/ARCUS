import asyncio
import time
from decimal import Decimal as D
from .api import ExchangeError, UncertainOrder, ClockUnavailable, Live, RateLimited, RequestForbidden, UnclassifiedRejection
from .models import BPS, Book, Ledger, Quote
from .paper import Paper
from .diagnostics import exception_locations
from .quality import Markouts
from .strategy import targets, replace_needed, safe_resting, QuotePolicy

class Engine:
    def __init__(self,c,api,log,live=False,resume=None,auto_resume=False):
        self.c,self.api,self.log,self.live=c,api,log,live
        self.resume=resume
        self.auto_resume=auto_resume
        self.trading_enabled=False
        self.safety_cancelled=False
        self.ledger=Ledger(c.paper_equity)
        self.venue=None
        self.halt=None
        self.operator_stop_requested=False
        self.last_failure_unclassified=False
        self.rejection_restart_ready=False
        self.flatten=False
        self.last_account=0.0
        self.stop=asyncio.Event()
        self.last_report=0.0
        self.ready_at=None
        self.started=time.monotonic()
        self.failure_count=0
        self.exit_attempts=0
        self.exit_started=None
        self.cancel_lock=asyncio.Lock()
        self.last_clear_revision=None
        self.cancel_terminal_error=None
        self.last_metadata=0.0
        self.unsynced_since=None
        self.clock_task=None
        self.quote_policy=QuotePolicy()
        self.quality=Markouts(c,log)
        self.quality_start_us=0

    def observe_book(self,book):
        self.quality.observe(book)
        self.quote_policy.observe(self.c,book)

    def observe_fill(self,fill):
        if self.live and (not self.trading_enabled or fill["timestamp_us"]<self.quality_start_us):return
        self.quality.add_fill(fill)

    def funding_reduce_bps(self):
        stamp=getattr(self.api,"next_funding_at",0)
        if not stamp or not self.c.funding_exit_window_seconds: return D(0)
        seconds=stamp/1000000 if stamp>10**12 else stamp
        remaining=seconds-time.time()
        rate=getattr(self.api,"funding_rate",D(0))
        pays=(self.ledger.qty>0 and rate>0) or (self.ledger.qty<0 and rate<0)
        return D(self.c.funding_exit_bps) if pays and 0<=remaining<=self.c.funding_exit_window_seconds else D(0)

    def attach_observers(self,set_feed=True):
        self.venue.on_fill=self.observe_fill
        self.quality_start_us=self.api.stamp()//1000 if self.live else 0
        if set_feed:self.api.on_book=self.observe_book

    def trip(self,reason,flatten=False):
        if reason=="operator stop":self.operator_stop_requested=True
        if not self.halt: self.log("halt",reason=reason,flatten=flatten)
        self.halt=self.halt or reason
        self.flatten |= flatten

    def rejection_restart_safe(self,synced):
        return bool(self.live and self.auto_resume and synced and not self.operator_stop_requested and
                    self.last_failure_unclassified and self.halt in ("consecutive request failures","consecutive order failures") and
                    not self.flatten and not self.venue.fatal and not self.venue.orders and
                    self.last_clear_revision==self.venue.order_revision and
                    self.venue.account_qty==self.ledger.qty and self.venue.last_account_seq>=self.venue.last_fill_seq)

    async def cancel_all(self,reason="risk_cleanup"):
        async with self.cancel_lock:
            if self.cancel_terminal_error is not None: raise self.cancel_terminal_error
            revision=getattr(self.venue,"order_revision",0)
            if self.last_clear_revision==revision and not getattr(self.venue,"orders",{}): return
            deadline=time.monotonic()+60
            retry_delay=1
            while True:
                try:
                    await self.venue.cancel_all(reason=reason)
                    self.last_clear_revision=getattr(self.venue,"order_revision",0)
                    if self.halt: self.safety_cancelled=True
                    return
                except ExchangeError as exc:
                    if isinstance(exc,(UncertainOrder,RequestForbidden)):
                        self.cancel_terminal_error=exc
                        raise
                    can_retry=isinstance(exc,RateLimited) or str(exc).startswith("mutation connection failed before sending: cancelAllOrders")
                    remaining=deadline-time.monotonic()
                    if not can_retry or remaining<=0: raise
                    wait=max(.1,self.api.cooldown-time.monotonic()) if isinstance(exc,RateLimited) else retry_delay
                    self.log("cancel_wait",retry_seconds=min(wait,remaining),error_type=type(exc).__name__)
                    await asyncio.sleep(min(wait,remaining))
                    retry_delay=min(8,retry_delay*2)

    async def setup(self):
        market,maker,taker=await self.api.metadata()
        self.maker_fee,self.taker_fee=maker,taker
        await self.api.clock()
        if self.live:
            self.venue=Live(self.api,self.ledger,self.log)
            await self.venue.preflight(self.resume,auto_resume_dir=self.c.log_dir if self.auto_resume else None,allow_cancel=self.auto_resume)
        else:
            self.venue=Paper(self.c,market,self.ledger,self.log,maker,taker)
            self.api.on_trades=self.venue.trades
        self.attach_observers()
        self.log("session",strategy=self.c.strategy,market=self.c.market,leverage_cap=self.c.leverage_cap,
                 maker_fee=maker,taker_fee=taker,config=self.c.__dict__)

    async def watchdog(self):
        stale_since=None
        while not self.stop.is_set():
            if self.api.fresh():
                stale_since=None
                if self.ready_at is None: self.ready_at=time.monotonic()
            else:
                stale_since=stale_since or time.monotonic()
                if self.api.book: stale_since=min(stale_since,self.api.book.received)
                if time.monotonic()-stale_since>=self.c.disconnect_seconds:
                    self.trip("disconnected, stale data, clock or incomplete subscriptions")
            if self.api.clock_fault: self.trip("clock skew protection triggered")
            if self.live and self.venue:
                if self.venue.fatal: self.trip(self.venue.fatal)
                if self.venue.failures>=self.c.max_order_failures: self.trip("consecutive order failures")
            if self.halt:
                if self.live and self.venue and not self.c.server_protection:
                    try:
                        await self.cancel_all()
                        self.log("software_cancel_confirmed",reason=self.halt)
                    except ExchangeError as exc:
                        self.log("software_cancel_failed",error_type=type(exc).__name__,reason=self.halt)
                return
            await asyncio.sleep(.2)

    async def close_position(self):
        self.exit_started=self.exit_started or time.monotonic()
        if time.monotonic()-self.exit_started>60: raise ExchangeError("exit verification timeout; residual position may remain")
        if self.live:
            await self.venue.reconcile()
            position=self.venue.account_qty
        else: position=self.ledger.qty
        if not position:
            return not self.live or (self.ledger.qty==0 and self.venue.last_account_seq>=self.venue.last_fill_seq)
        if self.exit_attempts>=10:
            self.trip("IOC exit attempt limit reached; residual position needs manual attention")
            return False
        book=self.api.book
        if not self.api.fresh():
            # Emergency exits may use fresh REST BBO, never stale cached prices.
            # https://docs.arcus.xyz/api-reference/public/get-best-bid-offer-bbo
            book=Book.parse(await self.api.get("/v1/bbo/"+self.c.market))
            age=(time.time_ns()+self.api.offset_ns)/1e9-book.timestamp_us/1e6
            if not -self.c.max_clock_skew_ms/1000<=age<self.c.disconnect_seconds:
                raise ExchangeError("cannot safely price emergency IOC from stale REST BBO")
        side="SELL" if position>0 else "BUY"
        px=(book.bid*(1-D(self.c.exit_slippage_bps)/BPS) if side=="SELL" else book.ask*(1+D(self.c.exit_slippage_bps)/BPS))
        # Round the bound outward so IOC retains the intended slippage allowance.
        px=self.api.market.price(px,"BUY" if side=="SELL" else "SELL")
        qty=self.api.market.quantity(abs(position))
        if not qty: raise ExchangeError("residual position below quantity step")
        self.exit_attempts+=1
        await self.venue.place(Quote("EXIT",side,px,qty,True),book,ioc=True)
        self.log("exit_attempt",attempt=self.exit_attempts,quantity=qty,side=side)
        return False

    async def refresh_clock(self):
        try:
            await self.api.clock()
        except (ClockUnavailable,RateLimited) as exc:
            self.api.clock_pending=True
            self.log("quote_pause",reason="clock_rate_limited" if isinstance(exc,RateLimited) else "clock_unavailable")
            if self.venue.orders: await self.cancel_all()
        except Exception as exc:
            self.api.clock_pending=True
            self.trip("clock skew protection triggered" if self.api.clock_fault else "clock synchronization failed")
            self.log("clock_refresh_failed",error_type=type(exc).__name__)
            if self.venue.orders: await self.cancel_all()

    async def cycle(self):
        now=time.monotonic()
        if (self.api.clock_pending or now-self.api.clock_checked>20) and (self.clock_task is None or self.clock_task.done()):
            if self.clock_task is not None:
                self.clock_task.result()
            if self.api.clock_pending and self.venue.orders: await self.cancel_all()
            self.clock_task=asyncio.create_task(self.refresh_clock())
        if not self.api.fresh():
            if self.venue.orders: await self.cancel_all()
            return
        book=self.api.book
        now=time.monotonic()
        if now-self.last_metadata>30:
            previous=self.api.market
            market,maker,taker=await self.api.metadata()
            self.maker_fee,self.taker_fee=maker,taker
            if market!=previous:
                self.trip("market metadata changed")
                return
            self.last_metadata=now
        equity=self.ledger.equity(book.mid)
        synced=True
        if self.live:
            if now-self.last_account>=self.c.account_refresh_seconds:
                synced=await self.venue.reconcile(); self.last_account=now
            else:
                synced=self.venue.account_qty==self.ledger.qty and self.venue.last_account_seq>=self.venue.last_fill_seq
            # Reconcile may replay delayed fills and replace ledger cost basis.
            equity=min(self.ledger.equity(book.mid),self.venue.equity)
            if now-self.venue.account_time>self.c.account_refresh_seconds+8:
                self.trip("account state stale")
            if self.c.server_protection and now-self.venue.arm_time>=self.c.protection_refresh_seconds: await self.venue.arm()
        loss=self.ledger.unrealized(book.bid if self.ledger.qty>0 else book.ask)
        if equity<=0 or loss<=-self.ledger.initial*D(self.c.stop_loss_equity_fraction):
            self.trip("floating loss stop reached",flatten=True)
        if self.halt: return
        if not synced:
            self.unsynced_since=self.unsynced_since or now
            if now-self.unsynced_since>=self.c.disconnect_seconds: self.trip("account and fills failed to reconcile")
            await self.cancel_all()
            if now-self.last_report>5: self.log("account_sync_wait",account_position=self.venue.account_qty,fill_position=self.ledger.qty)
            return
        self.unsynced_since=None
        orders=list(self.venue.orders.values())
        if not safe_resting(self.c,equity,self.ledger.qty,book,orders):
            await self.cancel_all()
            # Any fills while cancels were pending change the next target budget.
            return
        quote_config=self.quote_policy.effective(self.c,book,getattr(self,"maker_fee",D(0)),getattr(self,"taker_fee",D(0)),self.quality.toxicity(now))
        desired={q.slot:q for q in targets(quote_config,self.api.market,book,equity,self.ledger.qty,
                                              self.quote_policy.side_spreads,self.funding_reduce_bps(),
                                              self.quote_policy.state.get("blocked_sides"))}
        cancelled_quotes=False
        for slot,o in list(self.venue.orders.items()):
            q=desired.get(slot)
            age_limit=self.c.max_reduce_order_age_seconds if o.quote.reduce else self.c.max_order_age_seconds
            stale=now-o.created>=age_limit
            if q is None or stale or replace_needed(o.quote,q,self.api.market.tick,quote_config.reprice_bps):
                reason="target_removed" if q is None else "stale_order" if stale else "quote_changed"
                self.log("quote_cancel",slot=slot,reason=reason,old_price=o.quote.price,new_price=q.price if q else None)
                await self.venue.cancel(slot,reason=reason)
                # Only cancellations share a cycle; placements use a fresh next-cycle plan.
                cancelled_quotes=True
                if not self.c.batch_quote_cancels or self.halt or not self.api.fresh(): return
        if cancelled_quotes:
            # Confirmed cancellations free the logical slots immediately. Re-plan from the
            # newest websocket book so routine repricing does not leave an empty cycle.
            if self.halt or not self.api.fresh(): return
            book=self.api.book
            quote_config=self.quote_policy.effective(self.c,book,getattr(self,"maker_fee",D(0)),getattr(self,"taker_fee",D(0)),self.quality.toxicity(time.monotonic()))
            desired={q.slot:q for q in targets(quote_config,self.api.market,book,equity,self.ledger.qty,
                                              self.quote_policy.side_spreads,self.funding_reduce_bps(),
                                              self.quote_policy.state.get("blocked_sides"))}
        for slot,q in desired.items():
            if slot in self.venue.orders: continue
            if self.halt or not self.api.fresh(): return
            current_equity=min(self.ledger.equity(self.api.book.mid),self.venue.equity) if self.live else self.ledger.equity(self.api.book.mid)
            if self.live and (self.venue.account_qty!=self.ledger.qty or self.venue.last_account_seq<self.venue.last_fill_seq): return
            if any((q.side=="BUY" and q.price>=o.quote.price) or (q.side=="SELL" and q.price<=o.quote.price)
                   for o in self.venue.orders.values() if o.quote.side!=q.side): continue
            from .models import Order
            candidate=Order(q,"budget",q.qty,now)
            if not safe_resting(self.c,current_equity,self.ledger.qty,self.api.book,list(self.venue.orders.values())+[candidate]): continue
            if self.live:
                reserved=sum((o.remaining*o.quote.price for o in self.venue.orders.values() if not o.quote.reduce),D(0))
                if not q.reduce and (reserved+q.qty*q.price)/D(self.c.leverage_cap)>self.venue.free_collateral: continue
            placed=await self.venue.place(q,self.api.book)
            if self.live and placed is None: return
            self.failure_count=0
        self.failure_count=0
        if now-self.last_report>=5:
            self.log("quote_policy",**self.quote_policy.state,inventory_skew_bps=self.c.inventory_skew_bps)
            self.log("position",**self.ledger.report(book,self.taker_fee),
                     funding_cumulative_usd=self.venue.funding_cumulative if self.live else D(0),
                     authoritative_equity=self.venue.equity if self.live else equity,
                     account_position=self.venue.account_qty if self.live else self.ledger.qty)
            self.last_report=now

    async def run(self,seconds=0,flatten_on_exit=False):
        await self.setup()
        stream=asyncio.create_task(self.api.stream())
        guard=asyncio.create_task(self.watchdog())
        enabled=not self.live
        self.trading_enabled=enabled
        cancelled=False
        primary_error=None
        try:
            while not self.stop.is_set():
                if seconds and time.monotonic()-self.started>=seconds: self.trip("requested duration completed",flatten_on_exit)
                if self.live and self.venue.fatal: self.trip(self.venue.fatal)
                if self.halt:
                    if not cancelled:
                        await self.cancel_all(); cancelled=True
                    if self.flatten:
                        if await self.close_position(): break
                        if self.exit_attempts>=10: raise ExchangeError("IOC exit incomplete; residual position remains")
                        await asyncio.sleep(.5)
                        continue
                    break
                try:
                    if not enabled and time.monotonic()-self.api.clock_checked>20:
                        await self.api.clock()
                    if not enabled and self.live and time.monotonic()-self.last_account>=self.c.account_refresh_seconds:
                        await self.venue.reconcile(); self.last_account=time.monotonic()
                        book=self.api.book
                        if book:
                            loss=self.ledger.unrealized(book.bid if self.ledger.qty>0 else book.ask)
                            if self.venue.equity<=0 or loss<=-self.ledger.initial*D(self.c.stop_loss_equity_fraction):
                                self.trip("floating loss stop reached",flatten=True)
                                continue
                    cooldown=max(self.api.cooldown,getattr(self.venue,"protection_cooldown",0) if not enabled and self.c.server_protection else 0)
                    if self.live and time.monotonic()<cooldown:
                        if enabled and self.api.fresh():
                            book=self.api.book
                            loss=self.ledger.unrealized(book.bid if self.ledger.qty>0 else book.ask)
                            if min(self.ledger.equity(book.mid),self.venue.equity)<=0 or loss<=-self.ledger.initial*D(self.c.stop_loss_equity_fraction):
                                self.trip("floating loss stop reached",flatten=True)
                                continue
                        await asyncio.sleep(min(1,cooldown-time.monotonic()))
                        continue
                    if not enabled:
                        if self.api.fresh():
                            await self.venue.enable(); enabled=True
                            self.trading_enabled=True
                            self.log("trading_enabled",server_protection=self.c.server_protection,software_protection=True)
                        else: await asyncio.sleep(.1); continue
                    await self.cycle()
                    self.failure_count=0
                    self.last_failure_unclassified=False
                except UncertainOrder as exc:
                    self.last_failure_unclassified=False
                    self.trip(str(exc))
                except RequestForbidden as exc:
                    self.last_failure_unclassified=False
                    self.trip(str(exc))
                except RateLimited:
                    pass
                except ExchangeError as exc:
                    self.last_failure_unclassified=isinstance(exc,UnclassifiedRejection) and str(exc)=="unclassified HTTP 403: placeOrder"
                    self.failure_count+=1
                    self.log("request_failed",reason=str(exc),consecutive=self.failure_count)
                    if self.failure_count>=self.c.max_order_failures: self.trip("consecutive request failures")
                await asyncio.sleep(self.c.quote_interval_seconds)
        except Exception as exc:
            primary_error=exc
            self.log("execution_error",error_type=type(exc).__name__,locations=exception_locations(exc))
            raise
        finally:
            self.trading_enabled=False
            self.quality.finish()
            self.stop.set()
            if self.clock_task:
                self.clock_task.cancel()
                await asyncio.gather(self.clock_task,return_exceptions=True)
            try:
                if self.venue and enabled and not cancelled:
                    await asyncio.shield(self.cancel_all())
                if self.live:
                    synced=await self.venue.reconcile()
                    self.rejection_restart_ready=primary_error is None and self.rejection_restart_safe(synced)
                    if self.rejection_restart_ready:
                        self.log("rejection_restart_ready",retry_seconds=self.c.rejection_restart_seconds,position=self.venue.account_qty,cleanup_confirmed=True)
                    if enabled and self.c.server_protection: await self.venue.disarm_if_clear()
                if self.api.book:
                    self.log("final",halt=self.halt,**self.ledger.report(self.api.book,self.taker_fee),
                             residual_position=self.venue.account_qty if self.live else self.ledger.qty)
            except Exception as exc:
                self.log("cleanup_error",error_type=type(exc).__name__,locations=exception_locations(exc),primary_error_type=type(primary_error).__name__ if primary_error else None)
                if primary_error is None:raise
            finally:
                stream.cancel(); guard.cancel()
                await asyncio.gather(stream,guard,return_exceptions=True)
                await self.api.http.aclose()
        return self.halt
