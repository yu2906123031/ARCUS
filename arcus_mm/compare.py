"""Two independent paper ledgers; no live trading path."""
import argparse
import asyncio
import math
import os
import signal
from dataclasses import replace
from pathlib import Path
from .api import PublicAPI
from .config import load
from .dashboard import Dashboard
from .engine import Engine
from .models import Log


def make_engines(c):
    engines=[]
    for market in ("BTC-USD","ETH-USD"):
        config=replace(c,market=market,paper=True,paper_equity="100",
                       log_dir=str(Path(c.log_dir)/market))
        config.validate()
        log=Log(config.log_dir,"paper")
        engines.append(Engine(config,PublicAPI(config,log),log,live=False))
    return engines


async def run_account(engine,seconds,flatten):
    try:
        return await engine.run(seconds,flatten)
    except Exception as exc:
        engine.trip("paper session failed: "+type(exc).__name__)
        engine.log("fatal",error_type=type(exc).__name__)
        return engine.halt
    finally:
        engine.stop.set()
        await engine.api.http.aclose()


async def execute(c,port,seconds,flatten):
    lock=Path("mm_runtime.lock")
    try:
        fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY)
    except FileExistsError:
        raise RuntimeError("another maker/stale lock exists; stop it normally before starting comparison") from None
    os.write(fd,str(os.getpid()).encode()); os.close(fd)
    engines=[]; dashboard=None; previous={}
    try:
        engines=make_engines(c)
        dashboard=Dashboard(engines,asyncio.get_running_loop(),port,flatten)
        for sig in (signal.SIGINT,signal.SIGTERM):
            previous[sig]=signal.signal(sig,lambda *_: dashboard.stop_all())
        dashboard.start()
        results=await asyncio.gather(*(run_account(e,seconds,flatten) for e in engines))
        return 0 if all(r in (None,"operator stop","requested duration completed") for r in results) else 1
    finally:
        if dashboard and dashboard.thread.is_alive(): await asyncio.to_thread(dashboard.close)
        for sig,handler in previous.items(): signal.signal(sig,handler)
        for engine in engines: await engine.api.http.aclose()
        lock.unlink(missing_ok=True)


def main(argv=None):
    p=argparse.ArgumentParser(description="BTC and ETH paper comparison, USD 100 each")
    p.add_argument("--config",default="mm_config.json")
    p.add_argument("--port",type=int,default=8765)
    p.add_argument("--seconds",type=float,default=0)
    p.add_argument("--flatten-on-exit",action="store_true")
    a=p.parse_args(argv)
    if not 1<=a.port<=65535 or not math.isfinite(a.seconds) or a.seconds<0: p.error("invalid port/duration")
    try: return asyncio.run(execute(load(a.config),a.port,a.seconds,a.flatten_on_exit))
    except (RuntimeError,OSError) as exc:
        print(type(exc).__name__+": "+str(exc),flush=True)
        return 1

if __name__=="__main__": raise SystemExit(main())
