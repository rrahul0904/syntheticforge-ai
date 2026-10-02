from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from starlette.middleware.cors import CORSMiddleware

import app.ai as aimod
import app.main as mainmod
from app.main import app
from app.persistence import Repository
from app.security import hash_password, token_hash, validate_production_environment


def production_environment(monkeypatch):
    password = "test-password-with-more-than-fourteen-characters"
    token = "operator-test-token"
    monkeypatch.setenv("SYNTHETICFORGE_ENV", "production")
    monkeypatch.setenv("SYNTHETICFORGE_ADMIN_USERNAME", "admin")
    monkeypatch.setenv("SYNTHETICFORGE_ADMIN_PASSWORD_HASH", hash_password(password))
    monkeypatch.setenv("SYNTHETICFORGE_API_TOKEN_SHA256", hashlib.sha256(token.encode()).hexdigest())
    monkeypatch.setenv("SYNTHETICFORGE_CORS_ORIGINS", "https://forge.example.test")
    monkeypatch.setenv("SYNTHETICFORGE_APPLICATION_SHA", "a" * 40)
    monkeypatch.setenv("WEB_CONCURRENCY", "1")
    monkeypatch.setenv("SYNTHETICFORGE_REPLICAS", "1")
    monkeypatch.delenv("SYNTHETICFORGE_OPERATOR_USERNAME", raising=False)
    monkeypatch.delenv("SYNTHETICFORGE_OPERATOR_PASSWORD_HASH", raising=False)
    monkeypatch.delenv("SYNTHETICFORGE_ADMIN_API_TOKEN_SHA256", raising=False)
    return password, token


def install_repository(tmp_path, monkeypatch):
    monkeypatch.setenv("SYNTHETICFORGE_ENV", "development")
    repository = Repository(tmp_path / "state.db")
    mainmod._repo = repository
    monkeypatch.setenv("SYNTHETICFORGE_ENV", "production")
    return repository


