from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

SECRET_KEYS=re.compile(r"password|passwd|pwd|secret|token|api[_-]?key|private[_-]?key|credential",re.I)


def redact_secrets(value:Any)->Any:
    if isinstance(value,dict):
        return {k:("***" if SECRET_KEYS.search(str(k)) and v not in (None,"") else redact_secrets(v)) for k,v in value.items()}
    if isinstance(value,list): return [redact_secrets(v) for v in value]
    if isinstance(value,tuple): return tuple(redact_secrets(v) for v in value)
    return value


def safe_error_text(text:str,secrets:list[str|None]|None=None)->str:
    out=str(text)
    for secret in secrets or []:
        if secret: out=out.replace(secret,"***")
    out=re.sub(r"(?i)(password|token|api[_-]?key|secret)=([^\s;&]+)",r"\1=***",out)
    return out[:2000]


def safe_join(root:Path,name:str)->Path:
    root=root.resolve(); candidate=(root/name).resolve()
    if candidate!=root and root not in candidate.parents: raise ValueError("Path escapes configured workspace")
    return candidate


def max_upload_bytes()->int:
    return int(os.getenv("SYNTHETICFORGE_MAX_UPLOAD_BYTES",str(5*1024*1024)))
