from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from .ai import clear_provider_session, configure_provider_session, infer_with_provider, model_system_with_provider, provider_status as ai_provider_status
from .connectors import ConnectorError, connector_statuses, create_connector
from .generator import generate_rows, infer_schema_local, rows_to_csv, rows_to_sql
from .jobs import JobManager
from .agent import AgentExecutor
from .agent.loading import load_approved_artifact
from .loaders import load_table
from .models import (
    AIProviderSessionRequest, AgentLoadApprovalRequest, AgentRunCreateRequest, ConnectorConfig, ConnectorIntrospectRequest, DirectLoadRequest, GenerateRequest, GenerateResponse,
    JobGenerateRequest, ParseSchemaRequest, ParseSchemaResponse, ProfileRequest, ProjectCreateRequest,
    ProjectGenerateRequest, RecipeSpec, SchemaInferenceRequest, SystemGenerateRequest, SystemGenerateResponse, SystemModelingRequest,
)
from .observability import increment, log_event, metrics_snapshot, prometheus_text, request_id
from .persistence import Repository
from .parquet_export import rows_to_parquet, parquet_available
from .privacy import classify_columns
from .profiling import apply_profile_to_columns, profile_rows
from .schema_parser import parse_schema
from .security import max_upload_bytes, safe_error_text
from .system_generator import generate_system, system_to_zip

BASE = Path(__file__).resolve().parent
app = FastAPI(title="SyntheticForge AI", version="0.5.0", description="Local-first agentic synthetic test-data platform")
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
allowed_origins=[x.strip() for x in os.getenv("SYNTHETICFORGE_CORS_ORIGINS","http://127.0.0.1:8000,http://localhost:8000").split(",") if x.strip()]
app.add_middleware(CORSMiddleware,allow_origins=allowed_origins,allow_credentials=False,allow_methods=["GET","POST","PUT","DELETE"],allow_headers=["Content-Type","X-Request-ID"])

_repo: Repository | None = None
_agent_executor: AgentExecutor | None = None

def repo()->Repository:
    global _repo
    if _repo is None: _repo=Repository()
    return _repo


def jobs()->JobManager:
    return JobManager(repo())


def agent_executor()->AgentExecutor:
    global _agent_executor
    if _agent_executor is None:
        _agent_executor=AgentExecutor(repo())
    return _agent_executor


@app.middleware("http")
async def observability_middleware(request:Request,call_next):
    rid=request.headers.get("X-Request-ID") or request_id(); start=time.perf_counter()
    try:
        response=await call_next(request); increment("http_requests_total")
        if response.status_code>=400: increment("http_errors_total")
    except Exception as exc:
        increment("http_errors_total"); log_event("http.exception",request_id=rid,path=request.url.path,error=safe_error_text(str(exc))); raise
    response.headers["X-Request-ID"]=rid
    response.headers["X-Content-Type-Options"]="nosniff"
    response.headers["Referrer-Policy"]="no-referrer"
    log_event("http.request",request_id=rid,method=request.method,path=request.url.path,status=response.status_code,duration_seconds=round(time.perf_counter()-start,6))
    return response


@app.get("/", response_class=HTMLResponse)
def home():
    return (BASE / "static" / "index.html").read_text(encoding="utf-8")


@app.get("/api/health")
def health():
    return {"status":"ok","service":"SyntheticForge AI","version":"0.5.0"}


@app.get("/api/readiness")
def readiness():
    try:
        repo().list_projects(); return {"status":"ready","storage":"ok"}
    except Exception as exc:
        raise HTTPException(status_code=503,detail=f"Local state store unavailable: {safe_error_text(str(exc))}") from exc


@app.get("/api/metrics", response_class=PlainTextResponse)
def metrics(): return prometheus_text()


@app.get("/api/diagnostics")
def diagnostics():
    return {"version":"0.5.0","state_db":str(repo().path),"metrics":metrics_snapshot(),"connectors":[x.model_dump() for x in connector_statuses()],"max_upload_bytes":max_upload_bytes(),"parquet_available":parquet_available(),"agent_runs":len(repo().list_agent_runs(limit=1000))}


@app.get("/api/provider-status")
def provider_status():
    return ai_provider_status()


@app.post("/api/provider-settings")
def provider_settings(req:AIProviderSessionRequest):
    status=configure_provider_session(req)
    repo().audit("ai.provider.configure","local-session",{"provider":status.get("provider"),"model":status.get("model"),"source":"session"})
    return status


@app.delete("/api/provider-settings")
def clear_provider_settings():
    repo().audit("ai.provider.clear","local-session",{})
    return clear_provider_session()


@app.get("/api/connectors")
def list_connectors(): return [s.model_dump() for s in connector_statuses()]


@app.post("/api/connectors/test")
def test_connector(config:ConnectorConfig):
    db=create_connector(config)
    try: return db.test_connection()
    finally: db.close()


