"""Exception locations only: no values, source lines, locals or response bodies."""
import traceback
from pathlib import Path

def exception_locations(exc):
    return [dict(file=Path(frame.filename).name,function=frame.name,line=frame.lineno)
            for frame in traceback.extract_tb(exc.__traceback__)[-12:]]
