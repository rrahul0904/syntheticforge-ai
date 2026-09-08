from __future__ import annotations

import hashlib
import io
import json
import random
import re
import zipfile
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from faker import Faker

from .generator import _make_value, rows_to_csv, sql_literal, quote_identifier
from .models import ColumnSpec, GeneratedTable, GenerateRequest, SystemGenerateRequest, SystemGenerateResponse, TableSpec
from .validation import validate_system
from .privacy import classify_columns
from .intelligence import apply_correlations, apply_edge_cases, apply_sensitivity
from .profiling import profile_rows


def _table_key(name: str) -> str:
    return name.split(".")[-1].strip('"`[]').lower()


def _stable_offset(value: str) -> int:
    return int(hashlib.sha256(value.encode("utf-8")).hexdigest()[:8], 16)


def _generation_order(tables: list[TableSpec]) -> tuple[list[TableSpec], list[str]]:
    by_name = {_table_key(t.name): t for t in tables}
    deps: dict[str, set[str]] = {k: set() for k in by_name}
    warnings: list[str] = []
    for key, table in by_name.items():
        for fk in table.foreign_keys:
            parent = _table_key(fk.references_table)
            if parent in by_name and parent != key:
                deps[key].add(parent)
            elif parent not in by_name:
                warnings.append(f"{table.name}.{fk.column} references {fk.references_table}, which is not included; values will be generated locally.")

    ordered: list[TableSpec] = []
    remaining = set(by_name)
    while remaining:
        ready = sorted(k for k in remaining if not (deps[k] & remaining))
        if not ready:
            cycle = sorted(remaining)
            warnings.append("Relationship cycle detected among: " + ", ".join(cycle) + ". Generation continues with deterministic cycle fallback.")
            ready = [cycle[0]]
        for key in ready:
            ordered.append(by_name[key])
            remaining.remove(key)
    return ordered, warnings


def _pk_column(table: TableSpec) -> ColumnSpec | None:
    return next((c for c in table.columns if c.primary_key), None)


def _scenario_percentage(scenario: str | None, terms: list[str]) -> float | None:
    if not scenario:
        return None
    text = scenario.lower()
    for term in terms:
        patterns = [
            rf"(\d+(?:\.\d+)?)\s*%\s+(?:of\s+)?[^.\n]{{0,40}}{re.escape(term)}",
            rf"{re.escape(term)}[^.\n]{{0,40}}?(\d+(?:\.\d+)?)\s*%",
        ]
        for p in patterns:
            m = re.search(p, text)
            if m:
                return min(100.0, max(0.0, float(m.group(1)))) / 100.0
    return None


def _apply_scenario(rows: list[dict[str, Any]], table: TableSpec, scenario: str | None, rng: random.Random) -> None:
    if not rows or not scenario:
        return
    status_col = next((c for c in table.columns if "status" in c.name.lower()), None)
    if status_col:
        cancelled = _scenario_percentage(scenario, ["cancelled", "canceled", "cancellation", "cancellations"])
        if cancelled is not None:
            if status_col.choices and not any(str(v).lower() in {"cancelled", "canceled"} for v in status_col.choices):
                cancelled = None
            if not status_col.choices and not any(token in table.name.lower() for token in ["reservation", "booking", "order", "subscription"]):
                cancelled = None
            if cancelled is None:
                return
            count = round(len(rows) * cancelled)
            indexes = list(range(len(rows)))
            rng.shuffle(indexes)
            cancel_value = "cancelled"
            non_cancel_values: list[Any] = ["confirmed"]
            if status_col.choices:
                cancel_value = next(str(v) for v in status_col.choices if str(v).lower() in {"cancelled", "canceled"})
                non_cancel_values = [v for v in status_col.choices if str(v).lower() not in {"cancelled", "canceled"}] or ["active"]
            # Normalize first so the requested percentage is exact, not additive.
            for row in rows:
                if str(row.get(status_col.name, "")).lower() in {"cancelled", "canceled"}:
                    row[status_col.name] = rng.choice(non_cancel_values)
            for i in indexes[:count]:
                rows[i][status_col.name] = cancel_value

    null_pct = _scenario_percentage(scenario, ["nulls", "null values", "missing values"])
    if null_pct is not None:
        nullable = [c for c in table.columns if c.nullable and not c.primary_key]
        for col in nullable:
            count = round(len(rows) * null_pct)
            indexes = list(range(len(rows)))
            rng.shuffle(indexes)
            for i in indexes[:count]:
                rows[i][col.name] = None


