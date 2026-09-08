from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from collections import Counter, defaultdict
from contextlib import contextmanager
from typing import Any

from .security import redact_secrets

logger=logging.getLogger("syntheticforge")
if not logger.handlers:
    handler=logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler); logger.setLevel(logging.INFO)

_lock=threading.Lock(); _counters=Counter(); _durations=defaultdict(list)


def log_event(event:str,**fields:Any)->None:
    payload={"ts":time.time(),"event":event,**redact_secrets(fields)}
    logger.info(json.dumps(payload,default=str,separators=(",",":")))


def increment(metric:str,value:int|float=1)->None:
    with _lock: _counters[metric]+=value


def observe(metric:str,value:float)->None:
    with _lock:
        arr=_durations[metric]; arr.append(float(value))
        if len(arr)>1000: del arr[:-1000]


@contextmanager
def timer(metric:str,**fields:Any):
    start=time.perf_counter()
    try: yield
    finally:
        elapsed=time.perf_counter()-start; observe(metric,elapsed); log_event(metric,duration_seconds=round(elapsed,6),**fields)


def request_id()->str: return uuid.uuid4().hex[:16]


def metrics_snapshot()->dict[str,Any]:
    with _lock:
        durations={k:{"count":len(v),"sum":sum(v),"avg":sum(v)/len(v) if v else 0,"max":max(v) if v else 0} for k,v in _durations.items()}
        return {"counters":dict(_counters),"durations":durations}


def prometheus_text()->str:
    snap=metrics_snapshot(); lines=[]
    for k,v in snap["counters"].items(): lines.append(f"syntheticforge_{k} {v}")
    for k,v in snap["durations"].items():
        safe=k.replace(".","_").replace("-","_")
        lines.append(f"syntheticforge_{safe}_seconds_count {v['count']}")
        lines.append(f"syntheticforge_{safe}_seconds_sum {v['sum']}")
    return "\n".join(lines)+"\n"
