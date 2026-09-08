from __future__ import annotations

import csv
import io
import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from .generator import semantic_from_name
from .models import (ColumnSpec, ForeignKeySpec, ParseSchemaRequest, TableSpec, UniqueConstraintSpec, CheckConstraintSpec, IndexSpec)

_IDENTIFIER = r'(?:`[^`]+`|"[^"]+"|\[[^\]]+\]|[A-Za-z_][\w$#]*)'
_QUALIFIED = rf'(?:(?:{_IDENTIFIER})\s*\.\s*){{0,2}}{_IDENTIFIER}'


def _clean_ident(value: str) -> str:
    value = value.strip().rstrip(",")
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("`") and value.endswith("`")):
        return value[1:-1]
    if value.startswith("[") and value.endswith("]"):
        return value[1:-1]
    return value


def _split_top_level(value: str) -> list[str]:
    parts: list[str] = []
    start = 0
    depth = 0
    quote: str | None = None
    for i, ch in enumerate(value):
        if quote:
            if ch == quote and (i == 0 or value[i - 1] != "\\"):
                quote = None
            continue
        if ch in {'"', "'", '`'}:
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        elif ch == "," and depth == 0:
            parts.append(value[start:i].strip())
            start = i + 1
    tail = value[start:].strip()
    if tail:
        parts.append(tail)
    return parts


def _extract_table_blocks(ddl: str) -> list[tuple[str, str | None, str]]:
    pattern = re.compile(
        rf"CREATE\s+(?:OR\s+REPLACE\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?({_QUALIFIED})\s*\(",
        re.I,
    )
    blocks: list[tuple[str, str | None, str]] = []
    for match in pattern.finditer(ddl):
        raw_name = match.group(1).strip()
        dot_parts = [p.strip() for p in re.split(r"\s*\.\s*", raw_name)]
        schema = _clean_ident(dot_parts[-2]) if len(dot_parts) > 1 else None
        table = _clean_ident(dot_parts[-1])
        depth = 1
        i = match.end()
        quote: str | None = None
        while i < len(ddl) and depth:
            ch = ddl[i]
            if quote:
                if ch == quote and ddl[i - 1] != "\\":
                    quote = None
            elif ch in {'"', "'", '`'}:
                quote = ch
            elif ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            i += 1
        if depth == 0:
            blocks.append((table, schema, ddl[match.end():i - 1]))
    return blocks


def _type_to_semantic(name: str, data_type: str, primary_key: bool = False) -> str:
    probe = ColumnSpec(name=name, data_type=data_type, primary_key=primary_key)
    return semantic_from_name(probe)


def _parse_type_shape(data_type: str) -> tuple[int | None, int | None, int | None]:
    m = re.search(r"\((\d+)(?:\s*,\s*(\d+))?\)", data_type)
    if not m:
        return None, None, None
    first = int(m.group(1))
    second = int(m.group(2)) if m.group(2) is not None else None
    low = data_type.lower()
    if any(x in low for x in ["char", "varchar", "string", "binary", "varbinary"]):
        return first, None, None
    if any(x in low for x in ["decimal", "numeric", "number"]):
        return None, first, second or 0
    return None, None, None


def _parse_default(constraints: str) -> Any | None:
    m = re.search(r"\bDEFAULT\s+(.+?)(?=\s+(?:NOT\s+NULL|NULL|UNIQUE|PRIMARY\s+KEY|REFERENCES|CHECK|CONSTRAINT|GENERATED|IDENTITY)\b|$)", constraints, re.I)
    return m.group(1).strip() if m else None


def _parse_constraint_name(cleaned: str) -> str | None:
    m = re.match(rf"CONSTRAINT\s+({_IDENTIFIER})\s+", cleaned, re.I)
    return _clean_ident(m.group(1)) if m else None


