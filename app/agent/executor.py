from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..ai import model_system_with_provider, plan_agent_with_provider, repair_advice_with_provider
from ..connectors import create_connector
from ..models import (
    AgentRunCreateRequest,
    AgentRunRecord,
    AgentTraceEvent,
    GeneratedTable,
    ParseSchemaRequest,
    ProjectCreateRequest,
    SystemGenerateRequest,
    SystemModelingRequest,
    TableSpec,
)
from ..persistence import Repository
from ..privacy import classify_columns
from ..profiling import apply_profile_to_columns
from ..rules import infer_business_rules
from ..schema_parser import parse_schema
from ..security import redact_secrets, safe_error_text
from ..system_generator import generate_system, system_to_zip
from ..validation import validate_system
from .planner import ALLOWED_TOOLS, normalize_plan
from .repair import repair_result


def now() -> datetime:
    return datetime.now(timezone.utc)


def _run_async(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    # Agent execution is expected in a sync worker. If this path is ever hit, use a dedicated thread loop.
    import threading
    box: dict[str, Any] = {}
    error: list[BaseException] = []
    def runner():
        try:
            box["value"] = asyncio.run(coro)
        except BaseException as exc:  # pragma: no cover - defensive path
            error.append(exc)
    t = threading.Thread(target=runner)
    t.start(); t.join()
    if error:
        raise error[0]
    return box.get("value")


class AgentExecutor:
    def __init__(self, repository: Repository):
        self.repo = repository
        self._cancelled: set[str] = set()

    def create(self, req: AgentRunCreateRequest) -> AgentRunRecord:
        ts = now()
        record = AgentRunRecord(
            id=uuid.uuid4().hex,
            goal=req.goal,
            project_id=req.project_id,
            state="queued",
            current_step="queued",
            quality_threshold=req.quality_threshold,
            request=redact_secrets(req.model_dump(mode="json")),
            created_at=ts,
            updated_at=ts,
        )
        self.repo.upsert_agent_run(record)
        self.repo.audit("agent.create", record.id, {"goal": req.goal[:500], "project_id": req.project_id})
        return record

    def cancel(self, run_id: str) -> AgentRunRecord:
        self._cancelled.add(run_id)
        run = self.repo.get_agent_run(run_id)
        run.state = "cancelled"; run.current_step = "cancelled"; run.updated_at = now()
        self._trace(run, "result", "cancelled", "Cancellation requested by user")
        self.repo.upsert_agent_run(run)
        return run

    def _check_cancelled(self, run_id: str) -> None:
        if run_id in self._cancelled or self.repo.get_agent_run(run_id).state == "cancelled":
            raise RuntimeError("Agent run cancelled")

    def _trace(self, run: AgentRunRecord, kind: str, step: str, summary: str, details: dict[str, Any] | None = None) -> None:
        run.trace.append(AgentTraceEvent(id=uuid.uuid4().hex, created_at=now(), kind=kind, step=step, summary=summary[:2000], details=redact_secrets(details or {})))
        run.updated_at = now()
        self.repo.upsert_agent_run(run)

    def _set(self, run: AgentRunRecord, *, state: str | None = None, step: str | None = None, quality: float | None = None, error: str | None = None) -> None:
        if state is not None: run.state = state  # type: ignore[assignment]
        if step is not None: run.current_step = step
        if quality is not None: run.quality_score = quality
        if error is not None: run.error = safe_error_text(error)
        run.updated_at = now(); self.repo.upsert_agent_run(run)

    def _model_without_source(self, req: AgentRunCreateRequest) -> list[TableSpec]:
        if req.input_format and req.content:
            tables, _ = parse_schema(ParseSchemaRequest(
                database_type=req.database_type, database_name=req.database_name, schema_name=req.schema_name,
                input_format=req.input_format, content=req.content, default_row_count=req.default_row_count,
            ))
            return tables
        if req.ai_planning:
            ai_tables = _run_async(model_system_with_provider(SystemModelingRequest(
                database_type=req.database_type, database_name=req.database_name, schema_name=req.schema_name,
                description=req.goal, default_row_count=req.default_row_count, ai_inference=True,
            )))
            if ai_tables:
                return ai_tables
        tables, _ = parse_schema(ParseSchemaRequest(
            database_type=req.database_type, database_name=req.database_name, schema_name=req.schema_name,
            input_format="description", content=req.goal, default_row_count=req.default_row_count,
        ))
        return tables

    def execute(self, run_id: str, req: AgentRunCreateRequest) -> AgentRunRecord:
        run = self.repo.get_agent_run(run_id)
        connector = None
        profiles: dict[str, Any] = {}
        tables: list[TableSpec] = []
        result = None
        try:
            self._set(run, state="planning", step="planning")
            proposed = _run_async(plan_agent_with_provider(req.goal, ALLOWED_TOOLS, {"has_source": bool(req.source_config)})) if req.ai_planning else None
            plan = normalize_plan(proposed, bool(req.source_config))
            run.plan = plan
            self._trace(run, "plan", "planning", f"Created constrained {len(plan)}-step plan", {"steps": plan, "ai_proposed": proposed or []})
            self._set(run, state="running", step=plan[0])

            for step in plan:
                self._check_cancelled(run_id)
                self._set(run, state="running", step=step)
                self._trace(run, "tool", step, f"Executing {step}")

                if step == "inspect_source":
                    if not req.source_config:
                        raise RuntimeError("inspect_source requires source_config")
                    source_cfg = req.source_config.model_copy(deep=True)
                    source_cfg.read_only = True
                    connector = create_connector(source_cfg)
                    connector.connect()
                    tables = connector.introspect_system(req.schema_name or source_cfg.schema_name)
                    self._trace(run, "observation", step, f"Discovered {len(tables)} source tables", {"relationships": sum(len(t.foreign_keys) for t in tables), "read_only": True})

                elif step == "model_system":
                    tables = self._model_without_source(req)
                    self._trace(run, "observation", step, f"Modeled {len(tables)} tables", {"relationships": sum(len(t.foreign_keys) for t in tables)})

                elif step == "profile_source":
                    if connector is not None:
                        for table in tables:
                            sample = connector.sample_rows(table.name, table.schema_name, min(1000, max(req.default_row_count, 100)))
                            classified = classify_columns(table.columns, sample)
                            profile = connector.profile_table(table.name, table.schema_name, min(1000, max(req.default_row_count, 100)))
                            table.columns = apply_profile_to_columns(classified, profile)
                            table.correlations = profile.correlations
                            profiles[f"{table.schema_name}.{table.name}"] = profile.model_dump(mode="json")
                        self._trace(run, "observation", step, f"Profiled bounded samples for {len(tables)} tables", {"sample_limit_per_table": 1000})
                    else:
                        self._trace(run, "observation", step, "No live source; profiling step skipped safely")

                elif step == "classify_sensitive_data":
                    counts = {"personal": 0, "sensitive-pii": 0, "potential-phi": 0, "secret": 0}
                    for table in tables:
                        table.columns = classify_columns(table.columns)
                        for col in table.columns:
                            if col.sensitivity in counts: counts[col.sensitivity] += 1
                    self._trace(run, "observation", step, "Sensitive-field classification completed", counts)

                elif step == "infer_business_rules":
                    total = 0
                    for table in tables:
                        table.business_rules = infer_business_rules(table)
                        total += len(table.business_rules)
                    self._trace(run, "observation", step, f"Prepared {total} business rules")

                elif step == "generate_dataset":
                    gen_req = SystemGenerateRequest(
                        database_type=req.database_type, database_name=req.database_name, schema_name=req.schema_name,
                        default_row_count=req.default_row_count, seed=req.seed, scenario=req.scenario or req.goal,
                        tables=tables, negative_testing=False, edge_case_rate=0.0,
                    )
                    result = generate_system(gen_req)
                    self._trace(run, "observation", step, f"Generated {sum(len(t.rows) for t in result.tables)} rows across {len(result.tables)} tables")

                elif step == "validate_dataset":
                    if result is None:
                        raise RuntimeError("validate_dataset requires generated data")
                    quality = float(result.validation.quality_score)
                    self._set(run, quality=quality)
                    failed = [c.model_dump(mode="json") for c in result.validation.checks if not c.passed and c.severity == "error"]
                    self._trace(run, "validation", step, f"Validation quality {quality:.2f}", {"passed": result.validation.passed, "failed_checks": failed[:50]})
                    if quality < req.quality_threshold:
                        gen_req = SystemGenerateRequest(
                            database_type=req.database_type, database_name=req.database_name, schema_name=req.schema_name,
                            default_row_count=req.default_row_count, seed=req.seed, scenario=req.scenario or req.goal,
                            tables=tables, negative_testing=False, edge_case_rate=0.0,
                        )
                        while quality < req.quality_threshold and run.repair_attempts < req.max_repairs:
                            self._check_cancelled(run_id)
                            run.repair_attempts += 1
                            self._set(run, state="repairing", step="repair_dataset")
                            advice = _run_async(repair_advice_with_provider(req.goal, failed)) if req.ai_planning else None
                            self._trace(run, "repair", "repair_dataset", f"Repair attempt {run.repair_attempts}", {"ai_advice": advice or [], "failed_checks": [x.get("name") for x in failed]})
                            result = repair_result(gen_req, result, run.repair_attempts)
                            quality = float(result.validation.quality_score)
                            failed = [c.model_dump(mode="json") for c in result.validation.checks if not c.passed and c.severity == "error"]
                            self._set(run, state="running", step="validate_dataset", quality=quality)
                            self._trace(run, "validation", "validate_dataset", f"Revalidation quality {quality:.2f}", {"passed": result.validation.passed, "failed_checks": failed[:50]})
                        if quality < req.quality_threshold:
                            raise RuntimeError(f"Quality gate not reached: {quality:.2f} < {req.quality_threshold:.2f}")

                elif step == "package_dataset":
                    if result is None:
                        raise RuntimeError("package_dataset requires generated data")
                    if run.quality_score is None:
                        run.quality_score = result.validation.quality_score
                    if run.quality_score < req.quality_threshold:
                        raise RuntimeError("Refusing to package dataset below quality gate")
                    project_id = run.project_id
                    if not project_id:
                        project = self.repo.create_project(ProjectCreateRequest(name=f"Agent: {req.goal[:80]}", description="Created automatically by an agent run"))
                        project_id = project["id"]; run.project_id = project_id
                    gen_req = SystemGenerateRequest(
                        database_type=req.database_type, database_name=req.database_name, schema_name=req.schema_name,
                        default_row_count=req.default_row_count, seed=req.seed, scenario=req.scenario or req.goal,
                        tables=tables, negative_testing=False, edge_case_rate=0.0,
                    )
                    dataset = self.repo.create_dataset(
                        project_id=project_id, seed=req.seed,
                        schema=[t.model_dump(mode="json") for t in tables],
                        rules=[r.model_dump(mode="json") for t in tables for r in t.business_rules],
                        row_counts={f"{t.schema_name}.{t.name}": len(t.rows) for t in result.tables},
                        validation=result.validation.model_dump(mode="json"), profile=profiles,
                        generator_version="0.5.0",
                    )
                    artifact = self.repo.save_dataset_artifact(dataset.id, system_to_zip(gen_req, result), "agent-dataset.zip")
                    run.dataset_id = dataset.id; run.artifact_path = artifact; run.approval_state = "pending"
                    self._trace(run, "result", step, "Persisted immutable dataset version and ZIP artifact", {"dataset_id": dataset.id})

            self._set(run, state="completed", step="completed", quality=result.validation.quality_score if result else run.quality_score)
            self._trace(run, "result", "completed", f"Agent run completed at quality {run.quality_score:.2f}" if run.quality_score is not None else "Agent run completed")
            self.repo.audit("agent.complete", run.id, {"quality": run.quality_score, "dataset_id": run.dataset_id})
            return run
        except Exception as exc:
            if run.state == "cancelled" or "cancelled" in str(exc).lower():
                self._set(run, state="cancelled", step="cancelled")
                return run
            self._set(run, state="failed", step="failed", error=str(exc))
            self._trace(run, "error", "failed", safe_error_text(str(exc)))
            self.repo.audit("agent.failed", run.id, {"error": safe_error_text(str(exc))})
            return run
        finally:
            if connector is not None:
                connector.close()
