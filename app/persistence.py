from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import AgentRunRecord, DatasetRecord, JobRecord, ProjectCreateRequest, RecipeSpec
from .security import redact_secrets

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
        self._migrate()

    def connect(self):
        conn=sqlite3.connect(self.path); conn.row_factory=sqlite3.Row; conn.execute("PRAGMA foreign_keys=ON"); return conn

    def _migrate(self):
        with self.connect() as c:
            c.executescript("""
            CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY,name TEXT NOT NULL,description TEXT,system_json TEXT,settings_json TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS recipes(id TEXT PRIMARY KEY,project_id TEXT NOT NULL,name TEXT NOT NULL,version INTEGER NOT NULL,payload_json TEXT NOT NULL,created_at TEXT NOT NULL,UNIQUE(project_id,name,version));
            CREATE TABLE IF NOT EXISTS datasets(id TEXT PRIMARY KEY,project_id TEXT NOT NULL,version INTEGER NOT NULL,recipe_id TEXT,payload_json TEXT NOT NULL,created_at TEXT NOT NULL,UNIQUE(project_id,version));
            CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,project_id TEXT,state TEXT NOT NULL,progress REAL NOT NULL,current_step TEXT NOT NULL,generated_rows INTEGER NOT NULL,error TEXT,logs_json TEXT NOT NULL,payload_json TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS audit_events(id INTEGER PRIMARY KEY AUTOINCREMENT,created_at TEXT NOT NULL,event TEXT NOT NULL,actor TEXT,resource TEXT,details_json TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS agent_runs(id TEXT PRIMARY KEY,project_id TEXT,state TEXT NOT NULL,current_step TEXT NOT NULL,payload_json TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
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
        rid=recipe.id or uuid.uuid4().hex; payload=redact_secrets(recipe.model_copy(update={"id":rid}).model_dump(mode="json"))
        with self.connect() as c:
            if recipe.version<=0:
                row=c.execute("SELECT COALESCE(MAX(version),0)+1 FROM recipes WHERE project_id=? AND name=?",[recipe.project_id,recipe.name]).fetchone(); payload["version"]=row[0]
            c.execute("INSERT OR REPLACE INTO recipes(id,project_id,name,version,payload_json,created_at) VALUES(?,?,?,?,?,?)",[rid,recipe.project_id,recipe.name,payload["version"],json.dumps(payload),utcnow().isoformat()])
        self.audit("recipe.save",rid,{"project_id":recipe.project_id,"version":payload["version"]}); return RecipeSpec.model_validate(payload)

    def list_recipes(self,project_id:str)->list[RecipeSpec]:
        with self.connect() as c: rows=c.execute("SELECT payload_json FROM recipes WHERE project_id=? ORDER BY name,version DESC",[project_id]).fetchall()
        return [RecipeSpec.model_validate(json.loads(r[0])) for r in rows]

    def create_dataset(self,project_id:str,seed:int,schema:Any,rules:Any,row_counts:dict[str,int],validation:dict[str,Any],profile:dict[str,Any]|None=None,recipe_id:str|None=None,exports:list[str]|None=None,parent_version:int|None=None,generator_version:str="0.4.0")->DatasetRecord:
        with self.connect() as c: version=int(c.execute("SELECT COALESCE(MAX(version),0)+1 FROM datasets WHERE project_id=?",[project_id]).fetchone()[0])
        record=DatasetRecord(id=uuid.uuid4().hex,project_id=project_id,version=version,recipe_id=recipe_id,seed=seed,schema_hash=canonical_hash(schema),rules_hash=canonical_hash(rules),generator_version=generator_version,created_at=utcnow(),row_counts=row_counts,validation=validation,profile=profile or {},exports=exports or [],parent_version=parent_version)
        with self.connect() as c: c.execute("INSERT INTO datasets VALUES(?,?,?,?,?,?)",[record.id,project_id,version,recipe_id,json.dumps(record.model_dump(mode="json")),record.created_at.isoformat()])
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
        target.write_bytes(data)
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
