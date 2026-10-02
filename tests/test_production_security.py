from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import timedelta

from fastapi.testclient import TestClient

import app.main as mainmod
import app.persistence as persistencemod
from app.main import app
from app.models import ConnectorConfig, GeneratedTable, ProjectCreateRequest
from app.persistence import Repository
from app.security import hash_password, validate_production_environment
from app.connectors import create_connector
from app.connectors.base import ConnectorError


def production_environment(monkeypatch):
    password="test-password-with-more-than-fourteen-characters"
    monkeypatch.setenv("SYNTHETICFORGE_ENV","production")
    monkeypatch.setenv("SYNTHETICFORGE_ADMIN_USERNAME","admin")
    monkeypatch.setenv("SYNTHETICFORGE_ADMIN_PASSWORD_HASH",hash_password(password))
    monkeypatch.setenv("SYNTHETICFORGE_API_TOKEN_SHA256",hashlib.sha256(b"operator-test-token").hexdigest())
    monkeypatch.setenv("SYNTHETICFORGE_CORS_ORIGINS","https://forge.example.test")
    monkeypatch.setenv("SYNTHETICFORGE_APPLICATION_SHA","a"*40)
    monkeypatch.setenv("WEB_CONCURRENCY","1")
    monkeypatch.setenv("SYNTHETICFORGE_REPLICAS","1")
    monkeypatch.delenv("SYNTHETICFORGE_OPERATOR_USERNAME",raising=False)
    monkeypatch.delenv("SYNTHETICFORGE_OPERATOR_PASSWORD_HASH",raising=False)
    return password


def test_production_configuration_fails_closed(monkeypatch):
    monkeypatch.setenv("SYNTHETICFORGE_ENV","production")
    for key in ("SYNTHETICFORGE_ADMIN_PASSWORD_HASH","SYNTHETICFORGE_API_TOKEN_SHA256","SYNTHETICFORGE_CORS_ORIGINS","SYNTHETICFORGE_APPLICATION_SHA"):
        monkeypatch.delenv(key,raising=False)
    try:
        validate_production_environment()
    except RuntimeError as exc:
        assert "Production configuration missing" in str(exc)
    else:
        raise AssertionError("production accepted missing authentication and CORS settings")


def test_production_sessions_require_csrf_expire_on_logout_and_hide_secrets(tmp_path,monkeypatch):
    password=production_environment(monkeypatch)
    monkeypatch.setenv("SYNTHETICFORGE_ENV","development")
    repository=Repository(tmp_path/"state.db")
    mainmod._repo=repository
    monkeypatch.setenv("SYNTHETICFORGE_ENV","production")
    with TestClient(app,base_url="https://testserver") as client:
        denied=client.get("/api/projects")
        assert denied.status_code==401
        bad=client.post("/api/auth/login",json={"username":"admin","password":"wrong-password"})
        assert bad.status_code==401
        login=client.post("/api/auth/login",json={"username":"admin","password":password})
        assert login.status_code==200
        assert "csrf_token" in login.json() and password not in login.text
        assert "sf_session" in client.cookies
        assert client.post("/api/projects",json={"name":"csrf denied"}).status_code==403
        created=client.post("/api/projects",json={"name":"protected"},headers={"X-CSRF-Token":login.json()["csrf_token"]})
        assert created.status_code==200
        token="operator-test-token"
        with TestClient(app,base_url="https://testserver") as operator_client:
            admin_only=operator_client.get("/api/diagnostics",headers={"Authorization":f"Bearer {token}"})
            assert admin_only.status_code==403
        logout=client.post("/api/auth/logout",headers={"X-CSRF-Token":login.json()["csrf_token"]})
        assert logout.status_code==200
        assert client.get("/api/projects").status_code==401


