from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from fastapi.testclient import TestClient

import app.main as mainmod
from app.agent import AgentExecutor
import app.agent.executor as agent_executor_module
import app.ai as ai_module
from app.agent.planner import normalize_plan
from app.agent.repair import repair_result
from app.ai import clear_provider_session, configure_provider_session
from app.main import app
from app.models import (
    AgentRunCreateRequest,
    ConnectorConfig,
    ParseSchemaRequest,
    SystemGenerateRequest,
    AIProviderSessionRequest,
)
from app.persistence import Repository
from app.schema_parser import parse_schema
from app.system_generator import generate_system
from app.validation import validate_system

client = TestClient(app)


def fresh_repo(tmp_path: Path) -> Repository:
    clear_provider_session()
    repo = Repository(tmp_path / "state.db")
    mainmod._repo = repo
    mainmod._agent_executor = None
    return repo


def hospitality_request(**updates):
    data = dict(
        goal="Create a hospitality reservation platform with hotels, rooms, guests, reservations, payments, cancellations, loyalty accounts, invoices, refunds, rate plans and services. Make 12% cancellations.",
        database_type="postgresql",
        database_name="hotel",
        schema_name="prod",
        default_row_count=12,
        seed=42,
        quality_threshold=95,
        max_repairs=2,
        ai_planning=False,
    )
    data.update(updates)
    return AgentRunCreateRequest(**data)


def test_planner_rejects_unknown_and_restores_mandatory_gates():
    plan = normalize_plan(["shell", "generate_dataset", "package_dataset"], has_source=False)
    assert "shell" not in plan
    assert plan == [
        "model_system", "classify_sensitive_data", "infer_business_rules",
        "generate_dataset", "validate_dataset", "package_dataset",
    ]


def test_source_plan_forces_read_only_inspection_before_generation():
    plan = normalize_plan(["validate_dataset"], has_source=True)
    assert plan[:2] == ["inspect_source", "profile_source"]
    assert plan.index("validate_dataset") < plan.index("package_dataset")


def test_agent_request_persistence_redacts_source_password(tmp_path):
    repo = fresh_repo(tmp_path)
    executor = AgentExecutor(repo)
    req = hospitality_request(source_config=ConnectorConfig(connector="postgresql", host="localhost", database="x", username="u", password="very-secret"))
    run = executor.create(req)
    stored = repo.get_agent_run(run.id)
    assert stored.request["source_config"]["password"] == "***"
    assert "very-secret" not in str(stored.model_dump(mode="json"))


def test_deterministic_agent_completes_quality_gate_and_persists_artifact(tmp_path):
    repo = fresh_repo(tmp_path)
    executor = AgentExecutor(repo)
    req = hospitality_request()
    run = executor.create(req)
    done = executor.execute(run.id, req)
    assert done.state == "completed"
    assert done.quality_score is not None and done.quality_score >= 95
    assert done.dataset_id
    assert done.artifact_path and Path(done.artifact_path).is_file()
    assert any(e.kind == "plan" for e in done.trace)
    assert any(e.kind == "validation" for e in done.trace)
    assert done.approval_state == "pending"


def test_agent_execution_restores_mandatory_gates_after_untrusted_plan(tmp_path, monkeypatch):
    repo = fresh_repo(tmp_path)
    executor = AgentExecutor(repo)
    req = hospitality_request(ai_planning=True)

    async def malicious_plan(*_args, **_kwargs):
        return ["shell", "package_dataset", "skip_validation", "package_dataset"]

    monkeypatch.setattr(agent_executor_module, "plan_agent_with_provider", malicious_plan)
    run = executor.create(req)
    done = executor.execute(run.id, req)

    assert done.state == "completed"
    assert done.plan == normalize_plan(["shell", "package_dataset", "skip_validation"], has_source=False)
    assert done.plan.index("validate_dataset") < done.plan.index("package_dataset")
    assert "shell" not in done.plan and "skip_validation" not in done.plan
    plan_trace = next(event for event in done.trace if event.kind == "plan")
    assert plan_trace.details["ai_proposed"] == ["shell", "package_dataset", "skip_validation", "package_dataset"]


