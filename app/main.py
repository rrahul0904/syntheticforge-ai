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
    ProjectGenerateRequest, RecipeSpec, SchemaInferenceRequest, SystemGenerateRequest, SystemGenerateResponse, SystemModelingRequest, AdminLoginRequest, WriteApprovalRequest,
)
from .observability import increment, log_event, metrics_snapshot, prometheus_text, request_id
from .persistence import Repository
from .parquet_export import rows_to_parquet, parquet_available
from .privacy import classify_columns
from .profiling import apply_profile_to_columns, profile_rows
from .schema_parser import parse_schema
from .security import max_upload_bytes, safe_error_text, production_mode, validate_production_environment, verify_password, token_hash, AuthIdentity, csrf_token
import secrets
import hashlib
from contextlib import asynccontextmanager
from .system_generator import generate_system, system_to_zip
_job_manager: JobManager | None = None

BASE = Path(__file__).resolve().parent
@asynccontextmanager
async def lifespan(_app):
    validate_production_environment()
    repo()
    yield

app = FastAPI(title="SyntheticForge AI", version="1.0.0-rc1", description="Local-first agentic synthetic test-data platform", lifespan=lifespan,docs_url=None if production_mode() else "/docs",redoc_url=None if production_mode() else "/redoc",openapi_url=None if production_mode() else "/openapi.json")
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
allowed_origins=[x.strip() for x in os.getenv("SYNTHETICFORGE_CORS_ORIGINS","http://127.0.0.1:8000,http://localhost:8000").split(",") if x.strip()]
app.add_middleware(CORSMiddleware,allow_origins=allowed_origins,allow_credentials=production_mode(),allow_methods=["GET","POST","PUT","DELETE"],allow_headers=["Content-Type","X-Request-ID","X-CSRF-Token","Authorization"])

_repo: Repository | None = None
_agent_executor: AgentExecutor | None = None

def repo()->Repository:
    global _repo
    if _repo is None: _repo=Repository()
    return _repo


def jobs()->JobManager:
    global _job_manager
    if _job_manager is None:_job_manager=JobManager(repo())
    return _job_manager


def agent_executor()->AgentExecutor:
    global _agent_executor
    if _agent_executor is None:
        _agent_executor=AgentExecutor(repo())
    return _agent_executor


@app.middleware("http")
async def observability_middleware(request:Request,call_next):
    rid=request.headers.get("X-Request-ID") or request_id(); start=time.perf_counter()
    path=request.url.path
    identity=None
    def early_error(detail:str,status_code:int)->JSONResponse:
        headers={"Cache-Control":"no-store","X-Request-ID":rid,"X-Content-Type-Options":"nosniff","Referrer-Policy":"no-referrer","X-Frame-Options":"DENY","Permissions-Policy":"camera=(), microphone=(), geolocation=()","Content-Security-Policy":"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'"}
        if production_mode():headers["Strict-Transport-Security"]="max-age=31536000; includeSubDomains"
        return JSONResponse({"detail":detail},status_code=status_code,headers=headers)
    if production_mode():
        if request.method!="OPTIONS" and path not in {"/api/health","/api/readiness","/api/auth/login","/api/auth/session","/","/static/app.js","/static/styles.css"}:
            identity=_authenticate(request)
            if identity is None:
                return early_error("Authentication required",401)
            if request.method not in {"GET","HEAD","OPTIONS"} and identity.via=="session":
                supplied=request.headers.get("X-CSRF-Token","")
                if not secrets.compare_digest(supplied,csrf_token(request.cookies.get("sf_session",""))):
                    return early_error("CSRF validation failed",403)
        if identity and identity.via=="api-token" and _admin_only(path):
            return early_error("Administrator browser session required",403)
        if identity and identity.role!="admin" and _admin_only(path):
            return early_error("Administrator role required",403)
        request.state.identity=identity
    elif identity is None:
        request.state.identity=AuthIdentity(username="local-user",role="admin",via="local")
    length=request.headers.get("content-length")
    if length and length.isdecimal() and int(length)>max_upload_bytes()*3:
        return early_error("Request exceeds configured size limit",413)
    body_limit=max_upload_bytes()*3
    received=0
    original_receive=request.receive
    async def limited_receive():
        nonlocal received
        message=await original_receive()
        if message["type"]=="http.request":
            received+=len(message.get("body",b""))
            if received>body_limit:raise HTTPException(status_code=413,detail="Request exceeds configured size limit")
        return message
    request._receive=limited_receive
    try:
        response=await call_next(request); increment("http_requests_total")
        if response.status_code>=400: increment("http_errors_total")
    except Exception as exc:
        increment("http_errors_total"); log_event("http.exception",request_id=rid,path=request.url.path,error=safe_error_text(str(exc))); raise
    response.headers["X-Request-ID"]=rid
    response.headers["X-Content-Type-Options"]="nosniff"
    response.headers["Referrer-Policy"]="no-referrer"
    response.headers["X-Frame-Options"]="DENY"
    response.headers["Permissions-Policy"]="camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"]="default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'"
    if production_mode():response.headers["Strict-Transport-Security"]="max-age=31536000; includeSubDomains"
    log_event("http.request",request_id=rid,method=request.method,path=request.url.path,status=response.status_code,duration_seconds=round(time.perf_counter()-start,6))
    return response


