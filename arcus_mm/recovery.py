"""Load replay bounds from an explicitly selected prior live-session log."""
import json
import time
from dataclasses import dataclass
from pathlib import Path
from .models import number

@dataclass(frozen=True)
class Resume:
    since_us: int
    initial_equity: str
    market: str
    account_index: int

def load_resume(path):
    preflight=session=None
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip(): continue
        row=json.loads(line)
        if row.get("mode")!="live": raise ValueError("resume requires a live-session log")
        if row.get("event")=="preflight" and preflight is None: preflight=row
        if row.get("event")=="session" and session is None: session=row
    if preflight is None or session is None: raise ValueError("incomplete resume log")
    since=int(preflight.get("resume_since_us") or session["time_ns"]//1000)
    initial=str(preflight.get("initial_equity",preflight["equity"]))
    if number(initial)<=0 or not 0<since<=time.time_ns()//1000: raise ValueError("invalid resume bounds")
    return Resume(since,initial,session["market"],int(preflight["account_index"]))


def latest_resume(directory, market, account_index):
    """Choose the newest completed session for this market and subaccount."""
    for path in sorted(Path(directory).glob("live-*.jsonl"), reverse=True):
        rows=[]
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip(): continue
            try: rows.append(json.loads(line))
            except json.JSONDecodeError:
                # A process killed while writing can leave a partial final line.
                break
        preflight=next((r for r in rows if r.get("event")=="preflight"),None)
        session=next((r for r in rows if r.get("event")=="session"),None)
        if preflight is None or session is None: continue
        if session.get("market")!=market or preflight.get("account_index")!=account_index: continue
        since=int(preflight.get("resume_since_us") or session["time_ns"]//1000)
        initial=str(preflight.get("initial_equity",preflight["equity"]))
        if any(r.get("mode")!="live" for r in rows): raise ValueError("auto-resume requires live logs")
        if number(initial)<=0 or not 0<since<=time.time_ns()//1000: raise ValueError("invalid auto-resume bounds")
        return path, Resume(since,initial,market,account_index)
    raise ValueError("no matching live-session log for existing position")


def logged_client_ids(path):
    """Ownership comes from recorded submissions, not just a client-ID prefix."""
    ids=set()
    with Path(path).open(encoding="utf-8") as source:
        for line in source:
            try: row=json.loads(line)
            except json.JSONDecodeError: break
            if row.get("event")=="order" and row.get("mode")=="live":
                client=row.get("client_id")
                if isinstance(client,str) and client.startswith("mm-"): ids.add(client)
            if len(ids)>20000: raise ValueError("startup ownership history bound reached")
    return ids