@app.post("/api/connectors/introspect")
def introspect_connector(req:ConnectorIntrospectRequest):
    db=create_connector(req.config)
    try:
        tables=db.introspect_system(req.schema_name)
        profiles={}
        if req.include_profiles:
            for table in tables:
                profiles[table.name]=db.profile_table(table.name,table.schema_name,req.profile_limit).model_dump(mode="json")
        return {"connector":req.config.connector,"tables":[t.model_dump(mode="json") for t in tables],"relationship_count":sum(len(t.foreign_keys) for t in tables),"profiles":profiles}
    except ConnectorError as exc: raise HTTPException(status_code=400,detail=safe_error_text(str(exc),[req.config.password])) from exc
    finally: db.close()


@app.post("/api/profile")
def profile(req:ProfileRequest):
    classified=classify_columns(req.columns,req.rows)
    report=profile_rows(req.rows,classified,req.max_sample_rows)
    suggested=apply_profile_to_columns(classified,report)
    return {"profile":report.model_dump(mode="json"),"columns":[c.model_dump(mode="json") for c in suggested]}


@app.post("/api/model-system")
async def model_system(req:SystemModelingRequest):
    tables=await model_system_with_provider(req) if req.ai_inference else None
    mode="ai-assisted" if tables else "smart-local"
    warnings=[]
    if not tables:
        parse_req=ParseSchemaRequest(database_type=req.database_type,database_name=req.database_name,schema_name=req.schema_name,input_format="description",content=req.description,default_row_count=req.default_row_count)
        tables,warnings=parse_schema(parse_req)
    return {"mode":mode,"tables":[t.model_dump(mode="json") for t in tables],"relationship_count":sum(len(t.foreign_keys) for t in tables),"warnings":warnings}


@app.post("/api/infer-schema")
async def infer_schema(req:SchemaInferenceRequest):
    ai_columns=await infer_with_provider(req)
    if ai_columns:return {"mode":"ai-assisted","columns":[c.model_dump() for c in classify_columns(ai_columns)]}
    local=infer_schema_local(req); return {"mode":"smart-local","columns":[c.model_dump() for c in classify_columns(local)]}


@app.post("/api/generate", response_model=GenerateResponse)
async def generate(req:GenerateRequest):
    mode="explicit-schema"; columns=req.columns
    if not columns:
        meta=SchemaInferenceRequest(database_type=req.database_type,database_name=req.database_name,schema_name=req.schema_name,table_name=req.table_name,scenario=req.scenario)
        columns=await infer_with_provider(meta) if req.ai_inference else None
        if columns: mode="ai-assisted"
        else: columns=infer_schema_local(meta); mode="smart-local"
    if not columns: raise HTTPException(status_code=400,detail="No columns could be inferred")
    rows,warnings=generate_rows(req,columns); increment("generated_rows_total",len(rows))
    return GenerateResponse(inferred_columns=columns,rows=rows,warnings=warnings,generation_mode=mode)


@app.post("/api/export/{format_name}")
async def export_data(format_name:str,req:GenerateRequest):
    columns=req.columns or infer_schema_local(SchemaInferenceRequest(database_type=req.database_type,database_name=req.database_name,schema_name=req.schema_name,table_name=req.table_name,scenario=req.scenario))
    rows,_=generate_rows(req,columns); fmt=format_name.lower()
    if fmt=="csv":return PlainTextResponse(rows_to_csv(columns,rows),media_type="text/csv")
    if fmt=="sql":return PlainTextResponse(rows_to_sql(req,columns,rows),media_type="text/sql")
    if fmt=="json":return JSONResponse(rows)
    if fmt in {"ndjson","jsonl"}:return PlainTextResponse("\n".join(json.dumps(r,default=str) for r in rows)+"\n",media_type="application/x-ndjson")
    if fmt=="parquet":
        try: data=rows_to_parquet(rows)
        except RuntimeError as exc: raise HTTPException(status_code=503,detail=str(exc)) from exc
        return Response(content=data,media_type="application/vnd.apache.parquet")
    raise HTTPException(status_code=404,detail="Supported formats: csv, sql, json, ndjson, parquet")


@app.post("/api/parse-schema",response_model=ParseSchemaResponse)
def parse_schema_endpoint(req:ParseSchemaRequest):
    if len(req.content.encode("utf-8"))>max_upload_bytes(): raise HTTPException(status_code=413,detail="Schema input exceeds configured size limit")
    try: tables,warnings=parse_schema(req)
    except (ValueError,json.JSONDecodeError,ValidationError) as exc: raise HTTPException(status_code=400,detail=safe_error_text(str(exc))) from exc
    return ParseSchemaResponse(tables=tables,relationship_count=sum(len(t.foreign_keys) for t in tables),warnings=warnings)