@app.get("/", response_class=HTMLResponse)
def home():
    return (BASE / "static" / "index.html").read_text(encoding="utf-8")


@app.get("/api/health")
def health():
    return {"status":"ok","service":"SyntheticForge AI","version":"1.0.0-rc1"}


@app.get("/api/readiness")
def readiness():
    try:
        repo().list_projects(); return {"status":"ready","storage":"ok"}
    except Exception as exc:
        raise HTTPException(status_code=503,detail=f"Local state store unavailable: {safe_error_text(str(exc))}") from exc


@app.get("/api/metrics", response_class=PlainTextResponse)
def metrics(): return prometheus_text()


def _admin_only(path:str)->bool:
    return path in {"/api/diagnostics","/api/audit","/api/connector-receipts","/api/provider-status","/api/provider-settings","/api/write-approvals","/api/load"} or path.startswith("/api/connectors/") or path.endswith("/approve-load")


def _authenticate(request:Request)->AuthIdentity|None:
    token=request.cookies.get("sf_session")
    if token:
        found=repo().session(token)
        if found:return AuthIdentity(username=found["username"],role=found["role"],via="session")
    authorization=request.headers.get("Authorization","")
    if not authorization.startswith("Bearer "):return None
    presented=authorization[7:].strip(); digest=token_hash(presented)
    expected=os.getenv("SYNTHETICFORGE_API_TOKEN_SHA256","").strip()
    admin=os.getenv("SYNTHETICFORGE_ADMIN_API_TOKEN_SHA256","").strip()
    if expected and secrets.compare_digest(digest,expected.lower()):return AuthIdentity("api-operator","operator",via="api-token")
    if admin and secrets.compare_digest(digest,admin):return AuthIdentity("api-administrator","admin",via="api-token")
    return None


def _identity(request:Request)->AuthIdentity:
    return getattr(request.state,"identity",AuthIdentity("local-user","admin",via="local"))


@app.post("/api/auth/login")
def auth_login(req:AdminLoginRequest,request:Request,response:Response):
    if production_mode() and request.url.scheme!="https":raise HTTPException(status_code=400,detail="Production login requires HTTPS")
    ip=request.client.host if request.client else "unknown"; ip_hash=hashlib.sha256(("syntheticforge-login:"+ip).encode()).hexdigest()
    if not repo().login_allowed(ip_hash):raise HTTPException(status_code=429,detail="Too many login attempts; try again later")
    expected_user=os.getenv("SYNTHETICFORGE_ADMIN_USERNAME","admin")
    encoded=os.getenv("SYNTHETICFORGE_ADMIN_PASSWORD_HASH","")
    operator_user=os.getenv("SYNTHETICFORGE_OPERATOR_USERNAME","");operator_hash=os.getenv("SYNTHETICFORGE_OPERATOR_PASSWORD_HASH","")
    is_admin=bool(encoded) and secrets.compare_digest(req.username,expected_user) and verify_password(req.password,encoded)
    is_operator=bool(operator_hash) and secrets.compare_digest(req.username,operator_user) and verify_password(req.password,operator_hash)
    valid=is_admin or is_operator;role="admin" if is_admin else "operator"
    repo().record_login(ip_hash,valid)
    repo().audit("auth.login.success" if valid else "auth.login.denied",ip_hash[:16],{"username":req.username[:128]},actor=req.username[:128] if valid else "unauthenticated")
    if not valid:raise HTTPException(status_code=401,detail="Invalid username or password")
    session=secrets.token_urlsafe(40); repo().create_session(session,req.username,role,int(os.getenv("SYNTHETICFORGE_SESSION_TTL_SECONDS","28800")))
    response.set_cookie("sf_session",session,httponly=True,secure=production_mode(),samesite="lax",max_age=int(os.getenv("SYNTHETICFORGE_SESSION_TTL_SECONDS","28800")),path="/")
    response.headers["Cache-Control"]="no-store"
    return {"authenticated":True,"username":req.username,"role":role,"csrf_token":csrf_token(session)}


