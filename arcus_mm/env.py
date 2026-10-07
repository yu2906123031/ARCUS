"""Load only Arcus credentials from the project-local .env."""
import os
from pathlib import Path

NAMES={"ARCUS_ADDRESS","ARCUS_API_PRIVATE_KEY","ARCUS_ACCOUNT_INDEX"}

def load_env(path=None):
    path=Path(path) if path is not None else Path(__file__).resolve().parents[1]/".env"
    if not path.exists(): return
    for line_number,line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(),1):
        line=line.strip()
        if not line or line.startswith("#"): continue
        name,sep,value=line.partition("=")
        if not sep: raise ValueError(f"invalid .env syntax at line {line_number}")
        name,value=name.strip(),value.strip()
        if name not in NAMES: continue
        if value.startswith((chr(34),chr(39))):
            if len(value)<2 or value[-1]!=value[0]:
                raise ValueError(f"invalid .env quoting at line {line_number}")
            value=value[1:-1]
        else:
            value=value.split(" #",1)[0].rstrip()
        if value: os.environ.setdefault(name,value)
