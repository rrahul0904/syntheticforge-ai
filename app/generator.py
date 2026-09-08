from __future__ import annotations

import csv
import io
import json
import random
import ast
import re
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from faker import Faker

from .models import ColumnSpec, GenerateRequest, SchemaInferenceRequest, TableSpec


TABLE_BLUEPRINTS: dict[str, list[dict[str, Any]]] = {
    "reservation": [
        {"name": "reservation_id", "data_type": "bigint", "semantic_type": "id", "primary_key": True, "nullable": False, "unique": True},
        {"name": "confirmation_code", "data_type": "varchar", "semantic_type": "confirmation_code", "nullable": False, "unique": True},
        {"name": "guest_name", "data_type": "varchar", "semantic_type": "full_name", "nullable": False},
        {"name": "guest_email", "data_type": "varchar", "semantic_type": "email", "nullable": False},
        {"name": "check_in_date", "data_type": "date", "semantic_type": "future_date", "nullable": False},
        {"name": "check_out_date", "data_type": "date", "semantic_type": "future_date", "nullable": False},
        {"name": "room_type", "data_type": "varchar", "semantic_type": "choice", "choices": ["Standard King", "Deluxe King", "Double Queen", "Suite"]},
        {"name": "nightly_rate", "data_type": "decimal(10,2)", "semantic_type": "money", "min_value": 89, "max_value": 649, "nullable": False},
        {"name": "status", "data_type": "varchar", "semantic_type": "choice", "choices": ["confirmed", "checked_in", "checked_out", "cancelled", "no_show"], "nullable": False},
        {"name": "created_at", "data_type": "timestamp", "semantic_type": "past_datetime", "nullable": False},
    ],
    "guest": [
        {"name": "guest_id", "data_type": "bigint", "semantic_type": "id", "primary_key": True, "nullable": False, "unique": True},
        {"name": "first_name", "data_type": "varchar", "semantic_type": "first_name", "nullable": False},
        {"name": "last_name", "data_type": "varchar", "semantic_type": "last_name", "nullable": False},
        {"name": "email", "data_type": "varchar", "semantic_type": "email", "nullable": False, "unique": True},
        {"name": "phone", "data_type": "varchar", "semantic_type": "phone"},
        {"name": "country", "data_type": "varchar", "semantic_type": "country"},
        {"name": "loyalty_tier", "data_type": "varchar", "semantic_type": "choice", "choices": ["member", "silver", "gold", "platinum"]},
        {"name": "created_at", "data_type": "timestamp", "semantic_type": "past_datetime", "nullable": False},
    ],
    "payment": [
        {"name": "payment_id", "data_type": "bigint", "semantic_type": "id", "primary_key": True, "nullable": False, "unique": True},
        {"name": "reservation_id", "data_type": "bigint", "semantic_type": "foreign_id", "nullable": False},
        {"name": "amount", "data_type": "decimal(10,2)", "semantic_type": "money", "min_value": 25, "max_value": 2500, "nullable": False},
        {"name": "currency", "data_type": "varchar", "semantic_type": "choice", "choices": ["USD", "EUR", "GBP", "CAD"], "nullable": False},
        {"name": "payment_method", "data_type": "varchar", "semantic_type": "choice", "choices": ["visa", "mastercard", "amex", "digital_wallet"], "nullable": False},
        {"name": "payment_status", "data_type": "varchar", "semantic_type": "choice", "choices": ["authorized", "captured", "refunded", "failed"], "nullable": False},
        {"name": "paid_at", "data_type": "timestamp", "semantic_type": "past_datetime"},
    ],
    "order": [
        {"name": "order_id", "data_type": "bigint", "semantic_type": "id", "primary_key": True, "nullable": False, "unique": True},
        {"name": "customer_id", "data_type": "bigint", "semantic_type": "foreign_id", "nullable": False},
        {"name": "order_status", "data_type": "varchar", "semantic_type": "choice", "choices": ["pending", "paid", "shipped", "delivered", "cancelled"], "nullable": False},
        {"name": "subtotal", "data_type": "decimal(10,2)", "semantic_type": "money", "min_value": 8, "max_value": 1400},
        {"name": "tax_amount", "data_type": "decimal(10,2)", "semantic_type": "money", "min_value": 0, "max_value": 180},
        {"name": "created_at", "data_type": "timestamp", "semantic_type": "past_datetime", "nullable": False},
    ],
    "user": [
        {"name": "user_id", "data_type": "bigint", "semantic_type": "id", "primary_key": True, "nullable": False, "unique": True},
        {"name": "username", "data_type": "varchar", "semantic_type": "username", "nullable": False, "unique": True},
        {"name": "full_name", "data_type": "varchar", "semantic_type": "full_name", "nullable": False},
        {"name": "email", "data_type": "varchar", "semantic_type": "email", "nullable": False, "unique": True},
        {"name": "is_active", "data_type": "boolean", "semantic_type": "boolean", "nullable": False},
        {"name": "created_at", "data_type": "timestamp", "semantic_type": "past_datetime", "nullable": False},
    ],
}


