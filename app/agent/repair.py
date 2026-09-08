from __future__ import annotations

import random
from datetime import date, datetime, timedelta
from typing import Any

from faker import Faker

from ..generator import _make_value
from ..models import GeneratedTable, SystemGenerateRequest, SystemGenerateResponse
from ..system_generator import _table_key
from ..validation import validate_system


def _parse_date(value: Any):
    if value is None:
        return None
    if isinstance(value, (date, datetime)):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        try:
            return date.fromisoformat(str(value)[:10])
        except Exception:
            return None


def _fresh_value(table: GeneratedTable, column_name: str, index: int, seed: int) -> Any:
    col = next(c for c in table.columns if c.name == column_name)
    fake = Faker("en_US")
    fake.seed_instance(seed + index + 901)
    rng = random.Random(seed + index + 1901)
    value = _make_value(fake, rng, col, index)
    if isinstance(value, str) and col.length:
        value = value[: col.length]
    return value


def repair_result(req: SystemGenerateRequest, result: SystemGenerateResponse, attempt: int = 1) -> SystemGenerateResponse:
    """Deterministically repair common integrity/business failures, then revalidate."""
    tables = [t.model_copy(deep=True) for t in result.tables]
    by_name = {_table_key(t.name): t for t in tables}
    specs = {_table_key(t.name): t for t in req.tables}
    seed = req.seed + attempt * 100_003

    for table in tables:
        spec = specs.get(_table_key(table.name))
        if not spec:
            continue
        rng = random.Random(seed + len(table.rows))
        # Required values, enums/ranges/lengths.
        for i, row in enumerate(table.rows):
            for col in table.columns:
                if row.get(col.name) is None and not col.nullable:
                    row[col.name] = _fresh_value(table, col.name, i, seed)
                if row.get(col.name) is not None and col.choices and row.get(col.name) not in col.choices:
                    row[col.name] = rng.choice(col.choices)
                if isinstance(row.get(col.name), str) and col.length and len(row[col.name]) > col.length:
                    row[col.name] = row[col.name][: col.length]
                try:
                    if row.get(col.name) is not None and col.min_value is not None and float(row[col.name]) < col.min_value:
                        row[col.name] = col.min_value
                    if row.get(col.name) is not None and col.max_value is not None and float(row[col.name]) > col.max_value:
                        row[col.name] = col.max_value
                except Exception:
                    pass
        # PK/composite PK uniqueness.
        pk = spec.primary_key_columns or [c.name for c in table.columns if c.primary_key]
        seen: set[str] = set()
        for i, row in enumerate(table.rows):
            key = tuple(row.get(c) for c in pk)
            marker = repr(key)
            if pk and (any(v is None for v in key) or marker in seen):
                for pos, name in enumerate(pk):
                    col = next(c for c in table.columns if c.name == name)
                    if any(x in col.data_type.lower() for x in ["int", "number", "numeric", "decimal"]):
                        row[name] = i + 1 + pos * max(1, len(table.rows))
                    else:
                        row[name] = f"SF-{seed}-{i}-{pos}"[: col.length or 256]
                marker = repr(tuple(row.get(c) for c in pk))
            seen.add(marker)
        # FK repair including composites.
        for fk in table.foreign_keys:
            parent = by_name.get(_table_key(fk.references_table))
            if not parent or not parent.rows:
                continue
            valid = [tuple(r.get(c) for c in fk.references_columns) for r in parent.rows]
            valid = [v for v in valid if not any(x is None for x in v)]
            if not valid:
                continue
            valid_set = set(map(repr, valid))
            for row in table.rows:
                current = tuple(row.get(c) for c in fk.columns)
                if repr(current) not in valid_set:
                    chosen = valid[rng.randrange(len(valid))]
                    for child, value in zip(fk.columns, chosen):
                        row[child] = value
        # Date ordering and cancellation-dependent fields.
        names = {c.name.lower(): c.name for c in table.columns}
        for start_name, end_name in [
            ("check_in_date", "check_out_date"),
            ("start_date", "end_date"),
            ("created_at", "updated_at"),
            ("paid_at", "refunded_at"),
            ("payment_date", "refund_date"),
        ]:
            if start_name in names and end_name in names:
                a, b = names[start_name], names[end_name]
                for row in table.rows:
                    start = _parse_date(row.get(a))
                    end = _parse_date(row.get(b))
                    if start is not None and (end is None or end < start):
                        row[b] = (start + timedelta(days=1)).isoformat() if hasattr(start, "isoformat") else row.get(b)
        if "status" in names and "cancellation_date" in names:
            status, cancelled_at = names["status"], names["cancellation_date"]
            for row in table.rows:
                if str(row.get(status, "")).lower() in {"cancelled", "canceled"} and row.get(cancelled_at) is None:
                    row[cancelled_at] = datetime(2026, 1, 1) + timedelta(days=rng.randrange(365))
                    row[cancelled_at] = row[cancelled_at].isoformat(sep=" ", timespec="seconds")

    validation = validate_system(req, tables)
    return SystemGenerateResponse(
        generation_order=list(result.generation_order),
        relationship_count=result.relationship_count,
        tables=tables,
        warnings=list(result.warnings),
        validation=validation,
    )