def test_production_session_rotation_expiration_and_csrf(tmp_path, monkeypatch):
    password, _ = production_environment(monkeypatch)
    repository = install_repository(tmp_path, monkeypatch)
    attacker_chosen = "attacker-chosen-session-value"
    with TestClient(app, base_url="https://testserver") as client:
        client.cookies.set("sf_session", attacker_chosen, domain="testserver.local", path="/")
        assert client.get("/api/projects").status_code == 401
        login = client.post("/api/auth/login", json={"username": "admin", "password": password})
        assert login.status_code == 200
        session = next(cookie.value for cookie in client.cookies.jar if cookie.name == "sf_session")
        assert session and session != attacker_chosen
        csrf = login.json()["csrf_token"]

        assert client.post("/api/projects", json={"name": "csrf missing"}).status_code == 403
        assert client.post("/api/projects", json={"name": "csrf wrong"}, headers={"X-CSRF-Token": "0" * 64}).status_code == 403
        assert client.post("/api/projects", json={"name": "csrf valid"}, headers={"X-CSRF-Token": csrf}).status_code == 200

        with repository.connect() as connection:
            connection.execute(
                "UPDATE auth_sessions SET expires_at=? WHERE token_hash=?",
                [(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(), token_hash(session)],
            )
        assert client.get("/api/projects").status_code == 401


def test_production_bearer_token_cannot_reach_privileged_routes(tmp_path, monkeypatch):
    _, token = production_environment(monkeypatch)
    install_repository(tmp_path, monkeypatch)
    headers = {"Authorization": f"Bearer {token}"}
    with TestClient(app, base_url="https://testserver") as client:
        # Operator bearer tokens can use ordinary authenticated resources.
        assert client.get("/api/projects", headers=headers).status_code == 200
        assert client.get("/api/diagnostics", headers=headers).status_code == 403
        assert client.get("/api/provider-status", headers=headers).status_code == 403
        assert client.post("/api/provider-settings", headers=headers, json={"provider": "none"}).status_code == 403
        assert client.post("/api/write-approvals", headers=headers, json={"password": "x", "action": "direct-load", "target": "x"}).status_code == 403


def test_destructive_approval_rejects_changed_confirmation_flag(tmp_path, monkeypatch):
    password, _ = production_environment(monkeypatch)
    install_repository(tmp_path, monkeypatch)
    target = tmp_path / "destructive.db"
    with sqlite3.connect(target) as connection:
        connection.execute("CREATE TABLE people(id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
        connection.execute("INSERT INTO people VALUES(9, 'preserve-until-approved')")
    request = {
        "config": {"connector": "sqlite", "path": str(target), "read_only": False},
        "table": {
            "name": "people", "schema_name": "main",
            "columns": [
                {"name": "id", "data_type": "integer", "primary_key": True},
                {"name": "name", "data_type": "text", "nullable": False},
            ],
            "foreign_keys": [], "rows": [{"id": 1, "name": "approved-row"}],
        },
        "dry_run": False, "mode": "truncate", "confirm_destructive": True,
    }
    target_name = f"sqlite:{target}/main.people"
    with TestClient(app, base_url="https://testserver") as client:
        login = client.post("/api/auth/login", json={"username": "admin", "password": password}).json()
        csrf = {"X-CSRF-Token": login["csrf_token"]}
        issued = client.post(
            "/api/write-approvals", headers=csrf,
            json={
                "password": password, "action": "direct-load", "target": target_name,
                "confirm_destructive": True,
                "destructive_confirmation": f"CONFIRM DESTRUCTIVE WRITE TO {target_name}",
                "direct_load_request": request,
            },
        )
        assert issued.status_code == 200, issued.text
        approval_id = issued.json()["approval_id"]
        altered = {**request, "confirm_destructive": False, "approval_id": approval_id}
        assert client.post("/api/load", json=altered, headers=csrf).status_code == 403
        with sqlite3.connect(target) as connection:
            assert connection.execute("SELECT * FROM people").fetchall() == [(9, "preserve-until-approved")]
        valid = client.post("/api/load", json={**request, "approval_id": approval_id}, headers=csrf)
        assert valid.status_code == 200, valid.text
    with sqlite3.connect(target) as connection:
        assert connection.execute("SELECT * FROM people").fetchall() == [(1, "approved-row")]


def test_production_http_login_and_unsafe_cors_and_replica_settings_fail_closed(tmp_path, monkeypatch):
    password, _ = production_environment(monkeypatch)
    install_repository(tmp_path, monkeypatch)
    with TestClient(app, base_url="http://testserver") as client:
        response = client.post("/api/auth/login", json={"username": "admin", "password": password})
        assert response.status_code == 400
        assert "sf_session" not in client.cookies

    for origin in ("*", "http://forge.example.test", "https://forge.example.test/path", "https://user@forge.example.test"):
        monkeypatch.setenv("SYNTHETICFORGE_CORS_ORIGINS", origin)
        with pytest.raises(RuntimeError, match="Production CORS origins must be explicit HTTPS origins"):
            validate_production_environment()
    monkeypatch.setenv("SYNTHETICFORGE_CORS_ORIGINS", "https://forge.example.test")
    for key in ("WEB_CONCURRENCY", "SYNTHETICFORGE_REPLICAS"):
        monkeypatch.setenv(key, "2")
        with pytest.raises(RuntimeError, match="exactly one application (worker|replica)"):
            validate_production_environment()
        monkeypatch.setenv(key, "1")

    cors_config = next(middleware for middleware in app.user_middleware if middleware.cls is CORSMiddleware)
    original_origins = cors_config.kwargs["allow_origins"]
    cors_config.kwargs["allow_origins"] = ["https://forge.example.test"]
    app.middleware_stack = None
    try:
        with TestClient(app, base_url="https://testserver") as client:
            bare_options = client.options("/api/projects")
            assert bare_options.status_code == 405
            assert "projects" not in bare_options.text.lower()
            preflight = client.options(
                "/api/projects",
                headers={"Origin": "https://attacker.invalid", "Access-Control-Request-Method": "POST"},
            )
            assert preflight.status_code == 400
            assert "access-control-allow-origin" not in preflight.headers
            trusted_preflight = client.options(
                "/api/projects",
                headers={"Origin": "https://forge.example.test", "Access-Control-Request-Method": "POST"},
            )
            assert trusted_preflight.status_code == 200
            assert trusted_preflight.headers["access-control-allow-origin"] == "https://forge.example.test"
            assert client.get("/api/projects").status_code == 401
    finally:
        cors_config.kwargs["allow_origins"] = original_origins
        app.middleware_stack = None


def test_schema_parser_bounds_malformed_and_unsafe_sqlite_inputs(tmp_path, monkeypatch):
    password, _ = production_environment(monkeypatch)
    install_repository(tmp_path, monkeypatch)
    target = tmp_path / "metadata.db"
    with sqlite3.connect(target) as connection:
        connection.execute("CREATE TABLE people(id INTEGER PRIMARY KEY, name TEXT)")
        connection.execute("INSERT INTO people VALUES(1, 'private-row-marker')")

    with TestClient(app, base_url="https://testserver") as client:
        login = client.post("/api/auth/login", json={"username": "admin", "password": password}).json()
        headers = {"X-CSRF-Token": login["csrf_token"], "Content-Type": "application/json"}
        malformed = client.post("/api/parse-schema", content="{", headers=headers)
        assert 400 <= malformed.status_code < 500

        monkeypatch.setenv("SYNTHETICFORGE_MAX_UPLOAD_BYTES", "128")
        oversized = client.post(
            "/api/parse-schema",
            json={"database_type": "sqlite", "database_name": "x", "input_format": "ddl", "content": "x" * 129},
            headers=headers,
        )
        assert oversized.status_code == 413
        monkeypatch.setenv("SYNTHETICFORGE_MAX_UPLOAD_BYTES", str(5 * 1024 * 1024))

        parsed = client.post(
            "/api/parse-schema",
            json={"database_type": "sqlite", "database_name": "metadata", "input_format": "sqlite-db", "content": str(target)},
            headers=headers,
        )
        assert parsed.status_code == 200, parsed.text
        assert "people" in {table["name"] for table in parsed.json()["tables"]}
        assert "private-row-marker" not in parsed.text

        bad_identifier = client.post(
            "/api/load",
            json={
                "config": {"connector": "sqlite", "path": str(target), "read_only": False},
                "table": {
                    "name": 'people"; DROP TABLE people;--',
                        "schema_name": "main",
                        "columns": [{"name": "id", "data_type": "integer", "primary_key": True}],
                        "foreign_keys": [],
                        "rows": [{"id": 2}],
                },
                "dry_run": True,
            },
            headers=headers,
        )
        assert bad_identifier.status_code == 400
    with sqlite3.connect(target) as connection:
        assert connection.execute("SELECT * FROM people").fetchall() == [(1, "private-row-marker")]


def test_ai_key_is_not_returned_or_written_to_audit(tmp_path, monkeypatch):
    password, _ = production_environment(monkeypatch)
    repository = install_repository(tmp_path, monkeypatch)
    secret = "ai-key-never-return-or-persist"
    with TestClient(app, base_url="https://testserver") as client:
        login = client.post("/api/auth/login", json={"username": "admin", "password": password}).json()
        headers = {"X-CSRF-Token": login["csrf_token"]}
        configured = client.post(
            "/api/provider-settings",
            headers=headers,
            json={"provider": "openai-compatible", "model": "test-model", "base_url": "https://provider.example.test/v1", "api_key": secret},
        )
        assert configured.status_code == 200
        assert secret not in configured.text
        status = client.get("/api/provider-status")
        assert status.status_code == 200
        assert status.json()["has_api_key"] is True
        assert secret not in status.text
        assert secret not in json.dumps(repository.audit_events())
        client.delete("/api/provider-settings", headers=headers)
    aimod.clear_provider_session()


def test_production_configuration_rejects_missing_secrets_and_invalid_scale(monkeypatch):
    production_environment(monkeypatch)
    monkeypatch.delenv("SYNTHETICFORGE_API_TOKEN_SHA256")
    with pytest.raises(RuntimeError, match="Production configuration missing"):
        validate_production_environment()
