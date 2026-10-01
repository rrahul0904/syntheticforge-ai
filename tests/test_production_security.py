from __future__ import annotations

import hashlib
import json
import sqlite3

from fastapi.testclient import TestClient

import app.main as mainmod
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
        assert connection.execute("SELECT consumed_at FROM write_approvals").fetchone()[0]
        try:connection.execute("UPDATE write_approvals SET action='agent-load'")
        except sqlite3.IntegrityError:pass
        else:raise AssertionError("immutable approval evidence accepted edits")


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