def parse_ddl(req: ParseSchemaRequest) -> tuple[list[TableSpec], list[str]]:
    warnings: list[str] = []
    tables: list[TableSpec] = []
    blocks = _extract_table_blocks(req.content)
    if not blocks:
        raise ValueError("No CREATE TABLE statements were found")

    for table_name, parsed_schema, body in blocks:
        schema_name = parsed_schema or req.schema_name
        columns: list[ColumnSpec] = []
        fks: list[ForeignKeySpec] = []
        table_pk: list[str] = []
        uniques: list[UniqueConstraintSpec] = []
        checks: list[CheckConstraintSpec] = []
        column_items: list[tuple[str, str, str]] = []

        for item in _split_top_level(body):
            cleaned = re.sub(r"\s+", " ", item.strip())
            upper = cleaned.upper()
            if not cleaned:
                continue
            if upper.startswith(("CONSTRAINT ", "PRIMARY KEY", "FOREIGN KEY", "UNIQUE ", "CHECK ")):
                cname = _parse_constraint_name(cleaned)
                body_constraint = re.sub(rf"^CONSTRAINT\s+{_IDENTIFIER}\s+", "", cleaned, flags=re.I)
                pk = re.search(r"PRIMARY\s+KEY\s*\(([^)]+)\)", body_constraint, re.I)
                if pk:
                    table_pk.extend(_clean_ident(x.strip()) for x in _split_top_level(pk.group(1)))
                uq = re.search(r"\bUNIQUE\s*\(([^)]+)\)", body_constraint, re.I)
                if uq:
                    uniques.append(UniqueConstraintSpec(name=cname, columns=[_clean_ident(x.strip()) for x in _split_top_level(uq.group(1))]))
                fk = re.search(
                    rf"FOREIGN\s+KEY\s*\(([^)]+)\)\s*REFERENCES\s+({_QUALIFIED})\s*\(([^)]+)\)(.*)$",
                    body_constraint, re.I,
                )
                if fk:
                    ref_parts = [p.strip() for p in re.split(r"\s*\.\s*", fk.group(2))]
                    cols = [_clean_ident(x.strip()) for x in _split_top_level(fk.group(1))]
                    ref_cols = [_clean_ident(x.strip()) for x in _split_top_level(fk.group(3))]
                    tail = fk.group(4) or ""
                    on_delete = re.search(r"ON\s+DELETE\s+(CASCADE|SET\s+NULL|SET\s+DEFAULT|RESTRICT|NO\s+ACTION)", tail, re.I)
                    on_update = re.search(r"ON\s+UPDATE\s+(CASCADE|SET\s+NULL|SET\s+DEFAULT|RESTRICT|NO\s+ACTION)", tail, re.I)
                    fks.append(ForeignKeySpec(
                        name=cname,
                        column=cols[0], columns=cols,
                        references_table=_clean_ident(ref_parts[-1]),
                        references_column=ref_cols[0], references_columns=ref_cols,
                        on_delete=on_delete.group(1).upper() if on_delete else None,
                        on_update=on_update.group(1).upper() if on_update else None,
                    ))
                chk = re.search(r"\bCHECK\s*\((.*)\)\s*$", body_constraint, re.I)
                if chk:
                    checks.append(CheckConstraintSpec(name=cname, expression=chk.group(1).strip()))
                continue

            m = re.match(rf"^({_IDENTIFIER})\s+(.+)$", cleaned, re.I)
            if not m:
                warnings.append(f"Skipped unrecognized definition in {table_name}: {cleaned[:100]}")
                continue
            name = _clean_ident(m.group(1))
            remainder = m.group(2)
            constraint_match = re.search(
                r"\s+(PRIMARY\s+KEY|NOT\s+NULL|NULL|UNIQUE|REFERENCES|DEFAULT|CHECK|CONSTRAINT|GENERATED|IDENTITY|AUTO_INCREMENT|AUTOINCREMENT)\b",
                remainder, re.I,
            )
            data_type = remainder[:constraint_match.start()].strip() if constraint_match else remainder.strip()
            constraints = remainder[constraint_match.start():].strip() if constraint_match else ""
            column_items.append((name, data_type, constraints))

            fk = re.search(
                rf"REFERENCES\s+({_QUALIFIED})\s*\(\s*({_IDENTIFIER})\s*\)(.*)$",
                constraints, re.I,
            )
            if fk:
                ref_parts = [p.strip() for p in re.split(r"\s*\.\s*", fk.group(1))]
                tail = fk.group(3) or ""
                od = re.search(r"ON\s+DELETE\s+(CASCADE|SET\s+NULL|SET\s+DEFAULT|RESTRICT|NO\s+ACTION)", tail, re.I)
                ou = re.search(r"ON\s+UPDATE\s+(CASCADE|SET\s+NULL|SET\s+DEFAULT|RESTRICT|NO\s+ACTION)", tail, re.I)
                fks.append(ForeignKeySpec(
                    column=name,
                    references_table=_clean_ident(ref_parts[-1]),
                    references_column=_clean_ident(fk.group(2)),
                    on_delete=od.group(1).upper() if od else None,
                    on_update=ou.group(1).upper() if ou else None,
                ))

        for name, data_type, constraints in column_items:
            primary = bool(re.search(r"PRIMARY\s+KEY", constraints, re.I)) or name in table_pk
            nullable = not bool(re.search(r"NOT\s+NULL", constraints, re.I)) and not primary
            unique = bool(re.search(r"\bUNIQUE\b", constraints, re.I)) or (primary and len(table_pk or [name]) == 1)
            generated = bool(re.search(r"\bGENERATED\b", constraints, re.I))
            identity = bool(re.search(r"\bIDENTITY\b|AUTO_INCREMENT|AUTOINCREMENT|\bSERIAL\b", f"{data_type} {constraints}", re.I))
            semantic = _type_to_semantic(name, data_type, primary)
            length, precision, scale = _parse_type_shape(data_type)
            enum_values = None
            enum_match = re.match(r"ENUM\s*\((.*)\)", data_type, re.I)
            if enum_match:
                enum_values = [x.strip().strip("'\"") for x in _split_top_level(enum_match.group(1))]
            inline_check = re.search(r"CHECK\s*\((.*)\)", constraints, re.I)
            if inline_check:
                checks.append(CheckConstraintSpec(name=f"check_{table_name}_{name}", expression=inline_check.group(1).strip()))
            col = ColumnSpec(
                name=name,
                data_type=data_type,
                semantic_type=semantic,
                nullable=nullable,
                unique=unique,
                primary_key=primary,
                generated=generated,
                identity=identity,
                default=_parse_default(constraints),
                length=length,
                precision=precision,
                scale=scale,
                enum_values=enum_values,
                choices=enum_values,
                null_rate=0.05 if nullable else 0.0,
            )
            columns.append(col)
            if unique and not primary:
                uniques.append(UniqueConstraintSpec(name=f"uq_{table_name}_{name}", columns=[name]))

        if not columns:
            warnings.append(f"Table {table_name} contained no parseable columns")
            continue
        tables.append(TableSpec(
            name=table_name,
            schema_name=schema_name,
            row_count=req.default_row_count,
            columns=columns,
            foreign_keys=fks,
            primary_key_columns=list(dict.fromkeys(table_pk or [c.name for c in columns if c.primary_key])),
            unique_constraints=uniques,
            check_constraints=checks,
        ))

    # ALTER TABLE ... ADD [CONSTRAINT ...] support for PK/FK/UNIQUE/CHECK.
    by_name = {t.name.lower(): t for t in tables}
    alter_re = re.compile(rf"ALTER\s+TABLE\s+({_QUALIFIED})\s+ADD\s+(.*?);", re.I | re.S)
    for m in alter_re.finditer(req.content):
        target = _clean_ident(re.split(r"\s*\.\s*", m.group(1).strip())[-1]).lower()
        table = by_name.get(target)
        if not table:
            continue
        clause = re.sub(r"\s+", " ", m.group(2).strip())
        cname = _parse_constraint_name(clause)
        body_clause = re.sub(rf"^CONSTRAINT\s+{_IDENTIFIER}\s+", "", clause, flags=re.I)
        pk = re.search(r"PRIMARY\s+KEY\s*\(([^)]+)\)", body_clause, re.I)
        if pk:
            cols = [_clean_ident(x.strip()) for x in _split_top_level(pk.group(1))]
            table.primary_key_columns = cols
            for col in table.columns:
                if col.name in cols:
                    col.primary_key = True; col.nullable = False; col.unique = len(cols) == 1
        uq = re.search(r"UNIQUE\s*\(([^)]+)\)", body_clause, re.I)
        if uq:
            table.unique_constraints.append(UniqueConstraintSpec(name=cname, columns=[_clean_ident(x.strip()) for x in _split_top_level(uq.group(1))]))
        fk = re.search(rf"FOREIGN\s+KEY\s*\(([^)]+)\)\s*REFERENCES\s+({_QUALIFIED})\s*\(([^)]+)\)", body_clause, re.I)
        if fk:
            cols = [_clean_ident(x.strip()) for x in _split_top_level(fk.group(1))]
            refs = [_clean_ident(x.strip()) for x in _split_top_level(fk.group(3))]
            ref_table = _clean_ident(re.split(r"\s*\.\s*", fk.group(2))[-1])
            table.foreign_keys.append(ForeignKeySpec(name=cname, column=cols[0], columns=cols, references_table=ref_table, references_column=refs[0], references_columns=refs))
        chk = re.search(r"CHECK\s*\((.*)\)", body_clause, re.I)
        if chk:
            table.check_constraints.append(CheckConstraintSpec(name=cname, expression=chk.group(1).strip()))

    return tables, warnings

