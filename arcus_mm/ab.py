"""BTC inventory-skew A/B on one public feed; no authenticated API path."""
import argparse
import asyncio
import json
import math
import os
import signal
import statistics
import time
from dataclasses import replace
from decimal import Decimal as D
from pathlib import Path
from .api import PublicAPI
from .config import load
from .engine import Engine
from .models import Log
from .paper import Paper

class Measurements:
    def __init__(self,clock=time.monotonic):
        self.clock=clock
        self.cancels=0
        self.anchor=None
        self.half_times=[]
        self.completed=0
        self.censored=0

    def observe(self,event,values):
        if event=="cancel": self.cancels+=1
        if event!="fill": return
        if values.get("maker") is False:
            self.censored+=int(self.anchor is not None)
            self.anchor=None
            return
        qty=D(str(values["position"]))
        now=self.clock()
        if self.anchor is not None:
            start,peak=self.anchor
            if not qty or qty*peak<0 or abs(qty)<=abs(peak)/2:
                self.completed+=1
                self.half_times.append(now-start)
                self.half_times=self.half_times[-10000:]
                self.anchor=None
            elif abs(qty)>abs(peak):
                self.censored+=1
                self.anchor=(now,qty)
        if self.anchor is None and qty: self.anchor=(now,qty)

    def report(self,ledger,book,taker):
        volume=ledger.maker_volume
        return dict(**ledger.report(book,taker),cancel_count=self.cancels,
                    cancels_per_1000_maker_usd=D(self.cancels)*1000/volume if volume else None,
                    inventory_half_life_seconds=statistics.median(self.half_times) if self.half_times else None,
                    half_life_completed=self.completed,half_life_censored=self.censored+int(self.anchor is not None),
                    half_life_retained_samples=len(self.half_times))

class MeasuredLog(Log):
    def __init__(self,path):
        super().__init__(path,"paper")
        self.measurements=Measurements()
    def __call__(self,event,**values):
        self.measurements.observe(event,values)
        super().__call__(event,**values)


class FeedView:
    """Both variants price the same BBO frame, even across awaited reads."""
    def __init__(self,source,clock_lock):
        self.source=source
        self.clock_lock=clock_lock
        self.frame=None
    def __getattr__(self,name): return getattr(self.source,name)
    @property
    def book(self): return self.frame if self.frame is not None else self.source.book
    def fresh(self):
        return self.source.fresh() and (self.frame is None or 0<=time.monotonic()-self.frame.received<self.source.c.disconnect_seconds)
    async def clock(self):
        async with self.clock_lock:
            if not self.source.clock_pending and time.monotonic()-self.source.clock_checked<20: return
            await self.source.clock()


def make_pair(c,api,market,maker,taker,directory):
    engines=[]
    clock_lock=asyncio.Lock()
    for label,skew in (("skew_off","0"),("skew_on",c.inventory_skew_bps)):
        config=replace(c,market="BTC-USD",paper=True,strategy="mid",inventory_skew_bps=skew,inventory_cubic_bps="0" if label=="skew_off" else c.inventory_cubic_bps,
                       log_dir=str(Path(directory)/label))
        config.validate()
        log=MeasuredLog(config.log_dir)
        e=Engine(config,FeedView(api,clock_lock),log,live=False)
        e.venue=Paper(config,market,e.ledger,log,maker,taker)
        e.maker_fee,e.taker_fee=maker,taker
        e.last_metadata=time.monotonic()
        e.trading_enabled=True
        e.attach_observers(set_feed=False)
        log("session",variant=label,market=config.market,strategy=config.strategy,
            leverage_cap=config.leverage_cap,maker_fee=maker,taker_fee=taker,config=config.__dict__)
        engines.append(e)
    def trades(rows,book):
        for e in engines:
            if not e.halt and not e.stop.is_set(): e.venue.trades(rows,book)
    def books(book):
        for e in engines:
            if not e.stop.is_set():e.observe_book(book)
    api.on_book=books
    api.on_trades=trades
    return engines

