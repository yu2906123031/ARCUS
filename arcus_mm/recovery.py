"""Load replay bounds from an explicitly selected prior live-session log."""
import json
import os
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

@dataclass(frozen=True)
class Checkpoint:
    trade_id: str
    created_at: int
    sequence_number: int
    initial: str
    qty: str
    entry: str
    realized: str
    fees: str
    volume: str
    maker_volume: str
    market: str
    account_index: int
    recent_fills: tuple


def checkpoint_path(directory,market,account_index):
    safe=market.replace("/","-").replace("\\","-")
    return Path(directory)/(f"checkpoint-{safe}-{int(account_index)}.json")


def save_checkpoint(directory,market,account_index,ledger,trade_id,created_at,sequence_number,recent_fills=()):
    """Atomically persist the compact accounting state and newest processed fill."""
    root=Path(directory);root.mkdir(parents=True,exist_ok=True)
    path=checkpoint_path(root,market,account_index)
    compact=[];seen=set()
    for row in recent_fills:
        item=dict(trade_id=str(row["trade_id"]),created_at=int(row["created_at"]),
                  sequence_number=int(row.get("sequence_number",-1)))
        if item["trade_id"] not in seen:
            seen.add(item["trade_id"]);compact.append(item)
    if str(trade_id) not in seen:
        compact.append(dict(trade_id=str(trade_id),created_at=int(created_at),sequence_number=int(sequence_number)))
    payload=dict(version=2,market=market,account_index=int(account_index),trade_id=str(trade_id),
                 created_at=int(created_at),sequence_number=int(sequence_number),recent_fills=compact,
                 ledger={name:str(getattr(ledger,name)) for name in
                         ("initial","qty","entry","realized","fees","volume","maker_volume")})
    temp=path.with_name(path.name+f".{os.getpid()}.tmp")
    with temp.open("w",encoding="utf-8") as target:
        json.dump(payload,target,separators=(",",":"),sort_keys=True)
        target.flush();os.fsync(target.fileno())
    os.chmod(temp,0o600);os.replace(temp,path)
    directory_fd=os.open(root,os.O_RDONLY)
    try: os.fsync(directory_fd)
    finally: os.close(directory_fd)
    return path


def load_checkpoint(directory,market,account_index):
    path=checkpoint_path(directory,market,account_index)
    if not path.exists(): return None
    raw=json.loads(path.read_text(encoding="utf-8"))
    if raw.get("version") not in (1,2) or raw.get("market")!=market or int(raw.get("account_index",-1))!=int(account_index):
        raise ValueError("checkpoint market/subaccount mismatch")
    ledger=raw.get("ledger",{})
    names=("initial","qty","entry","realized","fees","volume","maker_volume")
    values={name:str(ledger[name]) for name in names}
    parsed={name:number(value) for name,value in values.items()}
    if any(not value.is_finite() for value in parsed.values()) or parsed["initial"]<=0:
        raise ValueError("invalid checkpoint ledger")
    created_at=int(raw["created_at"]);sequence=int(raw["sequence_number"])
    trade_id=str(raw["trade_id"])
    if created_at<0 or sequence<0 or not trade_id: raise ValueError("invalid checkpoint cursor")
    recent=[]
    fallback=[{"trade_id":trade_id,"created_at":created_at,"sequence_number":sequence}]
    for row in raw.get("recent_fills",fallback):
        item=(str(row["trade_id"]),int(row["created_at"]),int(row.get("sequence_number",-1)))
        if not item[0] or item[1]<0: raise ValueError("invalid checkpoint recent fill")
        recent.append(item)
    if len(recent)>20000: raise ValueError("checkpoint recent-fill bound reached")
    return Checkpoint(trade_id=trade_id,created_at=created_at,sequence_number=sequence,
                      market=market,account_index=int(account_index),recent_fills=tuple(recent),**values)

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