@app.post("/api/generate-system",response_model=SystemGenerateResponse)
def generate_system_endpoint(req:SystemGenerateRequest):
    result=generate_system(req); increment("generated_rows_total",sum(len(t.rows) for t in result.tables)); return result


@app.post("/api/export-system")
def export_system(req:SystemGenerateRequest):
    result=generate_system(req); archive=system_to_zip(req,result)
    return Response(content=archive,media_type="application/zip",headers={"Content-Disposition":'attachment; filename="synthetic-system.zip"'})


@app.post("/api/load")
def direct_load(req:DirectLoadRequest):
    try:
        result=load_table(req.config,req.table,req.batch_size,req.dry_run,req.mode,req.confirm_destructive)
        repo().audit("load.execute",f"{req.table.schema_name}.{req.table.name}",{"connector":req.config.connector,"dry_run":req.dry_run,"mode":req.mode,"rows":len(req.table.rows)})
        return result
    except Exception as exc: raise HTTPException(status_code=400,detail=safe_error_text(str(exc),[req.config.password])) from exc


@app.post("/api/projects")
def create_project(req:ProjectCreateRequest): return repo().create_project(req)

@app.get("/api/projects")
def list_projects(): return repo().list_projects()

@app.get("/api/projects/{project_id}")
def get_project(project_id:str):
    try:return repo().get_project(project_id)
    except KeyError as exc: raise HTTPException(status_code=404,detail="Project not found") from exc

@app.post("/api/projects/{project_id}/recipes")
def save_recipe(project_id:str,recipe:RecipeSpec):
    if recipe.project_id!=project_id: raise HTTPException(status_code=400,detail="Recipe project_id does not match URL")
    try:repo().get_project(project_id)
    except KeyError as exc: raise HTTPException(status_code=404,detail="Project not found") from exc
    return repo().save_recipe(recipe)

@app.get("/api/projects/{project_id}/recipes")
def list_recipes(project_id:str): return repo().list_recipes(project_id)

@app.post("/api/projects/{project_id}/generate")
def project_generate(project_id:str,payload:ProjectGenerateRequest):
    try:repo().get_project(project_id)
    except KeyError as exc: raise HTTPException(status_code=404,detail="Project not found") from exc
    result=generate_system(payload.request)
    dataset=repo().create_dataset(project_id,payload.request.seed,[t.model_dump(mode="json") for t in payload.request.tables],{"scenario":payload.request.scenario}, {f"{t.schema_name}.{t.name}":len(t.rows) for t in result.tables},result.validation.model_dump(mode="json"),recipe_id=payload.recipe_id,parent_version=payload.parent_version)
    repo().save_dataset_artifact(dataset.id,system_to_zip(payload.request,result),"dataset.zip")
    dataset=repo().get_dataset(dataset.id)
    return {"dataset":dataset.model_dump(mode="json"),"generation":result.model_dump(mode="json")}

@app.get("/api/projects/{project_id}/datasets")
def list_datasets(project_id:str): return [d.model_dump(mode="json") for d in repo().list_datasets(project_id)]

@app.get("/api/datasets/{dataset_id}")
def get_dataset(dataset_id:str):
    try:return repo().get_dataset(dataset_id).model_dump(mode="json")
    except KeyError as exc: raise HTTPException(status_code=404,detail="Dataset not found") from exc

@app.get("/api/datasets/compare/{a_id}/{b_id}")
def compare_datasets(a_id:str,b_id:str):
    try:return repo().compare_datasets(a_id,b_id)
    except KeyError as exc: raise HTTPException(status_code=404,detail="Dataset not found") from exc


def _run_generation_job(job_id:str,payload:JobGenerateRequest):
    manager=jobs()
    def task(update,cancelled):
        update(state="generating",progress=20,step="generating",log="Generating relational dataset")
        if cancelled():return None
        result=generate_system(payload.request)
        update(state="validating",progress=75,step="validating",generated_rows=sum(len(t.rows) for t in result.tables),log=f"Quality score: {result.validation.quality_score}")
        if payload.project_id:
            dataset=repo().create_dataset(payload.project_id,payload.request.seed,[t.model_dump(mode="json") for t in payload.request.tables],{"scenario":payload.request.scenario},{f"{t.schema_name}.{t.name}":len(t.rows) for t in result.tables},result.validation.model_dump(mode="json"))
            repo().save_dataset_artifact(dataset.id,system_to_zip(payload.request,result),"dataset.zip")
        return result
    manager.run(job_id,task)

@app.post("/api/jobs/generate")
def create_generation_job(payload:JobGenerateRequest,background_tasks:BackgroundTasks):
    if payload.project_id:
        try:repo().get_project(payload.project_id)
        except KeyError as exc: raise HTTPException(status_code=404,detail="Project not found") from exc
    job=jobs().create(payload.project_id,payload.model_dump(mode="json"))
    background_tasks.add_task(_run_generation_job,job.id,payload)
    return job.model_dump(mode="json")