def normalize_token(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def infer_schema_local(req: SchemaInferenceRequest) -> list[ColumnSpec]:
    table = normalize_token(req.table_name)
    for token, blueprint in TABLE_BLUEPRINTS.items():
        if token in table:
            return [ColumnSpec.model_validate(x) for x in blueprint]

    base = [
        ColumnSpec(name=f"{table.rstrip('s') or 'record'}_id", data_type="bigint", semantic_type="id", primary_key=True, unique=True, nullable=False),
        ColumnSpec(name="name", data_type="varchar", semantic_type="company" if "company" in table else "text", nullable=False),
        ColumnSpec(name="status", data_type="varchar", semantic_type="choice", choices=["active", "pending", "inactive"], nullable=False),
        ColumnSpec(name="created_at", data_type="timestamp", semantic_type="past_datetime", nullable=False),
        ColumnSpec(name="updated_at", data_type="timestamp", semantic_type="past_datetime", nullable=True, null_rate=0.1),
    ]
    return base


def semantic_from_name(column: ColumnSpec) -> str:
    if column.semantic_type:
        return column.semantic_type.lower()
    n = normalize_token(column.name)
    if column.primary_key or n == "id" or n.endswith("_id"):
        return "id" if column.primary_key or n == "id" else "foreign_id"
    if any(token in n for token in ["nightly_rate", "daily_rate", "hourly_rate", "unit_price", "list_price", "sale_price"]):
        return "money"
    if any(token in n for token in ["tier", "category", "role", "method", "channel", "segment", "room_type", "event_type"]):
        return "choice"
    patterns = [
        ("email", "email"), ("first_name", "first_name"), ("last_name", "last_name"),
        ("full_name", "full_name"), ("name", "text"), ("phone", "phone"),
        ("address", "address"), ("city", "city"), ("state", "state"), ("country", "country"),
        ("zip", "postal_code"), ("postal", "postal_code"), ("company", "company"),
        ("username", "username"), ("url", "url"), ("ip", "ipv4"), ("currency", "currency"),
        ("price", "money"), ("amount", "money"), ("cost", "money"), ("rate", "decimal"),
        ("date", "date"), ("time", "past_datetime"), ("created", "past_datetime"), ("updated", "past_datetime"),
        ("active", "boolean"), ("enabled", "boolean"), ("status", "choice"),
    ]
    for needle, semantic in patterns:
        if needle in n:
            return semantic
    t = column.data_type.lower()
    if "bool" in t:
        return "boolean"
    if any(x in t for x in ["int", "number"]):
        return "integer"
    if any(x in t for x in ["decimal", "numeric", "float", "double"]):
        return "decimal"
    if "date" in t and "time" not in t:
        return "date"
    if any(x in t for x in ["timestamp", "datetime"]):
        return "past_datetime"
    return "text"


def _bounded_number(rng: random.Random, col: ColumnSpec, integer: bool) -> int | float:
    lo = col.min_value if col.min_value is not None else (1 if integer else 0.0)
    hi = col.max_value if col.max_value is not None else (10000 if integer else 10000.0)
    # When a source profile supplies a mean/stddev, preserve its center/spread instead of
    # flattening the source into a uniform distribution. Clamp to learned/source bounds.
    st = col.statistics
    if st and st.mean is not None and st.stddev is not None:
        raw = float(st.mean) if float(st.stddev) == 0 else rng.gauss(float(st.mean), float(st.stddev))
        raw = min(float(hi), max(float(lo), raw))
        return int(round(raw)) if integer else round(raw, 2)
    if integer:
        return rng.randint(int(lo), int(hi))
    return round(rng.uniform(float(lo), float(hi)), 2)


def _make_value(fake: Faker, rng: random.Random, col: ColumnSpec, row_index: int) -> Any:
    semantic = semantic_from_name(col)
    type_name = col.data_type.lower()

    def fit(value: Any) -> Any:
        if isinstance(value, str) and col.length is not None and len(value) > col.length:
            return value[: col.length]
        return value

    # Source-profile categorical frequencies take precedence only for non-sensitive fields.
    if col.statistics and col.statistics.top_values and col.sensitivity == "non-sensitive" and (col.statistics.distinct_count or 999999) <= 100:
        items = col.statistics.top_values
        weights = [max(0.0, float(x.get("frequency", 0))) for x in items]
        if sum(weights) > 0:
            raw = rng.choices(items, weights=weights, k=1)[0].get("value")
            try: value = ast.literal_eval(raw) if isinstance(raw, str) else raw
            except Exception: value = raw
            return fit(value)
    if col.choices: return fit(rng.choice(col.choices))
    if semantic == "id":
        return fake.uuid4() if "uuid" in type_name else row_index + 1
    if semantic == "foreign_id": return rng.randint(1, max(2, row_index + 10))
    if semantic == "email": return fit(fake.unique.safe_email() if col.unique else fake.safe_email())
    if semantic == "first_name": return fit(fake.first_name())
    if semantic == "last_name": return fit(fake.last_name())
    if semantic == "full_name": return fit(fake.name())
    if semantic == "username": return fit(fake.unique.user_name() if col.unique else fake.user_name())
    if semantic == "phone": return fit(fake.phone_number())
    if semantic == "address": return fit(fake.street_address())
    if semantic == "city": return fit(fake.city())
    if semantic == "state": return fit(fake.state())
    if semantic == "country": return fit(fake.country())
    if semantic == "postal_code": return fit(fake.postcode())
    if semantic == "company": return fit(fake.company())
    if semantic == "url": return fit(fake.url())
    if semantic == "ipv4": return fit(fake.ipv4_public())
    if semantic == "currency": return fit(rng.choice(["USD", "EUR", "GBP", "CAD", "AUD"]))
    if semantic == "money":
        if col.min_value is None: col.min_value = 10
        if col.max_value is None: col.max_value = 2500
        return _bounded_number(rng, col, False)
    if semantic == "integer": return _bounded_number(rng, col, True)
    if semantic in {"decimal", "float"}: return _bounded_number(rng, col, False)
    if semantic == "boolean": return rng.choice([True, False])
    if semantic == "date": return fake.date_between(start_date="-3y", end_date="today").isoformat()
    if semantic == "future_date": return fake.date_between(start_date="today", end_date="+18M").isoformat()
    if semantic == "past_datetime": return fake.date_time_between(start_date="-3y", end_date="now").isoformat(sep=" ", timespec="seconds")
    if semantic == "confirmation_code": return fit(f"{fake.lexify('???').upper()}-{rng.randint(100000,999999)}")
    if semantic == "choice":
        n = normalize_token(col.name)
        choice_sets = [
            (("loyalty_tier", "member_tier"), ["member", "silver", "gold", "platinum"]),
            (("room_type",), ["Standard King", "Deluxe King", "Double Queen", "Suite"]),
            (("payment_status",), ["authorized", "captured", "refunded", "failed"]),
            (("payment_method",), ["visa", "mastercard", "amex", "digital_wallet"]),
            (("order_status",), ["pending", "paid", "shipped", "delivered", "cancelled"]),
            (("reservation_status", "booking_status"), ["confirmed", "checked_in", "checked_out", "cancelled", "no_show"]),
            (("role",), ["admin", "member", "viewer", "operator"]),
            (("channel",), ["web", "mobile", "api", "partner"]),
            (("event_type",), ["created", "updated", "processed", "completed"]),
            (("category",), ["standard", "premium", "enterprise"]),
        ]
        for needles, values in choice_sets:
            if any(needle in n for needle in needles): return fit(rng.choice(values))
        return fit(rng.choice(["active", "pending", "inactive"]))
    if any(x in type_name for x in ["json", "variant", "super"]): return {"source": rng.choice(["web", "mobile", "api"]), "score": rng.randint(1, 100)}
    if "uuid" in type_name: return fake.uuid4()
    if any(x in type_name for x in ["char", "text", "string"]):
        if col.unique:
            prefix = normalize_token(col.name).replace("_", "")[:4].upper() or "VAL"
            width = max(1, (col.length or 24) - len(prefix) - 1)
            return fit(f"{prefix}-{row_index + 1:0{min(width,12)}d}")
        return fit(fake.sentence(nb_words=4).rstrip("."))
    return fit(fake.word())

def generate_rows(req: GenerateRequest, columns: list[ColumnSpec]) -> tuple[list[dict[str, Any]], list[str]]:
    from .privacy import classify_columns
    from .intelligence import apply_edge_cases, apply_sensitivity
    classified = classify_columns(columns)
    columns[:] = classified
    Faker.seed(req.seed)
    fake = Faker(req.locale)
    fake.seed_instance(req.seed)
    rng = random.Random(req.seed)
    warnings: list[str] = []
    rows: list[dict[str, Any]] = []

    for i in range(req.row_count):
        row: dict[str, Any] = {}
        for col in columns:
            if col.nullable and not col.primary_key and rng.random() < col.null_rate:
                row[col.name] = None
            else:
                row[col.name] = _make_value(fake, rng, col, i)
        rows.append(row)

    names = {c.name for c in columns}
    if {"check_in_date", "check_out_date"}.issubset(names):
        for row in rows:
            if row.get("check_in_date"):
                checkin = date.fromisoformat(str(row["check_in_date"])[:10])
                nights = rng.randint(1, 9)
                row["check_out_date"] = (checkin + timedelta(days=nights)).isoformat()

    apply_sensitivity(TableSpec(name=req.table_name, schema_name=req.schema_name, columns=columns, foreign_keys=req.foreign_keys), rows, seed=req.seed)
    if req.edge_case_rate > 0:
        apply_edge_cases(TableSpec(name=req.table_name, schema_name=req.schema_name, columns=columns, foreign_keys=req.foreign_keys), rows, req.edge_case_rate, req.negative_testing, req.seed)
    if req.foreign_keys:
        warnings.append("Foreign-key metadata is accepted; cross-table parent datasets are not attached in a single-table request. Use system generation to preserve parent keys exactly.")
    return rows, warnings

def quote_identifier(value: str, dialect: str) -> str:
    if dialect == "mysql": return f"`{value.replace('`', '``')}`"
    if dialect == "sqlserver": return f"[{value.replace(']', ']]')}]"
    return '"' + value.replace('"', '""') + '"'


def sql_literal(value: Any) -> str:
    if value is None: return "NULL"
    if isinstance(value, bool): return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float, Decimal)): return str(value)
    if isinstance(value, (dict, list)): value = json.dumps(value, separators=(",", ":"))
    return "'" + str(value).replace("'", "''") + "'"


def rows_to_sql(req: GenerateRequest, columns: list[ColumnSpec], rows: list[dict[str, Any]]) -> str:
    d = req.database_type
    qcols = ", ".join(quote_identifier(c.name, d) for c in columns)
    if d == "bigquery":
        target = f"`{req.database_name}.{req.schema_name}.{req.table_name}`"
    else:
        target = ".".join(quote_identifier(x, d) for x in [req.schema_name, req.table_name])
    lines = []
    for row in rows:
        values = ", ".join(sql_literal(row.get(c.name)) for c in columns)
        lines.append(f"INSERT INTO {target} ({qcols}) VALUES ({values});")
    return "\n".join(lines)


def rows_to_csv(columns: list[ColumnSpec], rows: list[dict[str, Any]]) -> str:
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=[c.name for c in columns])
    writer.writeheader()
    for row in rows:
        normalized = {k: json.dumps(v) if isinstance(v, (dict, list)) else v for k, v in row.items()}
        writer.writerow(normalized)
    return out.getvalue()