async def execute(c,seconds,directory):
    lock=Path("mm_runtime.lock")
    fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY)
    os.write(fd,str(os.getpid()).encode());os.close(fd)
    api=None;stream=None;guards=[];engines=[];previous={}
    try:
        c=replace(c,market="BTC-USD",paper=True,strategy="mid")
        c.validate()
        Path(directory).mkdir(parents=True,exist_ok=True)
        api=PublicAPI(c,Log(Path(directory)/"feed","paper"))
        market,maker,taker=await api.metadata()
        await api.clock()
        engines=make_pair(c,api,market,maker,taker,directory)
        for sig in (signal.SIGINT,signal.SIGTERM):
            previous[sig]=signal.signal(sig,lambda *_: [e.trip("operator stop",True) for e in engines])
        stream=asyncio.create_task(api.stream())
        guards=[asyncio.create_task(e.watchdog()) for e in engines]
        start=time.monotonic()
        while any(not e.stop.is_set() for e in engines):
            frame=api.book
            for e in engines:
                if e.stop.is_set(): continue
                if seconds and time.monotonic()-start>=seconds: e.trip("requested duration completed",True)
                if e.halt:
                    await e.cancel_all()
                    if e.flatten and not await e.close_position():
                        if e.exit_attempts>=10:
                            e.log("fatal",error_type="PaperExitIncomplete")
                            e.flatten=False
                        continue
                    e.stop.set()
                    continue
                e.api.frame=frame
                try: await e.cycle()
                except Exception as exc:
                    e.trip("paper comparison failed: "+type(exc).__name__)
                    e.log("fatal",error_type=type(exc).__name__)
                finally: e.api.frame=None
            await asyncio.sleep(c.quote_interval_seconds)
        results={}
        for label,e in zip(("skew_off","skew_on"),engines):
            results[label]=dict(halt=e.halt,**e.log.measurements.report(e.ledger,api.book,e.taker_fee)) if api.book else dict(halt=e.halt,error="no BBO")
            e.quality.finish()
            results[label]["quality"]=e.quality.snapshot(time.monotonic())
            e.log("ab_summary",**results[label])
        report=dict(market="BTC-USD",mode="paper",elapsed_seconds=time.monotonic()-start,
                    caveat="Paper fills are not live fills; completed half-lives exclude censored episodes. Both variants use identical adaptive settings and fee models.",results=results)
        Path(directory,"summary.json").write_text(json.dumps(report,default=str,indent=2),encoding="utf-8")
        return 0 if all(e.halt in ("operator stop","requested duration completed") for e in engines) else 1
    finally:
        for e in engines:
            e.stop.set()
            await e.venue.cancel_all()
        tasks=guards+([stream] if stream else [])+[e.clock_task for e in engines if e.clock_task]
        for task in tasks: task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)
        if api: await api.http.aclose()
        for sig,handler in previous.items(): signal.signal(sig,handler)
        lock.unlink(missing_ok=True)

def main(argv=None):
    p=argparse.ArgumentParser(description="Paper-only BTC skew A/B on one shared public feed")
    p.add_argument("--config",default="mm_live_btc_100.json")
    p.add_argument("--seconds",type=float,default=3600)
    p.add_argument("--log-dir",default=None)
    a=p.parse_args(argv)
    if not math.isfinite(a.seconds) or a.seconds<0: p.error("invalid duration")
    directory=a.log_dir or str(Path("mm_logs")/("ab-BTC-USD-"+time.strftime("%Y%m%d-%H%M%S")))
    try: return asyncio.run(execute(load(a.config),a.seconds,directory))
    except (RuntimeError,OSError,ValueError) as exc:
        print(type(exc).__name__+": "+str(exc),flush=True)
        return 1

if __name__=="__main__": raise SystemExit(main())