@app.get("/api/jobs")
def list_jobs(project_id:str|None=None): return [j.model_dump(mode="json") for j in repo().list_jobs(project_id)]

@app.get("/api/jobs/{job_id}")
def get_job(job_id:str):
    try:return repo().get_job(job_id).model_dump(mode="json")
    except KeyError as exc: raise HTTPException(status_code=404,detail="Job not found") from exc

@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id:str):
    try:return jobs().cancel(job_id).model_dump(mode="json")
    except KeyError as exc: raise HTTPException(status_code=404,detail="Job not found") from exc

@app.post("/api/jobs/{job_id}/retry")
def retry_job(job_id:str,background_tasks:BackgroundTasks):
    try:
        previous=repo().get_job(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404,detail="Job not found") from exc
    try:
        payload=JobGenerateRequest.model_validate(previous.payload)
    except Exception as exc:
        raise HTTPException(status_code=409,detail="This job does not contain a retryable generation request") from exc
    job=jobs().create(payload.project_id,payload.model_dump(mode="json"))
    background_tasks.add_task(_run_generation_job,job.id,payload)
    repo().audit("job.retry",job.id,{"previous_job_id":job_id})
    return job.model_dump(mode="json")


def _run_agent(run_id:str,payload:AgentRunCreateRequest):
    agent_executor().execute(run_id,payload)


@app.post("/api/agent-runs")
def create_agent_run(payload:AgentRunCreateRequest,background_tasks:BackgroundTasks):
    if payload.project_id:
        try: repo().get_project(payload.project_id)
        except KeyError as exc: raise HTTPException(status_code=404,detail="Project not found") from exc
    run=agent_executor().create(payload)
    background_tasks.add_task(_run_agent,run.id,payload)
    return run.model_dump(mode="json")


@app.get("/api/agent-runs")
def list_agent_runs(project_id:str|None=None,limit:int=100):
    return [r.model_dump(mode="json") for r in repo().list_agent_runs(project_id,limit)]


@app.get("/api/agent-runs/{run_id}")
def get_agent_run(run_id:str):
    try: return repo().get_agent_run(run_id).model_dump(mode="json")
    except KeyError as exc: raise HTTPException(status_code=404,detail="Agent run not found") from exc


@app.post("/api/agent-runs/{run_id}/cancel")
def cancel_agent_run(run_id:str):
    try: return agent_executor().cancel(run_id).model_dump(mode="json")
    except KeyError as exc: raise HTTPException(status_code=404,detail="Agent run not found") from exc


@app.get("/api/agent-runs/{run_id}/artifact")
def agent_artifact(run_id:str):
    try: run=repo().get_agent_run(run_id)
    except KeyError as exc: raise HTTPException(status_code=404,detail="Agent run not found") from exc
    if not run.artifact_path: raise HTTPException(status_code=404,detail="Agent run has no packaged artifact")
    path=Path(run.artifact_path)
    if not path.is_file(): raise HTTPException(status_code=404,detail="Agent artifact is unavailable")
    return Response(content=path.read_bytes(),media_type="application/zip",headers={"Content-Disposition":f'attachment; filename="syntheticforge-agent-{run.id[:8]}.zip"'})


@app.post("/api/agent-runs/{run_id}/approve-load")
def approve_agent_load(run_id:str,payload:AgentLoadApprovalRequest):
    try: run=repo().get_agent_run(run_id)
    except KeyError as exc: raise HTTPException(status_code=404,detail="Agent run not found") from exc
    if run.state!="completed" or not run.artifact_path:
        raise HTTPException(status_code=409,detail="Only a completed, packaged agent run can be loaded")
    try:
        result=load_approved_artifact(run.artifact_path,payload)
    except Exception as exc:
        raise HTTPException(status_code=400,detail=safe_error_text(str(exc),[payload.config.password])) from exc
    run.approval_state="approved"; run.updated_at=datetime.now().astimezone(); repo().upsert_agent_run(run)
    repo().audit("agent.load.approved",run_id,{"connector":payload.config.connector,"mode":payload.mode,"rows":result.get("rows")})
    return result


@app.post("/api/agent-runs/{run_id}/decline-load")
def decline_agent_load(run_id:str):
    try: run=repo().get_agent_run(run_id)
    except KeyError as exc: raise HTTPException(status_code=404,detail="Agent run not found") from exc
    run.approval_state="declined"; run.updated_at=datetime.now().astimezone(); repo().upsert_agent_run(run)
    repo().audit("agent.load.declined",run_id,{})
    return run.model_dump(mode="json")


@app.get("/api/audit")
def audit(limit:int=100): return repo().audit_events(limit)
