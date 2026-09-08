from __future__ import annotations

import json
import os
import threading
from typing import Any

import httpx

from .models import AIProviderSessionRequest, ColumnSpec, SchemaInferenceRequest

_SESSION_LOCK = threading.RLock()
_SESSION_CONFIG: dict[str, str] = {}


def configure_provider_session(req: AIProviderSessionRequest) -> dict[str, Any]:
    """Configure an AI provider for this process only. The API key is never returned."""
    with _SESSION_LOCK:
        _SESSION_CONFIG.clear()
        if req.provider != "none":
            _SESSION_CONFIG.update(
                provider=req.provider,
                model=(req.model or "").strip(),
                base_url=(req.base_url or "").strip().rstrip("/"),
                api_key=(req.api_key or "").strip(),
            )
    return provider_status()


def clear_provider_session() -> dict[str, Any]:
    with _SESSION_LOCK:
        _SESSION_CONFIG.clear()
    return provider_status()


def _settings() -> dict[str, str]:
    with _SESSION_LOCK:
        if _SESSION_CONFIG:
            return dict(_SESSION_CONFIG)
    return {
        "provider": os.getenv("AI_PROVIDER", "none").strip().lower(),
        "model": os.getenv("AI_MODEL", "").strip(),
        "base_url": os.getenv("AI_BASE_URL", "").strip().rstrip("/"),
        "api_key": os.getenv("AI_API_KEY", "").strip(),
    }


def provider_status() -> dict[str, Any]:
    s = _settings()
    provider = s.get("provider") or "none"
    model = s.get("model") or ""
    with _SESSION_LOCK:
        source = "session" if _SESSION_CONFIG else "environment"
    return {
        "configured": provider != "none" and bool(model),
        "provider": provider,
        "model": model or None,
        "base_url": s.get("base_url") or None,
        "has_api_key": bool(s.get("api_key")),
        "source": source,
        "fallback": "smart-local",
    }


async def _json_completion(system_prompt: str, user_prompt: str, timeout: int = 60) -> dict[str, Any] | None:
    s = _settings()
    provider = s.get("provider", "none")
    base_url = s.get("base_url", "").rstrip("/")
    model = s.get("model", "")
    api_key = s.get("api_key", "")
    if provider == "none" or not model:
        return None
    try:
        if provider == "ollama":
            url = f"{base_url or 'http://localhost:11434'}/api/chat"
            payload = {
                "model": model,
                "stream": False,
                "format": "json",
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            }
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(url, json=payload)
                response.raise_for_status()
                return json.loads(response.json()["message"]["content"])
        if provider == "openai-compatible":
            if not base_url:
                return None
            headers = {"Content-Type": "application/json"}
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
            payload = {
                "model": model,
                "temperature": 0.1,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "response_format": {"type": "json_object"},
            }
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(f"{base_url}/chat/completions", headers=headers, json=payload)
                response.raise_for_status()
                return json.loads(response.json()["choices"][0]["message"]["content"])
    except Exception:
        return None
    return None


SYSTEM_PROMPT = """You design synthetic test-data schemas. Return JSON only as an object with a single key named columns containing an array of column objects.
Each object may contain: name, data_type, semantic_type, nullable, unique, primary_key, min_value,
max_value, choices, null_rate. Generate a realistic but safe schema from database/schema/table metadata.
Do not include real people or copied production data. Prefer 6-14 useful columns."""


async def infer_with_provider(req: SchemaInferenceRequest) -> list[ColumnSpec] | None:
    user_prompt = (
        f"Database type: {req.database_type}\nDatabase: {req.database_name}\n"
        f"Schema: {req.schema_name}\nTable: {req.table_name}\n"
        f"Scenario: {req.scenario or 'general application test data'}"
    )
    parsed = await _json_completion(SYSTEM_PROMPT, user_prompt, 45)
    if not isinstance(parsed, dict):
        return None
    items = parsed.get("columns")
    if not isinstance(items, list):
        return None
    try:
        columns = [ColumnSpec.model_validate(item) for item in items]
    except Exception:
        return None
    return columns[:30] or None


SYSTEM_MODEL_PROMPT = """You design relational synthetic test-data models. Return JSON only with key `tables`.
Each table must have: name, schema_name, row_count, columns, foreign_keys, primary_key_columns, unique_constraints, check_constraints, correlations, business_rules.
Each column may contain name, data_type, semantic_type, nullable, unique, primary_key, min_value, max_value, choices, null_rate.
Each foreign key uses column, references_table, references_column and may use columns/references_columns for composite keys.
Infer realistic entities, PK/FK relationships, cardinalities, constraints, enums, representative row counts, and date/business rules from the description.
Do not include real customer data. Return structurally valid JSON only."""


async def model_system_with_provider(req: "SystemModelingRequest") -> list["TableSpec"] | None:
    from .models import TableSpec

    user_prompt = (
        f"Database type: {req.database_type}\nDatabase: {req.database_name}\nSchema: {req.schema_name}\n"
        f"Default row count: {req.default_row_count}\nSystem description:\n{req.description}"
    )
    parsed = await _json_completion(SYSTEM_MODEL_PROMPT, user_prompt, 60)
    items = parsed.get("tables") if isinstance(parsed, dict) else None
    if not isinstance(items, list):
        return None
    try:
        tables = [TableSpec.model_validate(item) for item in items]
    except Exception:
        return None
    by_name = {t.name.lower(): t for t in tables}
    for table in tables:
        for fk in table.foreign_keys:
            parent = by_name.get(fk.references_table.lower())
            if parent and not all(any(c.name == rc for c in parent.columns) for rc in fk.references_columns):
                return None
    return tables[:250] or None


AGENT_PLANNER_PROMPT = """You are the planning model for SyntheticForge AI, a synthetic test-data agent.
Return JSON only: {"steps": ["tool_name", ...]}.
You may ONLY choose from the tool names provided by the caller. Never invent shell, network, deployment, credential-exfiltration, or target-write tools.
A safe plan must preserve source read-only behavior, classify sensitive data, generate data, validate it, and package it. Loading is never autonomous and requires a separate human approval action."""


async def plan_agent_with_provider(goal: str, allowed_tools: list[str], context: dict[str, Any] | None = None) -> list[str] | None:
    prompt = (
        f"Goal:\n{goal}\n\nAllowed tools:\n{json.dumps(allowed_tools)}\n\n"
        f"Context:\n{json.dumps(context or {}, default=str)[:12000]}"
    )
    parsed = await _json_completion(AGENT_PLANNER_PROMPT, prompt, 45)
    if not isinstance(parsed, dict) or not isinstance(parsed.get("steps"), list):
        return None
    return [str(x) for x in parsed["steps"] if isinstance(x, str)]


AGENT_REPAIR_PROMPT = """You are the repair advisor for SyntheticForge AI. Return JSON only with key `actions` containing short declarative repair actions.
Do not request source writes, target writes, shell commands, external browsing, secret access, or skipping validation. Focus on deterministic data repairs for failed synthetic-data validation checks."""


async def repair_advice_with_provider(goal: str, failed_checks: list[dict[str, Any]]) -> list[str] | None:
    parsed = await _json_completion(
        AGENT_REPAIR_PROMPT,
        f"Goal:\n{goal}\n\nFailed checks:\n{json.dumps(failed_checks, default=str)[:16000]}",
        45,
    )
    if not isinstance(parsed, dict) or not isinstance(parsed.get("actions"), list):
        return None
    return [str(x)[:500] for x in parsed["actions"] if isinstance(x, str)][:20]