@app.get("/api/auth/session")
def auth_session(request:Request):
    token=request.cookies.get("sf_session"); found=repo().session(token) if token else None
    return JSONResponse({"authenticated":bool(found),"required":production_mode(),"username":found["username"] if found else None,"role":found["role"] if found else None,"csrf_token":csrf_token(token) if found and token else None},headers={"Cache-Control":"no-store"})


@app.post("/api/auth/logout")
def auth_logout(request:Request,response:Response):
    token=request.cookies.get("sf_session")
    if token:repo().revoke_session(token)
    repo().audit("auth.logout",_identity(request).username,{},actor=_identity(request).username)
    response.delete_cookie("sf_session",path="/",httponly=True,secure=production_mode(),samesite="lax")
    response.headers["Cache-Control"]="no-store"
    return {"authenticated":False}


@app.post("/api/write-approvals")
def issue_write_approval(payload:WriteApprovalRequest,request:Request):
    identity=_identity(request)
    if identity.role!="admin" or identity.via!="session":raise HTTPException(status_code=403,detail="A reauthenticated administrator browser session is required")
    if production_mode() and request.url.scheme!="https":raise HTTPException(status_code=400,detail="Production approvals require HTTPS")
    expected_user=os.getenv("SYNTHETICFORGE_ADMIN_USERNAME","admin"); encoded=os.getenv("SYNTHETICFORGE_ADMIN_PASSWORD_HASH","")
    if production_mode() and (identity.username!=expected_user or not verify_password(payload.password,encoded)):
        repo().audit("write.approval.denied",payload.target,{"action":payload.action,"target":payload.target},actor=identity.username)
        raise HTTPException(status_code=401,detail="Administrator reauthentication failed")
    if payload.confirm_destructive and payload.destructive_confirmation!=f"CONFIRM DESTRUCTIVE WRITE TO {payload.target}":
        raise HTTPException(status_code=400,detail="Type the full destructive confirmation phrase shown for this target")
    if payload.action=="direct-load":
        if payload.direct_load_request is None or payload.agent_load_request is not None or payload.agent_run_id is not None:raise HTTPException(status_code=400,detail="A direct-load approval must include only its exact load request")
        load=payload.direct_load_request.model_copy(update={"approval_id":None})
        if load.confirm_destructive!=payload.confirm_destructive:raise HTTPException(status_code=400,detail="Destructive intent must match the direct-load request")
        request_data=load.model_dump(mode="json",exclude={"approval_id"})
        payload_hash=hashlib.sha256(json.dumps(request_data,sort_keys=True,separators=(",",":"),default=str).encode()).hexdigest()
        c=load.config;t=load.table
        expected_target=f"{c.connector}:{c.path or c.host or c.project or c.database or 'configured target'}/{t.schema_name}.{t.name}"
    else:
        if not payload.agent_run_id or payload.agent_load_request is None or payload.direct_load_request is not None:raise HTTPException(status_code=400,detail="An agent-load approval must include one run and its exact target request")
        try:run=repo().get_agent_run(payload.agent_run_id)
        except KeyError as exc:raise HTTPException(status_code=404,detail="Agent run not found") from exc
        if run.state!="completed" or not run.artifact_path or not Path(run.artifact_path).is_file():raise HTTPException(status_code=409,detail="Only a completed packaged agent run can be approved")
        load=payload.agent_load_request.model_copy(update={"approval_id":None})
        if load.confirm_destructive!=payload.confirm_destructive:raise HTTPException(status_code=400,detail="Destructive intent must match the agent-load request")
        artifact_hash=hashlib.sha256(Path(run.artifact_path).read_bytes()).hexdigest()
        request_data={"run_id":payload.agent_run_id,"artifact_sha256":artifact_hash,"request":load.model_dump(mode="json",exclude={"approval_id"})}
        payload_hash=hashlib.sha256(json.dumps(request_data,sort_keys=True,separators=(",",":"),default=str).encode()).hexdigest()
        c=load.config
        expected_target=f"{c.connector}:{c.path or c.host or c.project or c.database or 'configured target'}/agent-run-{payload.agent_run_id[:8]}"
    if payload.target!=expected_target:raise HTTPException(status_code=400,detail="Approval target does not match the exact write request")
    approval=repo().issue_approval(identity.username,payload.action,payload_hash)
    repo().audit("write.approval.issued",payload.target,{"action":payload.action,"target":payload.target,"destructive":payload.confirm_destructive,"approval_id":approval},actor=identity.username)
    return {"approval_id":approval,"expires_in_seconds":300,"action":payload.action,"target":payload.target}