@pytest.mark.parametrize("malformed", [None, "not-json", {"steps": "skip_validation"}, {"tables": "not-a-list"}])
def test_malformed_ai_output_falls_back_to_deterministic_model_and_plan(tmp_path, monkeypatch, malformed):
    fresh_repo(tmp_path)

    async def malformed_response(*_args, **_kwargs):
        return malformed

    monkeypatch.setattr(ai_module, "_json_completion", malformed_response)
    req = hospitality_request(ai_planning=True)
    executor = AgentExecutor(mainmod._repo)
    run = executor.create(req)
    done = executor.execute(run.id, req)

    assert done.state == "completed", done.error
    assert done.plan == normalize_plan(None, has_source=False)
    assert done.dataset_id and Path(done.artifact_path).is_file()
    assert any(event.kind == "validation" and event.details.get("passed") for event in done.trace)


def test_provider_timeout_falls_back_to_deterministic_agent(tmp_path, monkeypatch):
    repo = fresh_repo(tmp_path)

    class TimeoutClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def post(self, *_args, **_kwargs):
            raise httpx.ReadTimeout("simulated provider timeout")

    monkeypatch.setattr(ai_module.httpx, "AsyncClient", TimeoutClient)
    configure_provider_session(AIProviderSessionRequest(
        provider="openai-compatible", model="timeout-test", base_url="https://provider.invalid/v1", api_key="test-secret",
    ))
    try:
        req = hospitality_request(ai_planning=True)
        executor = AgentExecutor(repo)
        run = executor.create(req)
        done = executor.execute(run.id, req)
        assert done.state == "completed", done.error
        assert done.plan == normalize_plan(None, has_source=False)
        assert done.quality_score is not None and done.quality_score >= req.quality_threshold
        assert "test-secret" not in str(done.model_dump(mode="json"))
    finally:
        clear_provider_session()


def test_relational_repair_is_revalidated_and_persisted_on_agent_run(tmp_path, monkeypatch):
    repo = fresh_repo(tmp_path)
    executor = AgentExecutor(repo)
    req = hospitality_request(
        goal="Create a hotel reservation system with reservations and payments",
        scenario=None,
        default_row_count=20,
        quality_threshold=95,
        max_repairs=2,
        ai_planning=False,
    )
    generate = agent_executor_module.generate_system
    first_report = {}

    def generate_with_relational_defects(system_request):
        result = generate(system_request)
        reservations = next(table for table in result.tables if table.name == "reservations")
        payments = next(table for table in result.tables if table.name == "payments")
        assert len(reservations.rows) > 1 and payments.rows
        reservations.rows[0]["reservation_id"] = reservations.rows[1]["reservation_id"]
        reservations.rows[0]["check_out_date"] = "2000-01-01"
        reservations.rows[0]["status"] = "invalid-status"
        payments.rows[0]["reservation_id"] = -999999
        result.validation = validate_system(system_request, result.tables)
        first_report["failed"] = {check.name for check in result.validation.checks if not check.passed}
        return result

    monkeypatch.setattr(agent_executor_module, "generate_system", generate_with_relational_defects)
    run = executor.create(req)
    done = executor.execute(run.id, req)
    persisted = repo.get_agent_run(run.id)

    assert first_report["failed"] >= {"primary_key_uniqueness", "foreign_key_integrity", "date_coherence", "enum_membership"}
    assert done.state == "completed", (done.error, [event.model_dump(mode="json") for event in persisted.trace if event.kind == "validation"])
    assert done.repair_attempts >= 1
    assert done.quality_score == 100
    assert any(event.kind == "repair" for event in persisted.trace)
    validations = [event for event in persisted.trace if event.kind == "validation"]
    assert len(validations) >= 2
    assert validations[0].details["passed"] is False
    assert validations[-1].details["passed"] is True
    dataset = repo.get_dataset(done.dataset_id)
    assert dataset.validation["passed"] is True
    assert dataset.validation["quality_score"] == 100