def _cohere_dates(rows: list[dict[str, Any]], rng: random.Random) -> None:
    if not rows:
        return
    names = set(rows[0])
    if {"check_in_date", "check_out_date"}.issubset(names):
        for row in rows:
            if row.get("check_in_date"):
                try:
                    checkin = date.fromisoformat(str(row["check_in_date"])[:10])
                    row["check_out_date"] = (checkin + timedelta(days=rng.randint(1, 10))).isoformat()
                except ValueError:
                    pass
    if {"start_date", "end_date"}.issubset(names):
        for row in rows:
            if row.get("start_date"):
                try:
                    start = date.fromisoformat(str(row["start_date"])[:10])
                    row["end_date"] = (start + timedelta(days=rng.randint(1, 30))).isoformat()
                except ValueError:
                    pass



def _as_number(value: Any) -> float | None:
    try: return float(value) if value is not None else None
    except (TypeError, ValueError): return None

def _as_datetime(value: Any):
    from datetime import datetime
    if value is None: return None
    try: return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError: return None

def _apply_cross_table_semantics(table: TableSpec, rows: list[dict[str, Any]], generated: dict[str, GeneratedTable], rng: random.Random) -> None:
    """Repair common parent/child business semantics after FK assignment."""
    for fk in table.foreign_keys:
        if len(fk.columns) != 1 or len(fk.references_columns) != 1:
            continue
        parent = generated.get(_table_key(fk.references_table))
        if not parent: continue
        child_fk, parent_pk = fk.column, fk.references_column
        parent_by_id = {r.get(parent_pk): r for r in parent.rows}
        for row in rows:
            parent_row = parent_by_id.get(row.get(child_fk))
            if not parent_row: continue
            # Payment/refund-like amount cannot exceed its parent amount/total.
            if "amount" in row:
                parent_amount = next((_as_number(parent_row.get(k)) for k in ["total_amount", "amount", "subtotal"] if _as_number(parent_row.get(k)) is not None), None)
                if parent_amount is not None:
                    current = _as_number(row.get("amount"))
                    if current is None or current > parent_amount:
                        row["amount"] = round(max(0.0, parent_amount * rng.uniform(0.2, 1.0)), 2)
            # Refund-like event cannot precede its payment.
            for start_col, end_col in [("paid_at", "refunded_at"), ("created_at", "cancellation_date")]:
                if start_col in parent_row and end_col in row and parent_row.get(start_col):
                    start = _as_datetime(parent_row.get(start_col))
                    if start:
                        end = _as_datetime(row.get(end_col))
                        if end is None or end < start:
                            row[end_col] = (start + timedelta(days=rng.randint(0, 14))).isoformat(sep=" ", timespec="seconds")
    # Row-internal financial identities.
    names = set(table.columns[i].name for i in range(len(table.columns)))
    if {"subtotal", "tax_amount", "total_amount"}.issubset(names):
        for row in rows:
            subtotal = _as_number(row.get("subtotal")) or 0.0
            tax = _as_number(row.get("tax_amount")) or round(subtotal * rng.uniform(0.04, 0.12), 2)
            row["tax_amount"] = round(max(0.0, tax), 2)
            row["total_amount"] = round(subtotal + row["tax_amount"], 2)
    if {"quantity", "unit_price", "line_total"}.issubset(names):
        for row in rows:
            qty = _as_number(row.get("quantity")) or 1
            price = _as_number(row.get("unit_price")) or 0
            row["line_total"] = round(qty * price, 2)

