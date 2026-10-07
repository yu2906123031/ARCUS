"""Restart only transient exits; operator and risk stops remain final."""
import time
from .api import RateLimited, UncertainOrder, ClockUnavailable, RequestForbidden

RETRY_EXIT=75
TRANSIENT_HALTS={"disconnected, stale data, clock or incomplete subscriptions", "account state stale", "clock synchronization failed"}

def retryable(exc, halt=None, verified_rejection=False):
    if isinstance(exc,(KeyboardInterrupt,UncertainOrder,RequestForbidden)): return False
    if verified_rejection and halt in ("consecutive request failures","consecutive order failures"):
        return isinstance(exc,RuntimeError) and str(exc)=="risk stop: "+halt
    if halt and halt not in TRANSIENT_HALTS: return False
    if isinstance(exc,(RateLimited,ClockUnavailable)): return True
    if halt in TRANSIENT_HALTS: return True
    reason=str(exc)
    return (reason=="read-only request unavailable after 3 attempts" or
            reason.startswith("mutation connection failed before sending:") or
            reason in {"read-only request HTTP 500","read-only request HTTP 502","read-only request HTTP 503","read-only request HTTP 504","read-only request HTTP 429"})

def supervise(run, sleep=time.sleep, clock=time.monotonic, report=print, cooldown=lambda:0):
    delay=15
    try:
        while True:
            started=clock()
            code=run()
            if code!=RETRY_EXIT: return code
            if clock()-started>=300: delay=15
            wait=max(delay,cooldown())
            report(f"Temporary failure. Restarting in {wait:.1f}s; Ctrl+C stops automatic restart.",flush=True)
            sleep(wait)
            delay=min(60,delay*2)
    except KeyboardInterrupt:
        report("Automatic restart stopped by operator.",flush=True)
        return 0