def test_production_target_write_is_password_reauthenticated_exact_single_use(tmp_path,monkeypatch):
    password=production_environment(monkeypatch)
    monkeypatch.setenv("SYNTHETICFORGE_ENV","development")
    repository=Repository(tmp_path/"state.db")
    mainmod._repo=repository
    monkeypatch.setenv("SYNTHETICFORGE_ENV","production")
    target=tmp_path/"target.db"
    with sqlite3.connect(target) as connection:
        connection.execute("CREATE TABLE people(id INTEGER PRIMARY KEY,name TEXT NOT NULL)")
    write_request={"config":{"connector":"sqlite","path":str(target),"read_only":False},"table":{"name":"people","schema_name":"main","columns":[{"name":"id","data_type":"integer","primary_key":True},{"name":"name","data_type":"text","nullable":False}],"foreign_keys":[],"rows":[{"id":1,"name":"synthetic only"}]},"dry_run":False}
    with TestClient(app,base_url="https://testserver") as client:
        login=client.post("/api/auth/login",json={"username":"admin","password":password}).json()
        csrf={"X-CSRF-Token":login["csrf_token"]}
        assert client.post("/api/load",json=write_request,headers=csrf).status_code==403
        target_name=f"sqlite:{target}/main.people"
        # An approval bound to a different request cannot authorize this load.
        mismatched={**write_request,"batch_size":2}
        mismatch_approval=client.post("/api/write-approvals",headers=csrf,json={"password":password,"action":"direct-load","target":target_name,"direct_load_request":mismatched})
        assert mismatch_approval.status_code==200,mismatch_approval.text
        mismatch_req={**write_request,"approval_id":mismatch_approval.json()["approval_id"]}
        assert client.post("/api/load",json=mismatch_req,headers=csrf).status_code==403

        # Expiration is checked at consumption time, independently of the approval endpoint.
        expired_approval=client.post("/api/write-approvals",headers=csrf,json={"password":password,"action":"direct-load","target":target_name,"direct_load_request":write_request})
        assert expired_approval.status_code==200,expired_approval.text
        with monkeypatch.context() as expired_clock:
            expired_clock.setattr(persistencemod,"utcnow",lambda: persistencemod.datetime.now(persistencemod.timezone.utc)+timedelta(minutes=6))
            expired_req={**write_request,"approval_id":expired_approval.json()["approval_id"]}
            assert client.post("/api/load",json=expired_req,headers=csrf).status_code==403

        approval=client.post("/api/write-approvals",headers=csrf,json={"password":password,"action":"direct-load","target":target_name,"direct_load_request":write_request})
        assert approval.status_code==200,approval.text
        req={**write_request,"approval_id":approval.json()["approval_id"]}
        loaded=client.post("/api/load",json=req,headers=csrf)
        assert loaded.status_code==200,loaded.text
        assert loaded.json()["rows"]==1
        replay=client.post("/api/load",json=req,headers=csrf)
        assert replay.status_code==403
    with sqlite3.connect(target) as connection:
        assert connection.execute("SELECT COUNT(*) FROM people").fetchone()[0]==1
    with repository.connect() as connection:
        approvals=connection.execute("SELECT id,consumed_at FROM write_approvals ORDER BY rowid").fetchall()
        assert approvals[-1]["consumed_at"]
        try:connection.execute("UPDATE write_approvals SET action='agent-load'")
        except sqlite3.IntegrityError:pass
        else:raise AssertionError("immutable approval evidence accepted edits")