def generate_system(req: SystemGenerateRequest) -> SystemGenerateResponse:
    ordered, warnings = _generation_order(req.tables)
    generated: dict[str, GeneratedTable] = {}
    relationship_count = sum(len(t.foreign_keys) for t in req.tables)

    for original in ordered:
        table = original.model_copy(deep=True)
        table.columns = classify_columns(table.columns)
        table_seed = (req.seed + _stable_offset(f"{table.schema_name}.{table.name}")) % 2_147_483_647
        fake = Faker(req.locale)
        fake.seed_instance(table_seed)
        rng = random.Random(table_seed)
        row_count = table.row_count or req.default_row_count
        rows: list[dict[str, Any]] = []

        for i in range(row_count):
            row: dict[str, Any] = {}
            for col in table.columns:
                if col.nullable and not col.primary_key and rng.random() < col.null_rate:
                    row[col.name] = None
                else:
                    row[col.name] = _make_value(fake, rng, col, i)
            rows.append(row)

        # Replace generated FK values with actual parent keys, including composite relationships.
        for fk in table.foreign_keys:
            parent = generated.get(_table_key(fk.references_table))
            if not parent:
                continue
            parent_values = [tuple(r.get(c) for c in fk.references_columns) for r in parent.rows]
            parent_values = [v for v in parent_values if not any(x is None for x in v)]
            if not parent_values:
                warnings.append(f"No values available for FK {table.name}.{'+'.join(fk.columns)} -> {fk.references_table}.{'+'.join(fk.references_columns)}")
                continue
            for row in rows:
                chosen = rng.choice(parent_values)
                for child_col, value in zip(fk.columns, chosen):
                    row[child_col] = value

        _cohere_dates(rows, rng)
        _apply_cross_table_semantics(table, rows, generated, rng)
        apply_correlations(table, rows, table_seed)
        _apply_scenario(rows, table, req.scenario, rng)
        apply_sensitivity(table, rows, seed=table_seed)
        apply_edge_cases(table, rows, req.edge_case_rate, req.negative_testing, table_seed)
        if not req.negative_testing:
            _cohere_dates(rows, rng)

        generated[_table_key(table.name)] = GeneratedTable(
            name=table.name,
            schema_name=table.schema_name,
            columns=table.columns,
            foreign_keys=table.foreign_keys,
            rows=rows,
        )

    result_tables = [generated[_table_key(t.name)] for t in ordered]
    validation = validate_system(req, result_tables)
    return SystemGenerateResponse(
        generation_order=[f"{t.schema_name}.{t.name}" for t in ordered],
        relationship_count=relationship_count,
        tables=result_tables,
        warnings=warnings,
        validation=validation,
    )

def _table_sql(database_type: str, database_name: str, table: GeneratedTable) -> str:
    qcols = ", ".join(quote_identifier(c.name, database_type) for c in table.columns)
    if database_type == "bigquery":
        target = f"`{database_name}.{table.schema_name}.{table.name}`"
    else:
        target = ".".join(quote_identifier(x, database_type) for x in [table.schema_name, table.name])
    lines = []
    for row in table.rows:
        values = ", ".join(sql_literal(row.get(c.name)) for c in table.columns)
        lines.append(f"INSERT INTO {target} ({qcols}) VALUES ({values});")
    return "\n".join(lines)


def system_to_zip(req: SystemGenerateRequest, result: SystemGenerateResponse) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        relationships = [
            {"table": t.name, **fk.model_dump()}
            for t in result.tables for fk in t.foreign_keys
        ]
        profiles = {
            f"{t.schema_name}.{t.name}": profile_rows(t.rows, t.columns, max_sample_rows=min(10_000, len(t.rows))).model_dump(mode="json")
            for t in result.tables
        }
        manifest = {
            "database_type": req.database_type,
            "database_name": req.database_name,
            "scenario": req.scenario,
            "seed": req.seed,
            "negative_testing": req.negative_testing,
            "edge_case_rate": req.edge_case_rate,
            "generation_order": result.generation_order,
            "relationship_count": result.relationship_count,
            "warnings": result.warnings,
            "validation": result.validation.model_dump(mode="json"),
            "tables": [
                {
                    "schema": t.schema_name,
                    "table": t.name,
                    "row_count": len(t.rows),
                    "columns": [c.model_dump(mode="json") for c in t.columns],
                    "foreign_keys": [fk.model_dump(mode="json") for fk in t.foreign_keys],
                }
                for t in result.tables
            ],
        }
        zf.writestr("manifest.json", json.dumps(manifest, indent=2, default=str))
        zf.writestr("validation.json", result.validation.model_dump_json(indent=2))
        zf.writestr("relationships.json", json.dumps(relationships, indent=2, default=str))
        zf.writestr("profile.json", json.dumps(profiles, indent=2, default=str))
        sql_parts: list[str] = []
        for table in result.tables:
            stem = f"tables/{table.schema_name}.{table.name}"
            zf.writestr(stem + ".csv", rows_to_csv(table.columns, table.rows))
            zf.writestr(stem + ".jsonl", "\n".join(json.dumps(r, default=str, separators=(",", ":")) for r in table.rows) + "\n")
            sql_parts.append(f"-- {table.schema_name}.{table.name}\n" + _table_sql(req.database_type, req.database_name, table))
        zf.writestr("all_inserts.sql", "\n\n".join(sql_parts))
    return buf.getvalue()

