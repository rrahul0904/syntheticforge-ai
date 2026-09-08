from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone
from typing import Callable, Any

from .models import JobRecord, JobState
from .persistence import Repository
from .security import safe_error_text


def now(): return datetime.now(timezone.utc)


class JobManager:
    def __init__(self,repository:Repository):
        self.repo=repository; self._cancelled:set[str]=set(); self._lock=threading.Lock()

    def create(self,project_id:str|None=None,payload:dict[str,Any]|None=None)->JobRecord:
        t=now(); job=JobRecord(id=uuid.uuid4().hex,project_id=project_id,state="queued",progress=0,current_step="queued",created_at=t,updated_at=t,payload=payload or {})
        self.repo.upsert_job(job); return job

    def update(self,jid:str,state:JobState|None=None,progress:float|None=None,step:str|None=None,generated_rows:int|None=None,log:str|None=None,error:str|None=None)->JobRecord:
        job=self.repo.get_job(jid)
        if state: job.state=state
        if progress is not None: job.progress=max(0,min(100,progress))
        if step: job.current_step=step
        if generated_rows is not None: job.generated_rows=generated_rows
        if log: job.logs.append(str(log)[:2000])
        if error: job.error=safe_error_text(error)
        job.updated_at=now(); self.repo.upsert_job(job); return job

    def cancel(self,jid:str)->JobRecord:
        with self._lock: self._cancelled.add(jid)
        return self.update(jid,state="cancelled",step="cancelled",log="Cancellation requested")

    def is_cancelled(self,jid:str)->bool:
        with self._lock: return jid in self._cancelled

    def run(self,jid:str,fn:Callable[[Callable[...,JobRecord],Callable[[],bool]],Any])->Any:
        try:
            self.update(jid,state="generating",progress=1,step="starting")
            result=fn(lambda **kw:self.update(jid,**kw),lambda:self.is_cancelled(jid))
            if self.is_cancelled(jid): self.update(jid,state="cancelled",step="cancelled")
            else: self.update(jid,state="completed",progress=100,step="completed")
            return result
        except Exception as exc:
            self.update(jid,state="failed",step="failed",error=str(exc),log=f"Failed: {safe_error_text(str(exc))}")
            raise