def test_production_dry_run_needs_no_approval_and_preserves_issued_approval(tmp_path,monkeypatch):
    password=production_environment(monkeypatch)
    monkeypatch.setenv("SYNTHETICFORGE_ENV","development")
    repository=Repository(tmp_path/"state.db")
    mainmod._repo=repository
    monkeypatch.setenv("SYNTHETICFORGE_ENV","production")
    target=tmp_path/"target.db"
    with sqlite3.connect(target) as connection:
        connection.execute("CREATE TABLE people(id INTEGER PRIMARY KEY,name TEXT NOT NULL)")
        connection.execute("INSERT INTO people VALUES(9,'existing')")
    write_request={"config":{"connector":"sqlite","path":str(target),"read_only":False},"table":{"name":"people","schema_name":"main","columns":[{"name":"id","data_type":"integer","primary_key":True},{"name":"name","data_type":"text","nullable":False}],"foreign_keys":[],"rows":[{"id":1,"name":"synthetic only"}]},"dry_run":False}
    target_name=f"sqlite:{target}/main.people"
    with TestClient(app,base_url="https://testserver") as client:
        login=client.post("/api/auth/login",json={"username":"admin","password":password}).json()
        csrf={"X-CSRF-Token":login["csrf_token"]}
        dry_run={**write_request,"dry_run":True}
        result=client.post("/api/load",json=dry_run,headers=csrf)
        assert result.status_code==200,result.text
        assert result.json()["dry_run"] is True
        missing_target=tmp_path/"dry-run-must-not-create.db"
        missing_file_result=client.post("/api/load",json={**dry_run,"config":{"connector":"sqlite","path":str(missing_target),"read_only":False}},headers=csrf)
        assert missing_file_result.status_code==400
        assert not missing_target.exists()
        with sqlite3.connect(target) as connection:
            assert connection.execute("SELECT * FROM people ORDER BY id").fetchall()==[(9,"existing")]

        issued=client.post("/api/write-approvals",headers=csrf,json={"password":password,"action":"direct-load","target":target_name,"direct_load_request":write_request})
        assert issued.status_code==200,issued.text
        approval_id=issued.json()["approval_id"]
        dry_with_approval={**dry_run,"approval_id":approval_id}
        preserved=client.post("/api/load",json=dry_with_approval,headers=csrf)
        assert preserved.status_code==200,preserved.text
        with repository.connect() as connection:
            assert connection.execute("SELECT consumed_at FROM write_approvals WHERE id=?",[approval_id]).fetchone()[0] is None
        with sqlite3.connect(target) as connection:
            assert connection.execute("SELECT * FROM people ORDER BY id").fetchall()==[(9,"existing")]

        # The unused approval remains valid for the exact non-dry-run request.
        loaded=client.post("/api/load",json={**write_request,"approval_id":approval_id},headers=csrf)
        assert loaded.status_code==200,loaded.text
        assert loaded.json()["rows"]==1
    with sqlite3.connect(target) as connection:
        assert connection.execute("SELECT * FROM people ORDER BY id").fetchall()==[(1,"synthetic only"),(9,"existing")]


def test_operator_cannot_probe_arbitrary_targets_or_source_connectors(tmp_path,monkeypatch):
    password=production_environment(monkeypatch)
    operator_password="operator-password-with-more-than-fourteen-characters"
    monkeypatch.setenv("SYNTHETICFORGE_OPERATOR_USERNAME","operator")
    monkeypatch.setenv("SYNTHETICFORGE_OPERATOR_PASSWORD_HASH",hash_password(operator_password))
    monkeypatch.setenv("SYNTHETICFORGE_ENV","development")
    repository=Repository(tmp_path/"state.db")
    mainmod._repo=repository
    monkeypatch.setenv("SYNTHETICFORGE_ENV","production")
    opened=[]
    monkeypatch.setattr(mainmod,"load_table",lambda *args,**kwargs: opened.append((args,kwargs)) or {"dry_run":True,"rows":0})
    probe={
        "config":{"connector":"postgresql","host":"198.51.100.1","port":5432,"database":"customer","username":"reader","password":"secret","read_only":False},
        "table":{"name":"accounts","schema_name":"public","columns":[{"name":"id","data_type":"integer","primary_key":True}],"foreign_keys":[],"rows":[{"id":1}]},
        "dry_run":True,
    }
    with TestClient(app,base_url="https://testserver") as client:
        login=client.post("/api/auth/login",json={"username":"operator","password":operator_password})
        assert login.status_code==200,login.text
        response=client.post("/api/load",json=probe,headers={"X-CSRF-Token":login.json()["csrf_token"]})
        assert response.status_code==403
        assert opened==[]  # The operator cannot trigger a DBAPI connection or schema inspection.
        agent_response=client.post("/api/agent-runs",json={
            "goal":"Profile a customer source and create a synthetic QA dataset",
            "source_config":probe["config"],
        },headers={"X-CSRF-Token":login.json()["csrf_token"]})
        assert agent_response.status_code==403
        assert opened==[]

        admin_login=client.post("/api/auth/login",json={"username":"admin","password":password})
        assert admin_login.status_code==200,admin_login.text
        admin_response=client.post("/api/load",json=probe,headers={"X-CSRF-Token":admin_login.json()["csrf_token"]})
        assert admin_response.status_code==200,admin_response.text
        assert admin_response.json()["dry_run"] is True
        assert len(opened)==1