def _json_type_to_sql(spec: dict[str, Any]) -> str:
    typ = spec.get("type", "string")
    fmt = spec.get("format", "")
    if typ == "integer": return "bigint"
    if typ == "number": return "decimal(18,2)"
    if typ == "boolean": return "boolean"
    if typ == "array" or typ == "object": return "json"
    if fmt == "date": return "date"
    if fmt in {"date-time", "datetime"}: return "timestamp"
    if fmt == "uuid": return "uuid"
    return "varchar"


def _resolve_local_ref(root: dict[str, Any], ref: str) -> dict[str, Any]:
    if not ref.startswith("#/"):
        return {}
    node: Any = root
    for part in ref[2:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        if not isinstance(node, dict) or part not in node:
            return {}
        node = node[part]
    return node if isinstance(node, dict) else {}


def _merge_schema_variants(spec: dict[str, Any], root: dict[str, Any]) -> dict[str, Any]:
    if "$ref" in spec:
        base = dict(_resolve_local_ref(root, str(spec["$ref"])))
        base.update({k: v for k, v in spec.items() if k != "$ref"})
        spec = base
    if "allOf" in spec and isinstance(spec["allOf"], list):
        merged: dict[str, Any] = {k: v for k, v in spec.items() if k != "allOf"}
        props: dict[str, Any] = {}; required: list[str] = []
        for part in spec["allOf"]:
            if not isinstance(part, dict): continue
            part = _merge_schema_variants(part, root)
            props.update(part.get("properties", {})); required.extend(part.get("required", []))
            for k, v in part.items():
                if k not in {"properties", "required"}: merged.setdefault(k, v)
        merged["properties"] = {**props, **merged.get("properties", {})}
        merged["required"] = list(dict.fromkeys([*required, *merged.get("required", [])]))
        spec = merged
    for key in ("oneOf", "anyOf"):
        if key in spec and isinstance(spec[key], list) and spec[key]:
            variants = [_merge_schema_variants(x, root) for x in spec[key] if isinstance(x, dict)]
            if variants:
                # Prefer an object variant, otherwise preserve enum/type information from first useful branch.
                chosen = next((x for x in variants if x.get("type") == "object" or "properties" in x), variants[0])
                merged = dict(chosen); merged.update({k: v for k, v in spec.items() if k != key}); spec = merged
    return spec


def _json_type_to_sql_resolved(spec: dict[str, Any]) -> tuple[str, bool]:
    typ = spec.get("type", "string")
    nullable = bool(spec.get("nullable", False))
    if isinstance(typ, list):
        nullable = nullable or "null" in typ
        typ = next((x for x in typ if x != "null"), "string")
    clone = dict(spec); clone["type"] = typ
    return _json_type_to_sql(clone), nullable


def _table_from_json_schema(name: str, schema_name: str, payload: dict[str, Any], row_count: int, root: dict[str, Any] | None = None) -> TableSpec:
    root = root or payload
    payload = _merge_schema_variants(payload, root)
    required = set(payload.get("required", []))
    columns: list[ColumnSpec] = []
    for col_name, raw_spec in payload.get("properties", {}).items():
        spec = _merge_schema_variants(raw_spec if isinstance(raw_spec, dict) else {}, root)
        data_type, nullable_by_type = _json_type_to_sql_resolved(spec)
        is_pk = bool(spec.get("primary_key", False)) or col_name in {"id", f"{name.rstrip('s')}_id"}
        choices = spec.get("enum") or spec.get("choices")
        max_len = spec.get("maxLength")
        columns.append(ColumnSpec(
            name=col_name,
            data_type=data_type,
            semantic_type=spec.get("semantic_type") or _type_to_semantic(col_name, data_type, is_pk),
            nullable=(col_name not in required or nullable_by_type) and not is_pk,
            unique=bool(spec.get("unique", False) or is_pk),
            primary_key=is_pk,
            default=spec.get("default", (spec.get("examples") or [None])[0] if isinstance(spec.get("examples"), list) else None),
            min_value=spec.get("minimum", spec.get("min_value")),
            max_value=spec.get("maximum", spec.get("max_value")),
            length=max_len,
            choices=choices,
            enum_values=choices,
            description=spec.get("description"),
            null_rate=float(spec.get("null_rate", 0.05 if col_name not in required and not is_pk else 0)),
        ))
    return TableSpec(name=name, schema_name=schema_name, row_count=row_count, columns=columns)


def _json_schema_tables(root: dict[str, Any], root_name: str, schema_name: str, row_count: int) -> list[TableSpec]:
    tables: dict[str, TableSpec] = {}

    def snake(value: str) -> str:
        return re.sub(r"(?<!^)(?=[A-Z])", "_", value).replace("-", "_").lower()

    def build(name: str, schema: dict[str, Any], parent: str | None = None, parent_fk: str | None = None) -> TableSpec:
        name = snake(name)
        schema = _merge_schema_variants(schema, root)
        if name in tables:
            return tables[name]
        # First create scalar columns. Object/record arrays become related tables rather than opaque JSON where possible.
        scalar_payload = dict(schema); scalar_props: dict[str, Any] = {}; fks: list[ForeignKeySpec] = []
        deferred: list[tuple[str, dict[str, Any], bool]] = []
        for prop_name, raw in schema.get("properties", {}).items():
            spec = _merge_schema_variants(raw if isinstance(raw, dict) else {}, root)
            typ = spec.get("type")
            is_object = typ == "object" or "properties" in spec
            is_obj_array = typ == "array" and isinstance(spec.get("items"), dict) and (spec["items"].get("type") == "object" or "properties" in _merge_schema_variants(spec["items"], root) or "$ref" in spec["items"])
            if is_object or is_obj_array:
                deferred.append((prop_name, spec.get("items") if is_obj_array else spec, is_obj_array))
                if not is_obj_array:
                    child_name = snake(prop_name)
                    scalar_props[f"{prop_name}_id" if not prop_name.endswith("_id") else prop_name] = {"type": "integer"}
                continue
            scalar_props[prop_name] = spec
        scalar_payload["properties"] = scalar_props
        scalar_payload["required"] = [x for x in schema.get("required", []) if x in scalar_props]
        table = _table_from_json_schema(name, schema_name, scalar_payload, row_count, root)
        if parent:
            fk_name = parent_fk or f"{parent.rstrip('s')}_id"
            if not any(c.name == fk_name for c in table.columns):
                table.columns.insert(1 if table.columns else 0, ColumnSpec(name=fk_name, data_type="bigint", semantic_type="foreign_id", nullable=False, null_rate=0))
            parent_table = tables.get(parent)
            parent_pk = next((c.name for c in parent_table.columns if c.primary_key), f"{parent.rstrip('s')}_id") if parent_table else f"{parent.rstrip('s')}_id"
            table.foreign_keys.append(ForeignKeySpec(column=fk_name, references_table=parent, references_column=parent_pk))
        tables[name] = table
        for prop_name, child_schema, is_array in deferred:
            child_schema = _merge_schema_variants(child_schema if isinstance(child_schema, dict) else {}, root)
            ref_name = None
            if isinstance(child_schema, dict) and "$ref" in child_schema:
                ref_name = str(child_schema["$ref"]).split("/")[-1]
                child_schema = _merge_schema_variants(child_schema, root)
            child_name = snake(ref_name or (prop_name if is_array else prop_name))
            child = build(child_name, child_schema, name if is_array else None)
            if not is_array:
                fk_col = f"{prop_name}_id" if not prop_name.endswith("_id") else prop_name
                child_pk = next((c.name for c in child.columns if c.primary_key), f"{child_name.rstrip('s')}_id")
                table.foreign_keys.append(ForeignKeySpec(column=fk_col, references_table=child.name, references_column=child_pk))
        return table

    build(root_name, root)
    return list(tables.values())


def parse_json_schema(req: ParseSchemaRequest) -> tuple[list[TableSpec], list[str]]:
    payload = json.loads(req.content)
    warnings: list[str] = []
    if not isinstance(payload, dict):
        raise ValueError("JSON input must be an object")
    if isinstance(payload.get("tables"), list):
        tables=[]
        for raw in payload["tables"]:
            if not isinstance(raw, dict): continue
            item=dict(raw); item.setdefault("schema_name", req.schema_name); item.setdefault("row_count", req.default_row_count)
            if "columns" in item: tables.append(TableSpec.model_validate(item))
            elif "properties" in item: tables.extend(_json_schema_tables(item, item.get("name", item.get("title", "records")), item["schema_name"], item["row_count"]))
        if not tables: raise ValueError("No tables could be parsed from JSON catalog")
        return tables, warnings
    if "properties" in payload or payload.get("type") == "object" or "$ref" in payload or "allOf" in payload:
        name = payload.get("title") or req.table_name or "records"
        tables = _json_schema_tables(payload, name, req.schema_name, req.default_row_count)
        if not tables: raise ValueError("No tables could be parsed from JSON Schema")
        return tables, warnings
    raise ValueError("JSON input must be a JSON Schema object or an object containing a tables array")


def parse_openapi(req: ParseSchemaRequest) -> tuple[list[TableSpec], list[str]]:
    payload = json.loads(req.content)
    if not isinstance(payload, dict) or not str(payload.get("openapi", "")).startswith("3."):
        raise ValueError("OpenAPI 3.x document required")
    schemas = payload.get("components", {}).get("schemas", {})
    if not isinstance(schemas, dict): schemas = {}
    tables: dict[str, TableSpec] = {}
    warnings: list[str] = []
    # Build a synthetic root that allows ordinary JSON ref resolution.
    for schema_name, raw in schemas.items():
        if not isinstance(raw, dict): continue
        table_name = re.sub(r"(?<!^)(?=[A-Z])", "_", schema_name).lower()
        expanded = _merge_schema_variants(raw, payload)
        for t in _json_schema_tables(payload, table_name, req.schema_name, req.default_row_count) if raw.get("$ref") == "#" else _json_schema_tables(expanded, table_name, req.schema_name, req.default_row_count):
            tables.setdefault(t.name, t)
    # Explicit component $refs become relationship-friendly IDs.
    component_to_table = {name: re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower() for name in schemas}
    for schema_name, raw in schemas.items():
        t = tables.get(component_to_table[schema_name])
        if not t or not isinstance(raw, dict): continue
        for prop_name, spec in raw.get("properties", {}).items():
            if not isinstance(spec, dict): continue
            ref = spec.get("$ref")
            if ref and str(ref).startswith("#/components/schemas/"):
                target_name = component_to_table.get(str(ref).split("/")[-1])
                if target_name and target_name in tables:
                    fk_col = prop_name if prop_name.endswith("_id") else f"{prop_name}_id"
                    if not any(c.name == fk_col for c in t.columns): t.columns.append(ColumnSpec(name=fk_col, data_type="bigint", semantic_type="foreign_id"))
                    parent_pk = next((c.name for c in tables[target_name].columns if c.primary_key), f"{target_name.rstrip('s')}_id")
                    t.foreign_keys.append(ForeignKeySpec(column=fk_col, references_table=target_name, references_column=parent_pk))
    # Request/response-only inline schemas are useful resources too.
    for path, operations in (payload.get("paths") or {}).items():
        if not isinstance(operations, dict): continue
        for method, op in operations.items():
            if method.lower() not in {"get","post","put","patch","delete"} or not isinstance(op, dict): continue
            candidates=[]
            rb=op.get("requestBody",{}).get("content",{}) if isinstance(op.get("requestBody"),dict) else {}
            for media in rb.values():
                if isinstance(media,dict) and isinstance(media.get("schema"),dict): candidates.append((f"{method}_{path}_request", media["schema"]))
            for code,resp in (op.get("responses") or {}).items():
                content=resp.get("content",{}) if isinstance(resp,dict) else {}
                for media in content.values():
                    if isinstance(media,dict) and isinstance(media.get("schema"),dict): candidates.append((f"{method}_{path}_{code}_response", media["schema"]))
            for candidate_name,schema in candidates:
                expanded=_merge_schema_variants(schema,payload)
                if expanded.get("type")=="array" and isinstance(expanded.get("items"),dict): expanded=_merge_schema_variants(expanded["items"],payload)
                if "properties" not in expanded: continue
                safe=re.sub(r"[^a-z0-9]+","_",candidate_name.lower()).strip("_")[:120]
                for t in _json_schema_tables(expanded,safe,req.schema_name,req.default_row_count): tables.setdefault(t.name,t)
    if not tables: raise ValueError("No object schemas could be converted from OpenAPI")
    return list(tables.values()), warnings

def _avro_type(raw: Any) -> tuple[str, bool, list[Any] | None]:
    nullable = False
    choices = None
    if isinstance(raw, list):
        nullable = "null" in raw
        non_null = [x for x in raw if x != "null"]
        raw = non_null[0] if non_null else "string"
    if isinstance(raw, dict):
        if raw.get("type") == "enum": return "varchar", nullable, raw.get("symbols")
        logical = raw.get("logicalType")
        if logical == "date": return "date", nullable, None
        if logical and "timestamp" in logical: return "timestamp", nullable, None
        if logical == "decimal": return f"decimal({raw.get('precision',18)},{raw.get('scale',2)})", nullable, None
        typ = raw.get("type", "string")
        if typ in {"record", "array", "map"}: return "json", nullable, choices
        raw = typ
    mapping = {"int":"integer","long":"bigint","float":"float","double":"double","boolean":"boolean","bytes":"varbinary","string":"varchar","record":"json","array":"json","map":"json"}
    return mapping.get(str(raw), "varchar"), nullable, choices


def parse_avro(req: ParseSchemaRequest) -> tuple[list[TableSpec], list[str]]:
    payload = json.loads(req.content)
    records = payload if isinstance(payload, list) else [payload]
    tables: dict[str, TableSpec] = {}
    named_records: dict[str, dict[str, Any]] = {}

    def snake(v: str) -> str: return re.sub(r"(?<!^)(?=[A-Z])", "_", v).lower()
    def discover(node: Any):
        if isinstance(node, dict):
            if node.get("type") == "record" and node.get("name"): named_records[str(node["name"])]=node
            for v in node.values(): discover(v)
        elif isinstance(node, list):
            for v in node: discover(v)
    discover(payload)

    def unwrap(raw: Any) -> tuple[Any,bool]:
        nullable=False
        if isinstance(raw,list): nullable="null" in raw; raw=next((x for x in raw if x!="null"),"string")
        return raw,nullable

    def build(record: dict[str, Any], parent: str | None = None) -> TableSpec:
        name=snake(str(record.get("name",req.table_name)))
        if name in tables: return tables[name]
        cols:list[ColumnSpec]=[]; fks:list[ForeignKeySpec]=[]; deferred=[]
        for field in record.get("fields",[]):
            if not isinstance(field,dict) or "name" not in field: continue
            fname=str(field["name"]); raw,nullable=unwrap(field.get("type","string"))
            target_record=None; is_array=False
            if isinstance(raw,str) and raw in named_records: target_record=named_records[raw]
            elif isinstance(raw,dict) and raw.get("type")=="record": target_record=raw
            elif isinstance(raw,dict) and raw.get("type")=="array":
                is_array=True; item,_=unwrap(raw.get("items","string"))
                if isinstance(item,dict) and item.get("type")=="record": target_record=item
                elif isinstance(item,str) and item in named_records: target_record=named_records[item]
            if target_record:
                deferred.append((fname,target_record,is_array,nullable,field.get("default"))); continue
            data_type, inferred_nullable, choices=_avro_type(field.get("type","string")); nullable=nullable or inferred_nullable
            is_pk=fname in {"id",f"{name.rstrip('s')}_id"}
            cols.append(ColumnSpec(name=fname,data_type=data_type,semantic_type=_type_to_semantic(fname,data_type,is_pk),nullable=nullable and not is_pk,unique=is_pk,primary_key=is_pk,choices=choices,enum_values=choices,default=field.get("default"),description=field.get("doc"),null_rate=0.05 if nullable and not is_pk else 0))
        if parent:
            fkcol=f"{parent.rstrip('s')}_id"
            if not any(c.name==fkcol for c in cols): cols.insert(1 if cols else 0,ColumnSpec(name=fkcol,data_type="bigint",semantic_type="foreign_id",nullable=False,null_rate=0))
        table=TableSpec(name=name,schema_name=req.schema_name,row_count=req.default_row_count,columns=cols,foreign_keys=fks)
        tables[name]=table
        if parent:
            parent_table=tables.get(parent); parent_pk=next((c.name for c in parent_table.columns if c.primary_key),f"{parent.rstrip('s')}_id") if parent_table else f"{parent.rstrip('s')}_id"
            table.foreign_keys.append(ForeignKeySpec(column=f"{parent.rstrip('s')}_id",references_table=parent,references_column=parent_pk))
        for fname,target,is_array,_nullable,_default in deferred:
            child=build(target,name if is_array else None)
            if not is_array:
                fkcol=fname if fname.endswith("_id") else f"{fname}_id"
                if not any(c.name==fkcol for c in table.columns): table.columns.append(ColumnSpec(name=fkcol,data_type="bigint",semantic_type="foreign_id"))
                child_pk=next((c.name for c in child.columns if c.primary_key),f"{child.name.rstrip('s')}_id")
                table.foreign_keys.append(ForeignKeySpec(column=fkcol,references_table=child.name,references_column=child_pk))
        return table

    for record in records:
        if isinstance(record,dict) and record.get("type")=="record": build(record)
    if not tables: raise ValueError("Avro input must contain one or more record schemas")
    return list(tables.values()), []

def _sample_type(values: list[str]) -> str:
    nonempty = [v.strip() for v in values if v is not None and v.strip() != ""]
    if not nonempty: return "varchar"
    low = [v.lower() for v in nonempty]
    if all(v in {"true", "false", "0", "1", "yes", "no"} for v in low): return "boolean"
    try:
        for v in nonempty: int(v)
        return "bigint"
    except ValueError: pass
    try:
        for v in nonempty: float(v)
        return "decimal(18,4)"
    except ValueError: pass
    date_re = re.compile(r"^\d{4}-\d{2}-\d{2}$")
    if all(date_re.match(v) for v in nonempty): return "date"
    dt_re = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")
    if all(dt_re.match(v) for v in nonempty): return "timestamp"
    return "varchar"


def parse_csv_sample(req: ParseSchemaRequest) -> tuple[list[TableSpec], list[str]]:
    reader = csv.DictReader(io.StringIO(req.content))
    if not reader.fieldnames:
        raise ValueError("CSV input must contain a header row")
    rows = []
    for i, row in enumerate(reader):
        rows.append(row)
        if i >= 199:
            break
    cols: list[ColumnSpec] = []
    table_name = req.table_name or "records"
    for name in reader.fieldnames:
        values = [r.get(name, "") or "" for r in rows]
        dtype = _sample_type(values)
        nonempty = [v for v in values if v.strip()]
        normalized = name.lower()
        is_pk = normalized in {"id", f"{table_name.rstrip('s').lower()}_id"} and len(nonempty) == len(set(nonempty))
        distinct = list(dict.fromkeys(nonempty))
        semantic = _type_to_semantic(name, dtype, is_pk)
        safe_category_name = any(token in normalized for token in ["status", "type", "tier", "category", "segment", "role", "currency", "method", "channel"])
        choices = distinct if dtype == "varchar" and safe_category_name and 1 < len(distinct) <= 10 and len(rows) >= 4 else None
        null_rate = (sum(1 for v in values if not v.strip()) / len(values)) if values else 0.05
        cols.append(ColumnSpec(
            name=name,
            data_type=dtype,
            semantic_type=semantic,
            nullable=not is_pk,
            unique=is_pk,
            primary_key=is_pk,
            choices=choices,
            null_rate=min(1.0, max(0.0, null_rate if null_rate > 0 else (0.02 if not is_pk else 0))),
        ))
    return [TableSpec(name=table_name, schema_name=req.schema_name, row_count=req.default_row_count, columns=cols)], []


def _c(name: str, data_type: str = "varchar", semantic: str | None = None, *, pk: bool = False, nullable: bool = True, choices: list[Any] | None = None, lo: float | None = None, hi: float | None = None) -> ColumnSpec:
    return ColumnSpec(
        name=name, data_type=data_type, semantic_type=semantic or _type_to_semantic(name, data_type, pk),
        primary_key=pk, unique=pk, nullable=False if pk else nullable,
        choices=choices, min_value=lo, max_value=hi, null_rate=0 if (pk or not nullable) else 0.05,
    )


def parse_description(req: ParseSchemaRequest) -> tuple[list[TableSpec], list[str]]:
    text = req.content.lower()
    rc = req.default_row_count
    s = req.schema_name
    warnings = ["Natural-language system inference used the built-in domain model. Review the inferred schema before relying on it for contract-exact tests."]

    if any(k in text for k in ["hotel", "hospitality", "reservation", "booking", "property management", "pms"]):
        tables = [
            TableSpec(name="hotels", schema_name=s, row_count=max(5, rc // 5), columns=[
                _c("hotel_id", "bigint", "id", pk=True), _c("name", semantic="company", nullable=False),
                _c("city", semantic="city", nullable=False), _c("country", semantic="country", nullable=False),
                _c("star_rating", "integer", "integer", nullable=False, lo=1, hi=5),
            ]),
            TableSpec(name="guests", schema_name=s, row_count=rc, columns=[
                _c("guest_id", "bigint", "id", pk=True), _c("first_name", semantic="first_name", nullable=False),
                _c("last_name", semantic="last_name", nullable=False), _c("email", semantic="email", nullable=False),
                _c("phone", semantic="phone"), _c("loyalty_tier", choices=["member", "silver", "gold", "platinum"]),
                _c("created_at", "timestamp", "past_datetime", nullable=False),
            ]),
            TableSpec(name="rooms", schema_name=s, row_count=max(rc, 50), columns=[
                _c("room_id", "bigint", "id", pk=True), _c("hotel_id", "bigint", "foreign_id", nullable=False),
                _c("room_number", "integer", "integer", nullable=False, lo=101, hi=1999),
                _c("room_type", choices=["Standard King", "Deluxe King", "Double Queen", "Suite"], nullable=False),
                _c("nightly_rate", "decimal(10,2)", "money", nullable=False, lo=89, hi=649),
                _c("status", choices=["available", "occupied", "maintenance"], nullable=False),
            ]),
            TableSpec(name="reservations", schema_name=s, row_count=max(rc * 2, 50), columns=[
                _c("reservation_id", "bigint", "id", pk=True), _c("guest_id", "bigint", "foreign_id", nullable=False),
                _c("room_id", "bigint", "foreign_id", nullable=False), _c("confirmation_code", semantic="confirmation_code", nullable=False),
                _c("check_in_date", "date", "future_date", nullable=False), _c("check_out_date", "date", "future_date", nullable=False),
                _c("status", choices=["confirmed", "checked_in", "checked_out", "cancelled", "no_show"], nullable=False),
                _c("created_at", "timestamp", "past_datetime", nullable=False),
            ]),
            TableSpec(name="payments", schema_name=s, row_count=max(rc * 2, 50), columns=[
                _c("payment_id", "bigint", "id", pk=True), _c("reservation_id", "bigint", "foreign_id", nullable=False),
                _c("amount", "decimal(10,2)", "money", nullable=False, lo=25, hi=4000),
                _c("currency", choices=["USD", "EUR", "GBP", "CAD"], nullable=False),
                _c("payment_status", choices=["authorized", "captured", "refunded", "failed"], nullable=False),
                _c("paid_at", "timestamp", "past_datetime"),
            ]),
        ]
        # A system description should produce a useful application model, not only a five-table toy.
        # These related entities exercise real hospitality lifecycle semantics and are linked by the
        # relationship inference pass below.
        tables.extend([
            TableSpec(name="room_types", schema_name=s, row_count=8, columns=[
                _c("room_type_id", "bigint", "id", pk=True), _c("hotel_id", "bigint", "foreign_id", nullable=False),
                _c("name", semantic="text", nullable=False), _c("base_rate", "decimal(10,2)", "money", nullable=False, lo=79, hi=699),
                _c("capacity", "integer", "integer", nullable=False, lo=1, hi=8),
            ]),
            TableSpec(name="loyalty_accounts", schema_name=s, row_count=max(5, rc // 2), columns=[
                _c("loyalty_account_id", "bigint", "id", pk=True), _c("guest_id", "bigint", "foreign_id", nullable=False),
                _c("tier", choices=["member", "silver", "gold", "platinum"], nullable=False),
                _c("points_balance", "integer", "integer", nullable=False, lo=0, hi=250000), _c("joined_at", "timestamp", "past_datetime", nullable=False),
            ]),
            TableSpec(name="cancellations", schema_name=s, row_count=max(4, rc // 5), columns=[
                _c("cancellation_id", "bigint", "id", pk=True), _c("reservation_id", "bigint", "foreign_id", nullable=False),
                _c("reason", choices=["guest_request", "payment_failure", "weather", "duplicate", "other"], nullable=False),
                _c("cancelled_at", "timestamp", "past_datetime", nullable=False), _c("fee", "decimal(10,2)", "money", lo=0, hi=500),
            ]),
            TableSpec(name="refunds", schema_name=s, row_count=max(3, rc // 6), columns=[
                _c("refund_id", "bigint", "id", pk=True), _c("payment_id", "bigint", "foreign_id", nullable=False),
                _c("amount", "decimal(10,2)", "money", nullable=False, lo=1, hi=2500),
                _c("status", choices=["pending", "processed", "failed"], nullable=False), _c("refunded_at", "timestamp", "past_datetime"),
            ]),
            TableSpec(name="invoices", schema_name=s, row_count=max(rc, 30), columns=[
                _c("invoice_id", "bigint", "id", pk=True), _c("reservation_id", "bigint", "foreign_id", nullable=False),
                _c("subtotal", "decimal(10,2)", "money", nullable=False, lo=50, hi=5000), _c("tax", "decimal(10,2)", "money", nullable=False, lo=0, hi=750),
                _c("total", "decimal(10,2)", "money", nullable=False, lo=50, hi=5750), _c("status", choices=["draft", "issued", "paid", "void"], nullable=False),
            ]),
            TableSpec(name="invoice_lines", schema_name=s, row_count=max(rc * 2, 50), columns=[
                _c("invoice_line_id", "bigint", "id", pk=True), _c("invoice_id", "bigint", "foreign_id", nullable=False),
                _c("description", semantic="text", nullable=False), _c("quantity", "integer", "integer", nullable=False, lo=1, hi=8),
                _c("unit_price", "decimal(10,2)", "money", nullable=False, lo=5, hi=1200), _c("line_total", "decimal(10,2)", "money", nullable=False, lo=5, hi=9600),
            ]),
            TableSpec(name="rate_plans", schema_name=s, row_count=12, columns=[
                _c("rate_plan_id", "bigint", "id", pk=True), _c("hotel_id", "bigint", "foreign_id", nullable=False),
                _c("name", choices=["flexible", "advance_purchase", "corporate", "member", "package"], nullable=False),
                _c("discount_percent", "decimal(5,2)", "decimal", nullable=False, lo=0, hi=40),
            ]),
            TableSpec(name="services", schema_name=s, row_count=16, columns=[
                _c("service_id", "bigint", "id", pk=True), _c("hotel_id", "bigint", "foreign_id", nullable=False),
                _c("name", choices=["breakfast", "parking", "spa", "airport_transfer", "late_checkout"], nullable=False),
                _c("price", "decimal(10,2)", "money", nullable=False, lo=0, hi=350),
            ]),
            TableSpec(name="service_bookings", schema_name=s, row_count=max(rc, 30), columns=[
                _c("service_booking_id", "bigint", "id", pk=True), _c("reservation_id", "bigint", "foreign_id", nullable=False),
                _c("service_id", "bigint", "foreign_id", nullable=False), _c("quantity", "integer", "integer", nullable=False, lo=1, hi=5),
                _c("booked_at", "timestamp", "past_datetime", nullable=False),
            ]),
            TableSpec(name="employees", schema_name=s, row_count=max(10, rc // 3), columns=[
                _c("employee_id", "bigint", "id", pk=True), _c("hotel_id", "bigint", "foreign_id", nullable=False),
                _c("full_name", semantic="full_name", nullable=False), _c("email", semantic="email", nullable=False),
                _c("role", choices=["front_desk", "housekeeping", "manager", "concierge", "maintenance"], nullable=False),
            ]),
        ])
    elif any(k in text for k in ["ecommerce", "e-commerce", "shopping", "retail", "order management", "marketplace"]):
        tables = [
            TableSpec(name="customers", schema_name=s, row_count=rc, columns=[
                _c("customer_id", "bigint", "id", pk=True), _c("first_name", semantic="first_name", nullable=False),
                _c("last_name", semantic="last_name", nullable=False), _c("email", semantic="email", nullable=False),
                _c("country", semantic="country"), _c("created_at", "timestamp", "past_datetime", nullable=False),
            ]),
            TableSpec(name="products", schema_name=s, row_count=max(rc, 50), columns=[
                _c("product_id", "bigint", "id", pk=True), _c("name", semantic="text", nullable=False),
                _c("category", choices=["apparel", "home", "electronics", "beauty", "sports"], nullable=False),
                _c("unit_price", "decimal(10,2)", "money", nullable=False, lo=5, hi=1500), _c("is_active", "boolean", "boolean", nullable=False),
            ]),
            TableSpec(name="orders", schema_name=s, row_count=max(rc * 2, 50), columns=[
                _c("order_id", "bigint", "id", pk=True), _c("customer_id", "bigint", "foreign_id", nullable=False),
                _c("order_status", choices=["pending", "paid", "shipped", "delivered", "cancelled"], nullable=False),
                _c("subtotal", "decimal(10,2)", "money", nullable=False, lo=8, hi=3000), _c("created_at", "timestamp", "past_datetime", nullable=False),
            ]),
            TableSpec(name="order_items", schema_name=s, row_count=max(rc * 4, 100), columns=[
                _c("order_item_id", "bigint", "id", pk=True), _c("order_id", "bigint", "foreign_id", nullable=False),
                _c("product_id", "bigint", "foreign_id", nullable=False), _c("quantity", "integer", "integer", nullable=False, lo=1, hi=8),
                _c("unit_price", "decimal(10,2)", "money", nullable=False, lo=5, hi=1500),
            ]),
            TableSpec(name="payments", schema_name=s, row_count=max(rc * 2, 50), columns=[
                _c("payment_id", "bigint", "id", pk=True), _c("order_id", "bigint", "foreign_id", nullable=False),
                _c("amount", "decimal(10,2)", "money", nullable=False, lo=8, hi=5000),
                _c("payment_method", choices=["visa", "mastercard", "amex", "wallet"], nullable=False),
                _c("payment_status", choices=["authorized", "captured", "refunded", "failed"], nullable=False),
            ]),
        ]
    elif any(k in text for k in ["saas", "subscription", "crm", "software platform", "b2b"]):
        tables = [
            TableSpec(name="accounts", schema_name=s, row_count=max(10, rc // 4), columns=[
                _c("account_id", "bigint", "id", pk=True), _c("company_name", semantic="company", nullable=False),
                _c("status", choices=["trial", "active", "past_due", "churned"], nullable=False), _c("created_at", "timestamp", "past_datetime", nullable=False),
            ]),
            TableSpec(name="users", schema_name=s, row_count=rc, columns=[
                _c("user_id", "bigint", "id", pk=True), _c("account_id", "bigint", "foreign_id", nullable=False),
                _c("full_name", semantic="full_name", nullable=False), _c("email", semantic="email", nullable=False),
                _c("role", choices=["owner", "admin", "member", "viewer"], nullable=False), _c("is_active", "boolean", "boolean", nullable=False),
            ]),
            TableSpec(name="plans", schema_name=s, row_count=4, columns=[
                _c("plan_id", "bigint", "id", pk=True), _c("name", choices=["free", "starter", "pro", "enterprise"], nullable=False),
                _c("monthly_price", "decimal(10,2)", "money", nullable=False, lo=0, hi=999),
            ]),
            TableSpec(name="subscriptions", schema_name=s, row_count=max(10, rc // 3), columns=[
                _c("subscription_id", "bigint", "id", pk=True), _c("account_id", "bigint", "foreign_id", nullable=False),
                _c("plan_id", "bigint", "foreign_id", nullable=False), _c("status", choices=["trialing", "active", "past_due", "cancelled"], nullable=False),
                _c("start_date", "date", "date", nullable=False), _c("end_date", "date", "date"),
            ]),
            TableSpec(name="invoices", schema_name=s, row_count=max(rc, 30), columns=[
                _c("invoice_id", "bigint", "id", pk=True), _c("subscription_id", "bigint", "foreign_id", nullable=False),
                _c("amount", "decimal(10,2)", "money", nullable=False, lo=0, hi=5000), _c("status", choices=["draft", "open", "paid", "void"], nullable=False),
                _c("created_at", "timestamp", "past_datetime", nullable=False),
            ]),
            TableSpec(name="events", schema_name=s, row_count=max(rc * 5, 100), columns=[
                _c("event_id", "bigint", "id", pk=True), _c("user_id", "bigint", "foreign_id"),
                _c("event_type", choices=["login", "page_view", "feature_used", "export", "invite_sent"], nullable=False),
                _c("created_at", "timestamp", "past_datetime", nullable=False),
            ]),
        ]
    else:
        warnings.append("No specialized built-in domain matched; a generic application model was created.")
        tables = [
            TableSpec(name="users", schema_name=s, row_count=rc, columns=[
                _c("user_id", "bigint", "id", pk=True), _c("full_name", semantic="full_name", nullable=False),
                _c("email", semantic="email", nullable=False), _c("status", choices=["active", "pending", "inactive"], nullable=False),
                _c("created_at", "timestamp", "past_datetime", nullable=False),
            ]),
            TableSpec(name="records", schema_name=s, row_count=max(rc * 2, 50), columns=[
                _c("record_id", "bigint", "id", pk=True), _c("user_id", "bigint", "foreign_id"),
                _c("name", semantic="text", nullable=False), _c("status", choices=["new", "processing", "complete"], nullable=False),
                _c("created_at", "timestamp", "past_datetime", nullable=False),
            ]),
            TableSpec(name="events", schema_name=s, row_count=max(rc * 3, 100), columns=[
                _c("event_id", "bigint", "id", pk=True), _c("record_id", "bigint", "foreign_id"),
                _c("event_type", choices=["created", "updated", "processed"], nullable=False), _c("created_at", "timestamp", "past_datetime", nullable=False),
            ]),
        ]
    return tables, warnings


def parse_sqlite_database(req: ParseSchemaRequest) -> tuple[list[TableSpec], list[str]]:
    db_path = Path(req.content.strip()).expanduser().resolve()
    if not db_path.is_file():
        raise ValueError(f"SQLite database file not found: {db_path}")
    uri = f"file:{db_path.as_posix()}?mode=ro"
    tables: list[TableSpec] = []
    warnings: list[str] = []
    try:
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
        names = [r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()]
        for table_name in names:
            escaped = table_name.replace('"', '""')
            info = conn.execute(f'PRAGMA table_info("{escaped}")').fetchall()
            unique_cols: set[str] = set()
            for idx in conn.execute(f'PRAGMA index_list("{escaped}")').fetchall():
                # columns: seq, name, unique, origin, partial
                if not idx["unique"]:
                    continue
                idx_name = str(idx["name"]).replace('"', '""')
                idx_cols = conn.execute(f'PRAGMA index_info("{idx_name}")').fetchall()
                if len(idx_cols) == 1:
                    unique_cols.add(idx_cols[0]["name"])
            columns: list[ColumnSpec] = []
            for col in info:
                name = col["name"]
                dtype = col["type"] or "varchar"
                primary = bool(col["pk"])
                nullable = not bool(col["notnull"]) and not primary
                columns.append(ColumnSpec(
                    name=name,
                    data_type=dtype,
                    semantic_type=_type_to_semantic(name, dtype, primary),
                    nullable=nullable,
                    unique=primary or name in unique_cols,
                    primary_key=primary,
                    null_rate=0.05 if nullable else 0,
                ))
            fks = []
            for fk in conn.execute(f'PRAGMA foreign_key_list("{escaped}")').fetchall():
                if fk["from"] and fk["table"]:
                    fks.append(ForeignKeySpec(
                        column=fk["from"],
                        references_table=fk["table"],
                        references_column=fk["to"] or "id",
                    ))
            if columns:
                tables.append(TableSpec(
                    name=table_name,
                    schema_name=req.schema_name,
                    row_count=req.default_row_count,
                    columns=columns,
                    foreign_keys=fks,
                ))
        conn.close()
    except sqlite3.Error as exc:
        raise ValueError(f"Could not read SQLite metadata: {exc}") from exc
    if not tables:
        raise ValueError("No user tables were found in the SQLite database")
    warnings.append(f"Read-only SQLite metadata introspection: {db_path.name}; no source rows were copied.")
    return tables, warnings

def _dedupe_and_infer_relationships(tables: list[TableSpec]) -> list[TableSpec]:
    by_name = {t.name.lower(): t for t in tables}
    singular = {}
    for t in tables:
        key = t.name.lower()
        singular[key.rstrip("s")] = key
        if key.endswith("ies"):
            singular[key[:-3] + "y"] = key

    for table in tables:
        unique: dict[tuple[str, str, str], ForeignKeySpec] = {}
        for fk in table.foreign_keys:
            unique[(fk.column.lower(), fk.references_table.lower(), fk.references_column.lower())] = fk
        existing_cols = {fk.column.lower() for fk in unique.values()}
        pk_names = {c.name.lower() for c in table.columns if c.primary_key}
        for col in table.columns:
            cname = col.name.lower()
            if cname in existing_cols or cname in pk_names or not cname.endswith("_id"):
                continue
            stem = cname[:-3]
            target_key = None
            for candidate in (stem, stem + "s", stem[:-1] + "ies" if stem.endswith("y") else ""):
                if candidate and candidate in by_name:
                    target_key = candidate
                    break
            if not target_key and stem in singular:
                target_key = singular[stem]
            if not target_key or target_key == table.name.lower():
                continue
            parent = by_name[target_key]
            parent_pk = next((c.name for c in parent.columns if c.primary_key), f"{stem}_id")
            fk = ForeignKeySpec(column=col.name, references_table=parent.name, references_column=parent_pk)
            unique[(fk.column.lower(), fk.references_table.lower(), fk.references_column.lower())] = fk
            existing_cols.add(cname)
        table.foreign_keys = list(unique.values())
    return tables

def parse_schema(req: ParseSchemaRequest) -> tuple[list[TableSpec], list[str]]:
    if req.input_format == "ddl":
        tables, warnings = parse_ddl(req)
    elif req.input_format == "json":
        tables, warnings = parse_json_schema(req)
    elif req.input_format == "openapi":
        tables, warnings = parse_openapi(req)
    elif req.input_format == "avro":
        tables, warnings = parse_avro(req)
    elif req.input_format == "csv":
        tables, warnings = parse_csv_sample(req)
    elif req.input_format == "sqlite-db":
        tables, warnings = parse_sqlite_database(req)
    else:
        tables, warnings = parse_description(req)
    return _dedupe_and_infer_relationships(tables), warnings
