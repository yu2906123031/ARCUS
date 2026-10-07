import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from dataclasses import replace
from .api import PublicAPI, Live
from .config import load
from .engine import Engine
from .models import Log, Ledger

def main(argv=None):
    p=argparse.ArgumentParser(description="Independent BTC-USD Arcus maker; default PAPER")
    p.add_argument("command",choices=("run","doctor"),nargs="?",default="run")
    p.add_argument("--config",default="mm_config.json")
    p.add_argument("--live",action="store_true",help="explicitly authorize real order placement")
    p.add_argument("--strategy",choices=("mid","grid"))
    p.add_argument("--seconds",type=float,default=0)
    p.add_argument("--flatten-on-exit",action="store_true",help="IOC close on duration/normal stop; stop-loss always closes")
    p.add_argument("--dashboard", action="store_true", help="local monitoring UI")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--resume-log",help="replay prior live fills and prove account consistency before taking over")
    p.add_argument("--auto-resume",action="store_true",help="automatically replay the latest matching live log for an existing position")
    p.add_argument("--auto-restart",action="store_true",help="restart transient failures after backoff; never restart operator or risk stops")
    argv=list(sys.argv[1:] if argv is None else argv)
    a=p.parse_args(argv)
    if a.auto_resume and not a.live: p.error("auto-resume requires --live")
    if a.auto_resume and a.resume_log: p.error("choose auto-resume or resume-log")
    resume=None
    if a.resume_log:
        if not a.live: p.error("resume-log requires --live")
        from .recovery import load_resume
        try: resume=load_resume(a.resume_log)
        except (ValueError,OSError,KeyError) as exc: p.error("invalid resume log: "+type(exc).__name__)
    if not 1 <= a.port <= 65535: p.error("invalid dashboard port")
    if a.live:
        from .env import load_env
        try: load_env()
        except ValueError as exc: p.error(str(exc))
    c=load(a.config)
    if a.strategy: c=replace(c,strategy=a.strategy)
    if not c.paper and not a.live: p.error("config paper=false requires explicit --live")
    if a.seconds<0 or not __import__("math").isfinite(a.seconds): p.error("invalid seconds")
    if a.live and c.market != "BTC-USD": p.error("ETH currently supports paper mode only")
    c.validate()
    if a.auto_restart:
        if a.command!="run" or not a.live or not a.auto_resume: p.error("auto-restart requires run --live --auto-resume")
        from .restart import supervise
        child_args=[arg for arg in argv if arg!="--auto-restart"]
        return supervise(lambda: main(child_args),cooldown=lambda:getattr(main,"retry_after",0))
    main.retry_after=0
    engines=[]
    apis=[]
    log=Log(c.log_dir,"live" if a.live else "paper")
    async def execute():
        api=PublicAPI(c,log)
        apis.append(api)
        if a.command=="doctor":
            try:
                m,maker,taker=await api.metadata(); await api.clock()
                if a.live: await Live(api,Ledger(c.paper_equity),log).preflight(resume,auto_resume_dir=c.log_dir if a.auto_resume else None)
                log("doctor",market_id=m.id,tick=m.tick,step=m.step,min_size=m.min_size,min_notional=m.min_notional,
                    maker_fee=maker,taker_fee=taker,orders_sent=False,
                    minimum_base_size=m.min_size,order_equity_fraction=c.order_equity_fraction)
            finally: await api.http.aclose()
            return
        lock=Path("mm_runtime.lock")
        try:
            fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY)
        except FileExistsError: raise RuntimeError("another maker/stale lock exists; verify process before removing mm_runtime.lock") from None
        os.write(fd,str(os.getpid()).encode()); os.close(fd)
        engine=Engine(c,api,log,a.live,resume,a.auto_resume)
        engines.append(engine)
        loop=asyncio.get_running_loop()
        import signal
        def stop_signal(*_): engine.trip("operator stop",a.flatten_on_exit)
        dashboard=None
        previous=signal.signal(signal.SIGINT,stop_signal)
        previous_term=None
        try:
            if hasattr(signal,"SIGTERM"): previous_term=signal.signal(signal.SIGTERM,stop_signal)
            if a.dashboard:
                from .dashboard import Dashboard
                dashboard=Dashboard(engine,loop,a.port,a.flatten_on_exit)
                dashboard.start()
            reason=await engine.run(a.seconds,a.flatten_on_exit)
            if reason not in (None,"requested duration completed","operator stop"):
                raise RuntimeError("risk stop: "+reason)
        finally:
            if dashboard: await asyncio.to_thread(dashboard.close)
            signal.signal(signal.SIGINT,previous)
            if previous_term is not None: signal.signal(signal.SIGTERM,previous_term)
            await api.http.aclose()
            lock.unlink(missing_ok=True)
    try: asyncio.run(execute())
    except (Exception,KeyboardInterrupt) as exc:
        # Avoid printing credentials or arbitrary response/request objects.
        log("fatal",error_type=type(exc).__name__,reason=str(exc) if isinstance(exc,(ValueError,RuntimeError)) else "execution failed; inspect structured logs")
        from .restart import retryable, RETRY_EXIT
        halt=engines[-1].halt if engines else None
        if retryable(exc,halt):
            main.retry_after=max(0,apis[-1].cooldown-time.monotonic()) if apis else 0
            return RETRY_EXIT
        return 1
    return 0

if __name__=="__main__": sys.exit(main())