def test_production_destructive_load_keeps_confirmation_gate(tmp_path,monkeypatch):
    password=production_environment(monkeypatch)
    monkeypatch.setenv("SYNTHETICFORGE_ENV","development")
    repository=Repository(tmp_path/"state.db")
    mainmod._repo=repository
    monkeypatch.setenv("SYNTHETICFORGE_ENV","production")
    target=tmp_path/"target.db"
    with sqlite3.connect(target) as connection:
        connection.execute("CREATE TABLE people(id INTEGER PRIMARY KEY,name TEXT NOT NULL)")
        connection.execute("INSERT INTO people VALUES(9,'existing')")
    write_request={"config":{"connector":"sqlite","path":str(target),"read_only":False},"table":{"name":"people","schema_name":"main","columns":[{"name":"id","data_type":"integer","primary_key":True},{"name":"name","data_type":"text","nullable":False}],"foreign_keys":[],"rows":[{"id":1,"name":"synthetic only"}]},"dry_run":False,"mode":"truncate","confirm_destructive":True}
    target_name=f"sqlite:{target}/main.people"
    with TestClient(app,base_url="https://testserver") as client:
        login=client.post("/api/auth/login",json={"username":"admin","password":password}).json()
        csrf={"X-CSRF-Token":login["csrf_token"]}
        unsafe_dry={**write_request,"dry_run":True,"confirm_destructive":False}
        assert client.post("/api/load",json=unsafe_dry,headers=csrf).status_code==400
        safe_dry={**write_request,"dry_run":True}
        assert client.post("/api/load",json=safe_dry,headers=csrf).status_code==200
        with sqlite3.connect(target) as connection:
            assert connection.execute("SELECT * FROM people").fetchall()==[(9,"existing")]

        phrase=f"CONFIRM DESTRUCTIVE WRITE TO {target_name}"
        missing_phrase=client.post("/api/write-approvals",headers=csrf,json={"password":password,"action":"direct-load","target":target_name,"confirm_destructive":True,"direct_load_request":write_request})
        assert missing_phrase.status_code==400
        approval=client.post("/api/write-approvals",headers=csrf,json={"password":password,"action":"direct-load","target":target_name,"confirm_destructive":True,"destructive_confirmation":phrase,"direct_load_request":write_request})
        assert approval.status_code==200,approval.text
        loaded=client.post("/api/load",headers=csrf,json={**write_request,"approval_id":approval.json()["approval_id"]})
        assert loaded.status_code==200,loaded.text
    with sqlite3.connect(target) as connection:
        assert connection.execute("SELECT * FROM people").fetchall()==[(1,"synthetic only")]


def test_interactive_api_bounds_large_generation(monkeypatch):
    monkeypatch.setenv("SYNTHETICFORGE_ENV","development")
    with TestClient(app) as client:
        payload={"database_type":"postgresql","database_name":"demo","table_name":"events","row_count":100_001,"columns":[{"name":"id","data_type":"integer","primary_key":True}]}
        assert client.post("/api/generate",json=payload).status_code==413
        assert client.post("/api/export/csv",json=payload).status_code==413


def test_production_denies_unproven_connector_read_only_and_hashes_query_receipts(tmp_path,monkeypatch):
    production_environment(monkeypatch)
    for connector in ("sqlserver","snowflake","bigquery","redshift"):
        adapter=create_connector(ConnectorConfig(connector=connector,host="unavailable.invalid",database="source",read_only=True))
        try:
            try:adapter.connect()
            except ConnectorError as exc:assert "read-only enforcement is unverified" in str(exc)
            else:raise AssertionError(f"{connector} connected as a production source without an enforcement proof")
        finally:adapter.close()

    source=tmp_path/"source.db"
    with sqlite3.connect(source) as con:
        con.execute("CREATE TABLE private_values(value TEXT)")
        con.execute("INSERT INTO private_values VALUES ('source-secret-row')")
    repository=Repository(tmp_path/"receipt.db")
    adapter=create_connector(ConnectorConfig(connector="sqlite",path=str(source),read_only=True))
    adapter.receipt_sink=lambda **fields:repository.connector_receipt("admin",**fields)
    try:
        adapter.connect()
        assert adapter.sample_rows("private_values")[0]["value"]=="source-secret-row"
    finally:adapter.close()
    receipts=repository.list_connector_receipts()
    assert receipts
    assert "source-secret-row" not in json.dumps(receipts)
    assert all(len(item["query_hash"])==64 for item in receipts if item["query_hash"])
