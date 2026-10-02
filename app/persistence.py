from __future__ import annotations

import hashlib
import json
import os
import tempfile
import sqlite3
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from .models import AgentRunRecord, DatasetRecord, JobRecord, ProjectCreateRequest, RecipeSpec
from .security import redact_secrets, token_hash

SCHEMA_VERSION=2


def utcnow()->datetime: return datetime.now(timezone.utc)


def canonical_hash(value:Any)->str:
    raw=json.dumps(value,sort_keys=True,separators=(",",":"),default=str).encode()
    return hashlib.sha256(raw).hexdigest()


class Repository:
    def __init__(self,path:Path|str|None=None):
        home=Path(os.getenv("SYNTHETICFORGE_HOME",Path.home()/".syntheticforge"))
        home.mkdir(parents=True,exist_ok=True)
        self.path=Path(path or home/"state.db")
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self._single_node_lock=None
        if os.getenv("SYNTHETICFORGE_ENV", "development").lower()=="production":
            import fcntl
            lock_path=self.path.with_suffix(self.path.suffix+".single-node.lock")
            self._single_node_lock=lock_path.open("a+")
            try:fcntl.flock(self._single_node_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise RuntimeError("This SQLite production state store is already open by another application process; run exactly one replica and worker") from None
        self._migrate()

    def connect(self):
        conn=sqlite3.connect(self.path, timeout=30); conn.row_factory=sqlite3.Row; conn.execute("PRAGMA foreign_keys=ON"); conn.execute("PRAGMA busy_timeout=30000");conn.execute("PRAGMA synchronous=FULL"); return conn

    def _migrate(self):
        with self.connect() as c:
            c.execute("PRAGMA journal_mode=WAL")
            c.executescript("""
            CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY,name TEXT NOT NULL,description TEXT,system_json TEXT,settings_json TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS recipes(id TEXT PRIMARY KEY,project_id TEXT NOT NULL,name TEXT NOT NULL,version INTEGER NOT NULL,payload_json TEXT NOT NULL,created_at TEXT NOT NULL,UNIQUE(project_id,name,version));
            CREATE TABLE IF NOT EXISTS datasets(id TEXT PRIMARY KEY,project_id TEXT NOT NULL,version INTEGER NOT NULL,recipe_id TEXT,payload_json TEXT NOT NULL,created_at TEXT NOT NULL,UNIQUE(project_id,version));
            CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,project_id TEXT,state TEXT NOT NULL,progress REAL NOT NULL,current_step TEXT NOT NULL,generated_rows INTEGER NOT NULL,error TEXT,logs_json TEXT NOT NULL,payload_json TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS audit_events(id INTEGER PRIMARY KEY AUTOINCREMENT,created_at TEXT NOT NULL,event TEXT NOT NULL,actor TEXT,resource TEXT,details_json TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS agent_runs(id TEXT PRIMARY KEY,project_id TEXT,state TEXT NOT NULL,current_step TEXT NOT NULL,payload_json TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS auth_sessions(token_hash TEXT PRIMARY KEY,username TEXT NOT NULL,role TEXT NOT NULL,created_at TEXT NOT NULL,expires_at TEXT NOT NULL,revoked_at TEXT);
            CREATE TABLE IF NOT EXISTS auth_attempts(id INTEGER PRIMARY KEY AUTOINCREMENT,ip_hash TEXT NOT NULL,created_at TEXT NOT NULL,success INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS write_approvals(id TEXT PRIMARY KEY,actor TEXT NOT NULL,action TEXT NOT NULL,payload_hash TEXT NOT NULL,created_at TEXT NOT NULL,expires_at TEXT NOT NULL,consumed_at TEXT);
            CREATE TABLE IF NOT EXISTS connector_receipts(id INTEGER PRIMARY KEY AUTOINCREMENT,created_at TEXT NOT NULL,actor TEXT NOT NULL,action TEXT NOT NULL,connector TEXT NOT NULL,policy TEXT NOT NULL,query_hash TEXT,metadata_json TEXT NOT NULL);
            """)
            columns={r[1] for r in c.execute("PRAGMA table_info(datasets)")}
            for name, declaration in (("recipe_fingerprint","TEXT"),("source_metadata_fingerprint","TEXT"),("application_sha","TEXT"),("contract_fingerprint","TEXT"),("contract_json","TEXT")):
                if name not in columns: c.execute(f"ALTER TABLE datasets ADD COLUMN {name} {declaration}")
            c.executescript("""
            CREATE TRIGGER IF NOT EXISTS approval_immutable BEFORE UPDATE ON write_approvals
            WHEN OLD.id!=NEW.id OR OLD.actor!=NEW.actor OR OLD.action!=NEW.action OR OLD.payload_hash!=NEW.payload_hash OR OLD.created_at!=NEW.created_at OR OLD.expires_at!=NEW.expires_at OR OLD.consumed_at IS NOT NULL OR NEW.consumed_at IS NULL
            BEGIN SELECT RAISE(ABORT,'approval evidence is immutable and single-use'); END;
            CREATE TRIGGER IF NOT EXISTS approval_no_delete BEFORE DELETE ON write_approvals
            BEGIN SELECT RAISE(ABORT,'approval evidence is immutable'); END;
            CREATE TRIGGER IF NOT EXISTS receipt_no_update BEFORE UPDATE ON connector_receipts BEGIN SELECT RAISE(ABORT,'connector receipts are immutable'); END;
            CREATE TRIGGER IF NOT EXISTS receipt_no_delete BEFORE DELETE ON connector_receipts BEGIN SELECT RAISE(ABORT,'connector receipts are immutable'); END;
            CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit_events BEGIN SELECT RAISE(ABORT,'audit evidence is immutable'); END;
            CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit_events BEGIN SELECT RAISE(ABORT,'audit evidence is immutable'); END;
            """)
            c.execute("INSERT OR REPLACE INTO metadata(key,value) VALUES('schema_version',?)",[str(SCHEMA_VERSION)])

    def create_project(self,req:ProjectCreateRequest)->dict[str,Any]:
        now=utcnow().isoformat(); pid=uuid.uuid4().hex
        settings=redact_secrets(req.settings)
        system=req.system.model_dump(mode="json") if req.system else None
        with self.connect() as c:
            c.execute("INSERT INTO projects VALUES(?,?,?,?,?,?,?)",[pid,req.name,req.description,json.dumps(system),json.dumps(settings),now,now])
        self.audit("project.create",pid,{"name":req.name}); return self.get_project(pid)

    def get_project(self,pid:str)->dict[str,Any]:
        with self.connect() as c: row=c.execute("SELECT * FROM projects WHERE id=?",[pid]).fetchone()
        if not row: raise KeyError(pid)
        return {"id":row["id"],"name":row["name"],"description":row["description"],"system":json.loads(row["system_json"] or "null"),"settings":json.loads(row["settings_json"] or "{}"),"created_at":row["created_at"],"updated_at":row["updated_at"]}

    def list_projects(self)->list[dict[str,Any]]:
        with self.connect() as c: rows=c.execute("SELECT id FROM projects ORDER BY updated_at DESC").fetchall()
        return [self.get_project(r[0]) for r in rows]

    def save_recipe(self,recipe:RecipeSpec)->RecipeSpec:
        rid=recipe.id or uuid.uuid4().hex
        content=redact_secrets(recipe.model_dump(mode="json",exclude={"id","fingerprint"})); fingerprint=canonical_hash(content)
        payload=redact_secrets(recipe.model_copy(update={"id":rid,"fingerprint":fingerprint}).model_dump(mode="json"))
        with self.connect() as c:
            if recipe.version<=0:
                row=c.execute("SELECT COALESCE(MAX(version),0)+1 FROM recipes WHERE project_id=? AND name=?",[recipe.project_id,recipe.name]).fetchone(); payload["version"]=row[0]
            c.execute("INSERT OR REPLACE INTO recipes(id,project_id,name,version,payload_json,created_at) VALUES(?,?,?,?,?,?)",[rid,recipe.project_id,recipe.name,payload["version"],json.dumps(payload),utcnow().isoformat()])
        self.audit("recipe.save",rid,{"project_id":recipe.project_id,"version":payload["version"]}); return RecipeSpec.model_validate(payload)

    def list_recipes(self,project_id:str)->list[RecipeSpec]:
        with self.connect() as c: rows=c.execute("SELECT payload_json FROM recipes WHERE project_id=? ORDER BY name,version DESC",[project_id]).fetchall()
        return [RecipeSpec.model_validate(json.loads(r[0])) for r in rows]

    def create_dataset(self,project_id:str,seed:int,schema:Any,rules:Any,row_counts:dict[str,int],validation:dict[str,Any],profile:dict[str,Any]|None=None,recipe_id:str|None=None,exports:list[str]|None=None,parent_version:int|None=None,generator_version:str="1.0.0rc1",recipe_fingerprint:str|None=None,source_metadata_fingerprint:str|None=None,contract:dict[str,Any]|None=None,application_sha:str|None=None)->DatasetRecord:
        with self.connect() as c: version=int(c.execute("SELECT COALESCE(MAX(version),0)+1 FROM datasets WHERE project_id=?",[project_id]).fetchone()[0])
        source_fp=source_metadata_fingerprint or canonical_hash(schema)
        exact_contract=contract or {"normalized_schema":schema,"generation_constraints":rules,"seed":seed,"validation":validation,"profiles":profile or {},"row_counts":row_counts}
        record=DatasetRecord(id=uuid.uuid4().hex,project_id=project_id,version=version,recipe_id=recipe_id,seed=seed,schema_hash=canonical_hash(schema),rules_hash=canonical_hash(rules),generator_version=generator_version,created_at=utcnow(),row_counts=row_counts,validation=validation,profile=profile or {},exports=exports or [],parent_version=parent_version,recipe_fingerprint=recipe_fingerprint,source_metadata_fingerprint=source_fp,application_sha=application_sha or os.getenv("SYNTHETICFORGE_APPLICATION_SHA"),contract_fingerprint=canonical_hash(exact_contract),contract=exact_contract)
        with self.connect() as c: c.execute("INSERT INTO datasets(id,project_id,version,recipe_id,payload_json,created_at,recipe_fingerprint,source_metadata_fingerprint,application_sha,contract_fingerprint,contract_json) VALUES(?,?,?,?,?,?,?,?,?,?,?)",[record.id,project_id,version,recipe_id,json.dumps(record.model_dump(mode="json")),record.created_at.isoformat(),record.recipe_fingerprint,record.source_metadata_fingerprint,record.application_sha,record.contract_fingerprint,json.dumps(exact_contract,sort_keys=True,default=str)])
        self.audit("dataset.create",record.id,{"project_id":project_id,"version":version}); return record

    def get_dataset(self,dataset_id:str)->DatasetRecord:
        with self.connect() as c: row=c.execute("SELECT payload_json FROM datasets WHERE id=?",[dataset_id]).fetchone()
        if not row: raise KeyError(dataset_id)
        return DatasetRecord.model_validate(json.loads(row[0]))

    def list_datasets(self,project_id:str)->list[DatasetRecord]:
        with self.connect() as c: rows=c.execute("SELECT payload_json FROM datasets WHERE project_id=? ORDER BY version DESC",[project_id]).fetchall()
        return [DatasetRecord.model_validate(json.loads(r[0])) for r in rows]

    def compare_datasets(self,a_id:str,b_id:str)->dict[str,Any]:
        a=self.get_dataset(a_id); b=self.get_dataset(b_id)
        tables=sorted(set(a.row_counts)|set(b.row_counts))
        return {"a":a.model_dump(mode="json"),"b":b.model_dump(mode="json"),"schema_changed":a.schema_hash!=b.schema_hash,"rules_changed":a.rules_hash!=b.rules_hash,"row_count_changes":{t:{"a":a.row_counts.get(t,0),"b":b.row_counts.get(t,0),"delta":b.row_counts.get(t,0)-a.row_counts.get(t,0)} for t in tables}}

    def save_dataset_artifact(self,dataset_id:str,data:bytes,filename:str="dataset.zip")->str:
        record=self.get_dataset(dataset_id)
        root=self.path.parent/"datasets"/dataset_id
        root.mkdir(parents=True,exist_ok=True)
        safe=Path(filename).name
        target=root/safe
        with tempfile.NamedTemporaryFile(mode="wb",dir=root,prefix=".artifact-",suffix=".tmp",delete=False) as stream:
            temp=Path(stream.name)
            stream.write(data);stream.flush();os.fsync(stream.fileno())
        os.replace(temp,target)
        dirfd=os.open(root,os.O_RDONLY)
        try:os.fsync(dirfd)
        finally:os.close(dirfd)
        export=str(target)
        if export not in record.exports: record.exports.append(export)
        with self.connect() as c:
            c.execute("UPDATE datasets SET payload_json=? WHERE id=?",[json.dumps(record.model_dump(mode="json")),dataset_id])
        self.audit("dataset.export",dataset_id,{"filename":safe,"bytes":len(data)})
        return export

    def latest_dataset(self,project_id:str)->DatasetRecord|None:
        items=self.list_datasets(project_id)
        return items[0] if items else None

    def upsert_job(self,job:JobRecord)->None:
        data=redact_secrets(job.payload); logs=[str(x)[:2000] for x in job.logs[-500:]]
        with self.connect() as c:
            c.execute("INSERT OR REPLACE INTO jobs VALUES(?,?,?,?,?,?,?,?,?,?,?)",[job.id,job.project_id,job.state,job.progress,job.current_step,job.generated_rows,job.error,json.dumps(logs),json.dumps(data),job.created_at.isoformat(),job.updated_at.isoformat()])

    def get_job(self,jid:str)->JobRecord:
        with self.connect() as c: r=c.execute("SELECT * FROM jobs WHERE id=?",[jid]).fetchone()
        if not r: raise KeyError(jid)
        return JobRecord(id=r["id"],project_id=r["project_id"],state=r["state"],progress=r["progress"],current_step=r["current_step"],generated_rows=r["generated_rows"],error=r["error"],logs=json.loads(r["logs_json"]),payload=json.loads(r["payload_json"]),created_at=datetime.fromisoformat(r["created_at"]),updated_at=datetime.fromisoformat(r["updated_at"]))

    def list_jobs(self,project_id:str|None=None)->list[JobRecord]:
        with self.connect() as c:
            rows=c.execute("SELECT id FROM jobs WHERE project_id=? ORDER BY updated_at DESC",[project_id]).fetchall() if project_id else c.execute("SELECT id FROM jobs ORDER BY updated_at DESC LIMIT 200").fetchall()
        return [self.get_job(r[0]) for r in rows]


    def upsert_agent_run(self, run:AgentRunRecord)->None:
        payload=redact_secrets(run.model_dump(mode="json"))
        with self.connect() as c:
            c.execute(
                "INSERT OR REPLACE INTO agent_runs(id,project_id,state,current_step,payload_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                [run.id,run.project_id,run.state,run.current_step,json.dumps(payload),run.created_at.isoformat(),run.updated_at.isoformat()]
            )

    def get_agent_run(self,run_id:str)->AgentRunRecord:
        with self.connect() as c:
            row=c.execute("SELECT payload_json FROM agent_runs WHERE id=?",[run_id]).fetchone()
        if not row: raise KeyError(run_id)
        return AgentRunRecord.model_validate(json.loads(row[0]))

    def list_agent_runs(self,project_id:str|None=None,limit:int=200)->list[AgentRunRecord]:
        limit=max(1,min(int(limit),1000))
        with self.connect() as c:
            if project_id:
                rows=c.execute("SELECT payload_json FROM agent_runs WHERE project_id=? ORDER BY updated_at DESC LIMIT ?",[project_id,limit]).fetchall()
            else:
                rows=c.execute("SELECT payload_json FROM agent_runs ORDER BY updated_at DESC LIMIT ?",[limit]).fetchall()
        return [AgentRunRecord.model_validate(json.loads(r[0])) for r in rows]

    def audit(self,event:str,resource:str,details:dict[str,Any],actor:str="local-user"):
        with self.connect() as c: c.execute("INSERT INTO audit_events(created_at,event,actor,resource,details_json) VALUES(?,?,?,?,?)",[utcnow().isoformat(),event,actor,resource,json.dumps(redact_secrets(details))])

    def audit_events(self,limit:int=100)->list[dict[str,Any]]:
        with self.connect() as c: rows=c.execute("SELECT * FROM audit_events ORDER BY id DESC LIMIT ?",[max(1,min(limit,1000))]).fetchall()
        return [dict(r)|{"details":json.loads(r["details_json"])} for r in rows]

    def create_session(self,token:str,username:str,role:str,ttl_seconds:int=43200)->None:
        now=utcnow(); expiry=now+timedelta(seconds=ttl_seconds)
        with self.connect() as c:c.execute("INSERT INTO auth_sessions(token_hash,username,role,created_at,expires_at) VALUES(?,?,?,?,?)",[token_hash(token),username,role,now.isoformat(),expiry.isoformat()])

    def session(self,token:str)->dict[str,Any]|None:
        with self.connect() as c:r=c.execute("SELECT username,role,expires_at,revoked_at FROM auth_sessions WHERE token_hash=?",[token_hash(token)]).fetchone()
        if not r or r["revoked_at"] or datetime.fromisoformat(r["expires_at"])<=utcnow():return None
        return {"username":r["username"],"role":r["role"]}

    def revoke_session(self,token:str)->None:
        with self.connect() as c:c.execute("UPDATE auth_sessions SET revoked_at=? WHERE token_hash=? AND revoked_at IS NULL",[utcnow().isoformat(),token_hash(token)])

    def login_allowed(self,ip_hash:str,window_seconds:int=900,max_attempts:int=10)->bool:
        since=(utcnow()-timedelta(seconds=window_seconds)).isoformat()
        with self.connect() as c:
            c.execute("DELETE FROM auth_attempts WHERE created_at<?",[since])
            return c.execute("SELECT count(*) FROM auth_attempts WHERE ip_hash=? AND success=0 AND created_at>=?",[ip_hash,since]).fetchone()[0]<max_attempts

    def record_login(self,ip_hash:str,success:bool)->None:
        with self.connect() as c:c.execute("INSERT INTO auth_attempts(ip_hash,created_at,success) VALUES(?,?,?)",[ip_hash,utcnow().isoformat(),int(success)])

    def issue_approval(self,actor:str,action:str,payload_hash:str,ttl_seconds:int=300)->str:
        aid=uuid.uuid4().hex; now=utcnow(); expiry=now+timedelta(seconds=ttl_seconds)
        with self.connect() as c:c.execute("INSERT INTO write_approvals VALUES(?,?,?,?,?,?,NULL)",[aid,actor,action,payload_hash,now.isoformat(),expiry.isoformat()])
        return aid

    def consume_approval(self,aid:str,actor:str,action:str,payload_hash:str)->bool:
        now=utcnow().isoformat()
        with self.connect() as c:
            row=c.execute("SELECT actor,action,payload_hash,expires_at,consumed_at FROM write_approvals WHERE id=?",[aid]).fetchone()
            if not row or row["actor"]!=actor or row["action"]!=action or row["payload_hash"]!=payload_hash or row["consumed_at"] or row["expires_at"]<=now:return False
            cur=c.execute("UPDATE write_approvals SET consumed_at=? WHERE id=? AND consumed_at IS NULL",[now,aid])
            return cur.rowcount==1

    def connector_receipt(self,actor:str,action:str,connector:str,policy:str,query_hash:str|None=None,metadata:dict[str,Any]|None=None)->dict[str,Any]:
        details=redact_secrets(metadata or {}); created=utcnow().isoformat()
        with self.connect() as c:cur=c.execute("INSERT INTO connector_receipts(created_at,actor,action,connector,policy,query_hash,metadata_json) VALUES(?,?,?,?,?,?,?)",[created,actor,action,connector,policy,query_hash,json.dumps(details,sort_keys=True,default=str)])
        return {"id":cur.lastrowid,"created_at":created,"actor":actor,"action":action,"connector":connector,"policy":policy,"query_hash":query_hash,"metadata":details}

    def list_connector_receipts(self,limit:int=100,q:str|None=None,connector:str|None=None,policy:str|None=None)->list[dict[str,Any]]:
        clauses=[];params=[]
        for field,value in (("connector",connector),("policy",policy)):
            if value:clauses.append(f"{field}=?");params.append(value)
        if q:clauses.append("(action LIKE ? OR query_hash=? OR metadata_json LIKE ?)");params.extend([f"%{q}%",q,f"%{q}%"])
        where=(" WHERE "+" AND ".join(clauses)) if clauses else ""
        with self.connect() as c:rows=c.execute("SELECT * FROM connector_receipts"+where+" ORDER BY id DESC LIMIT ?",[*params,max(1,min(limit,1000))]).fetchall()
        return [dict(r)|{"metadata":json.loads(r["metadata_json"])} for r in rows]