@app.get("/api/diagnostics")
def diagnostics():
    return {"version":"1.0.0-rc1","state_backend":"sqlite-single-node","state_db":str(repo().path) if not production_mode() else repo().path.name,"metrics":metrics_snapshot(),"connectors":[x.model_dump() for x in connector_statuses()],"max_upload_bytes":max_upload_bytes(),"parquet_available":parquet_available(),"agent_runs":len(repo().list_agent_runs(limit=1000)),"production":production_mode()}


@app.get("/api/dashboard-summary")
def dashboard_summary():
    """Return the small, role-safe operational summary shown on the dashboard."""
    counters=metrics_snapshot().get("counters",{})
    return {"generated_rows":int(counters.get("generated_rows_total",0))}


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
def test_connector(config:ConnectorConfig,request:Request):
    config=config.model_copy(update={"read_only":True})
    db=create_connector(config);identity=_identity(request)
    db.receipt_sink=lambda **fields:repo().connector_receipt(identity.username,**fields)
    try:
        result=db.test_connection()
        repo().connector_receipt(identity.username,"connector.test",config.connector,"success" if result.get("ok") else "failure",metadata={"ok":bool(result.get("ok")),"read_only_enforcement":db.policy_receipt()})
        return result
    finally: db.close()


@app.post("/api/connectors/introspect")
def introspect_connector(req:ConnectorIntrospectRequest,request:Request):
    config=req.config.model_copy(update={"read_only":True});identity=_identity(request)
    db=create_connector(config);db.receipt_sink=lambda **fields:repo().connector_receipt(identity.username,**fields)
    try:
        db.connect()
        repo().connector_receipt(identity.username,"connector.policy",config.connector,"allowed",metadata=db.policy_receipt())
        tables=db.introspect_system(req.schema_name)
        profiles={}
        if req.include_profiles:
            for table in tables:
                profiles[table.name]=db.profile_table(table.name,table.schema_name,req.profile_limit).model_dump(mode="json")
        repo().connector_receipt(identity.username,"connector.introspect",config.connector,"success",metadata={"table_count":len(tables),"include_profiles":req.include_profiles})
        return {"connector":config.connector,"tables":[t.model_dump(mode="json") for t in tables],"relationship_count":sum(len(t.foreign_keys) for t in tables),"profiles":profiles}
    except Exception as exc:
        repo().connector_receipt(identity.username,"connector.introspect",config.connector,"failure",metadata={"error_class":type(exc).__name__})
        raise HTTPException(status_code=400,detail=safe_error_text(str(exc),[config.password])) from exc
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
    if req.row_count>100_000:raise HTTPException(status_code=413,detail="Interactive API generation is limited to 100,000 rows; use a streaming job or CLI for larger exports")
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
    if req.row_count>100_000:raise HTTPException(status_code=413,detail="Interactive API exports are limited to 100,000 rows; use a streaming job or CLI for larger exports")
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
def direct_load(req:DirectLoadRequest,request:Request):
    # A dry run validates the target schema and builds the insert plan without
    # writing target data. It must not consume an approval intended for a real
    # load, even if the caller includes that approval_id in the request.
    if production_mode() and not req.dry_run:
        if not req.approval_id:raise HTTPException(status_code=403,detail="A separate, single-use administrator approval is required before a target write")
        clean=req.model_dump(mode="json",exclude={"approval_id"})
        payload_hash=hashlib.sha256(json.dumps(clean,sort_keys=True,separators=(",",":"),default=str).encode()).hexdigest()
        if not repo().consume_approval(req.approval_id,_identity(request).username,"direct-load",payload_hash):
            repo().audit("write.approval.rejected",req.table.name,{"action":"direct-load","reason":"invalid-expired-or-replayed"},actor=_identity(request).username)
            raise HTTPException(status_code=403,detail="Target write approval is invalid, expired, mismatched or already used")
    try:
        load_config=req.config
        if req.dry_run and req.config.connector=="sqlite":
            # SQLite creates a missing file when opened writable. A dry run
            # must not create the target as a side effect of schema checking.
            load_config=req.config.model_copy(update={"read_only":True})
        result=load_table(load_config,req.table,req.batch_size,req.dry_run,req.mode,req.confirm_destructive)
        if not req.dry_run:
            repo().audit("load.execute",f"{req.table.schema_name}.{req.table.name}",{"connector":req.config.connector,"dry_run":False,"mode":req.mode,"rows":len(req.table.rows)},actor=_identity(request).username)
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
    recipe=next((item for item in repo().list_recipes(project_id) if item.id==payload.recipe_id),None) if payload.recipe_id else None
    config=payload.request.model_dump(mode="json")
    contract={"generation_configuration":config,"privacy_policy":{"synthetic_only":True},"output_configuration":{"format":"zip","artifact":"dataset.zip"},"target_loading_policy":{"requires_separate_approval":True}}
    dataset=repo().create_dataset(project_id,payload.request.seed,[t.model_dump(mode="json") for t in payload.request.tables],{"scenario":payload.request.scenario}, {f"{t.schema_name}.{t.name}":len(t.rows) for t in result.tables},result.validation.model_dump(mode="json"),recipe_id=payload.recipe_id,parent_version=payload.parent_version,recipe_fingerprint=recipe.fingerprint if recipe else None,contract=contract)
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
def create_agent_run(payload:AgentRunCreateRequest,background_tasks:BackgroundTasks,request:Request):
    if production_mode() and payload.source_config and _identity(request).role!="admin":
        raise HTTPException(status_code=403,detail="Administrator role required for agent source connectors")
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
def approve_agent_load(run_id:str,payload:AgentLoadApprovalRequest,request:Request):
    try: run=repo().get_agent_run(run_id)
    except KeyError as exc: raise HTTPException(status_code=404,detail="Agent run not found") from exc
    if run.state!="completed" or not run.artifact_path:
        raise HTTPException(status_code=409,detail="Only a completed, packaged agent run can be loaded")
    if production_mode():
        if not payload.approval_id:raise HTTPException(status_code=403,detail="A separate, single-use administrator approval is required before a target write")
        artifact_hash=hashlib.sha256(Path(run.artifact_path).read_bytes()).hexdigest()
        context={"run_id":run_id,"artifact_sha256":artifact_hash,"request":payload.model_dump(mode="json",exclude={"approval_id"})}
        payload_hash=hashlib.sha256(json.dumps(context,sort_keys=True,separators=(",",":"),default=str).encode()).hexdigest()
        if not repo().consume_approval(payload.approval_id,_identity(request).username,"agent-load",payload_hash):
            repo().audit("write.approval.rejected",run_id,{"action":"agent-load","run_id":run_id,"reason":"invalid-expired-or-replayed"},actor=_identity(request).username)
            raise HTTPException(status_code=403,detail="Target write approval is invalid, expired, mismatched or already used")
    try:
        result=load_approved_artifact(run.artifact_path,payload)
    except Exception as exc:
        raise HTTPException(status_code=400,detail=safe_error_text(str(exc),[payload.config.password])) from exc
    run.approval_state="approved"; run.updated_at=datetime.now().astimezone(); repo().upsert_agent_run(run)
    repo().audit("agent.load.approved",run_id,{"connector":payload.config.connector,"mode":payload.mode,"rows":result.get("rows")},actor=_identity(request).username)
    return result


@app.post("/api/agent-runs/{run_id}/decline-load")
def decline_agent_load(run_id:str,request:Request):
    try: run=repo().get_agent_run(run_id)
    except KeyError as exc: raise HTTPException(status_code=404,detail="Agent run not found") from exc
    run.approval_state="declined"; run.updated_at=datetime.now().astimezone(); repo().upsert_agent_run(run)
    repo().audit("agent.load.declined",run_id,{},actor=_identity(request).username)
    return run.model_dump(mode="json")


@app.get("/api/audit")
def audit(limit:int=100): return repo().audit_events(limit)


@app.get("/api/connector-receipts")
def connector_receipts(limit:int=100,q:str|None=None,connector:str|None=None,policy:str|None=None):
    return repo().list_connector_receipts(limit,q,connector,policy)