def test_cross_table_refund_amount_rule_detects_overpayment():
    tables, _ = parse_schema(ParseSchemaRequest(
        database_type="postgresql", database_name="hotel", schema_name="prod", input_format="description",
        content="Create a hotel reservation system with reservations, payments and refunds", default_row_count=20,
    ))
    req = SystemGenerateRequest(database_type="postgresql", database_name="hotel", schema_name="prod", tables=tables, default_row_count=20)
    result = generate_system(req)
    refunds = next(table for table in result.tables if table.name == "refunds")
    payments = next(table for table in result.tables if table.name == "payments")
    assert refunds.rows and payments.rows
    payment_by_id = {row["payment_id"]: row for row in payments.rows}
    refund = refunds.rows[0]
    parent_payment = payment_by_id[refund["payment_id"]]
    refund["amount"] = float(parent_payment["amount"]) + 1000

    report = validate_system(req, result.tables)
    semantic_check = next(check for check in report.checks if check.name == "cross_table_business_semantics")
    assert semantic_check.passed is False
    assert "refunds.amount>payments" in semantic_check.details


def test_repair_turns_failed_date_validation_into_pass():
    tables, _ = parse_schema(ParseSchemaRequest(
        database_type="postgresql", database_name="hotel", schema_name="prod", input_format="description",
        content="Create a hotel reservation system with reservations and payments", default_row_count=20,
    ))
    req = SystemGenerateRequest(database_type="postgresql", database_name="hotel", schema_name="prod", tables=tables, default_row_count=20)
    result = generate_system(req)
    reservation = next(t for t in result.tables if t.name == "reservations")
    original_ids = [row["reservation_id"] for row in reservation.rows]
    reservation.rows[0]["check_out_date"] = "2000-01-01"
    result.validation = validate_system(req, result.tables)
    assert result.validation.quality_score < 95
    repaired = repair_result(req, result, 1)
    assert repaired.validation.passed
    assert repaired.validation.quality_score >= 95
    repaired_reservations = next(t for t in repaired.tables if t.name == "reservations")
    assert [row["reservation_id"] for row in repaired_reservations.rows] == original_ids


def test_agent_api_run_trace_and_artifact(tmp_path):
    fresh_repo(tmp_path)
    response = client.post("/api/agent-runs", json=hospitality_request().model_dump(mode="json"))
    assert response.status_code == 200, response.text
    run_id = response.json()["id"]
    run = client.get(f"/api/agent-runs/{run_id}").json()
    assert run["state"] == "completed"
    assert run["quality_score"] >= 95
    assert any(e["kind"] == "tool" for e in run["trace"])
    artifact = client.get(f"/api/agent-runs/{run_id}/artifact")
    assert artifact.status_code == 200
    assert artifact.headers["content-type"] == "application/zip"
    assert artifact.content.startswith(b"PK")


def test_agent_cancel_is_persisted(tmp_path):
    repo = fresh_repo(tmp_path)
    executor = AgentExecutor(repo)
    run = executor.create(hospitality_request())
    cancelled = executor.cancel(run.id)
    assert cancelled.state == "cancelled"
    assert repo.get_agent_run(run.id).state == "cancelled"


def test_target_load_requires_explicit_writable_target(tmp_path):
    fresh_repo(tmp_path)
    run = client.post("/api/agent-runs", json=hospitality_request().model_dump(mode="json")).json()
    response = client.post(f"/api/agent-runs/{run['id']}/approve-load", json={
        "config": {"connector": "sqlite", "path": str(tmp_path / "target.db"), "read_only": True},
        "mode": "append",
    })
    assert response.status_code == 400
    assert "read_only=false" in response.json()["detail"]


def test_session_ai_settings_never_return_or_audit_key(tmp_path):
    repo = fresh_repo(tmp_path)
    secret = "sk-test-never-persist-this"
    response = client.post("/api/provider-settings", json={
        "provider": "openai-compatible", "model": "test-model", "base_url": "http://localhost:9999/v1", "api_key": secret,
    })
    assert response.status_code == 200
    body = response.json()
    assert body["configured"] is True and body["has_api_key"] is True
    assert secret not in response.text
    status = client.get("/api/provider-status")
    assert secret not in status.text
    assert secret not in str(repo.audit_events(100))
    cleared = client.delete("/api/provider-settings").json()
    assert cleared["configured"] is False


def test_health_and_diagnostics_report_agentic_version(tmp_path):
    fresh_repo(tmp_path)
    assert client.get("/api/health").json()["version"] == "1.0.0-rc1"
    diag = client.get("/api/diagnostics").json()
    assert diag["version"] == "1.0.0-rc1"
    assert "agent_runs" in diag


def test_ui_exposes_agent_runs_and_session_settings(tmp_path):
    fresh_repo(tmp_path)
    html = client.get("/").text
    assert "Agent runs" in html
    assert "AGENTIC SYNTHETIC DATA" in html
    assert "LOCAL AI CONFIGURATION" in html
    assert "Start agent run" in html


def test_job_retry_uses_persisted_request(tmp_path):
    fresh_repo(tmp_path)
    payload = {
        "request": {
            "database_type": "postgresql", "database_name": "x", "schema_name": "public", "default_row_count": 5,
            "seed": 42, "tables": [{"name": "things", "row_count": 5, "columns": [{"name": "thing_id", "data_type": "bigint", "semantic_type": "id", "primary_key": True}]}],
        }
    }
    first = client.post("/api/jobs/generate", json=payload)
    assert first.status_code == 200
    retried = client.post(f"/api/jobs/{first.json()['id']}/retry")
    assert retried.status_code == 200, retried.text
    jobs = client.get("/api/jobs").json()
    assert len(jobs) >= 2


def test_agent_list_endpoint_returns_persisted_runs(tmp_path):
    fresh_repo(tmp_path)
    r1 = client.post("/api/agent-runs", json=hospitality_request(seed=1).model_dump(mode="json")).json()
    r2 = client.post("/api/agent-runs", json=hospitality_request(seed=2).model_dump(mode="json")).json()
    runs = client.get("/api/agent-runs").json()
    ids = {r["id"] for r in runs}
    assert {r1["id"], r2["id"]} <= ids


def test_planner_keeps_privacy_and_validation_before_package_with_source():
    plan = normalize_plan(["package_dataset", "inspect_source", "unknown_tool"], has_source=True)
    assert "unknown_tool" not in plan
    assert plan.index("inspect_source") < plan.index("classify_sensitive_data")
    assert plan.index("classify_sensitive_data") < plan.index("validate_dataset")
    assert plan.index("validate_dataset") < plan.index("package_dataset")


def test_provider_status_exposes_boolean_not_secret(tmp_path):
    fresh_repo(tmp_path)
    secret = "local-secret-value"
    client.post("/api/provider-settings", json={
        "provider": "openai-compatible", "model": "model-x", "base_url": "http://localhost:9999/v1", "api_key": secret,
    })
    payload = client.get("/api/provider-status").json()
    assert payload["has_api_key"] is True
    assert "api_key" not in payload
    assert secret not in str(payload)
    client.delete("/api/provider-settings")


def test_agent_decline_load_is_persisted(tmp_path):
    fresh_repo(tmp_path)
    run = client.post("/api/agent-runs", json=hospitality_request().model_dump(mode="json")).json()
    response = client.post(f"/api/agent-runs/{run['id']}/decline-load")
    assert response.status_code == 200
    assert response.json()["approval_state"] == "declined"
    assert client.get(f"/api/agent-runs/{run['id']}").json()["approval_state"] == "declined"


def test_agent_artifact_is_immutable_dataset_record(tmp_path):
    repo = fresh_repo(tmp_path)
    created = client.post("/api/agent-runs", json=hospitality_request(seed=99).model_dump(mode="json")).json()
    run = client.get(f"/api/agent-runs/{created['id']}").json()
    assert run["dataset_id"]
    dataset = repo.get_dataset(run["dataset_id"])
    assert dataset.version >= 1
    assert dataset.generator_version == "1.0.0rc1"
    assert dataset.validation["quality_score"] >= 95
    assert dataset.exports and Path(dataset.exports[0]).is_file()
