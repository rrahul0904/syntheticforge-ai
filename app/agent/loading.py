from __future__ import annotations

import csv
import io
import json
import zipfile
from pathlib import Path

from ..loaders import load_table
from ..models import AgentLoadApprovalRequest, ColumnSpec, GeneratedTable


def _tables_from_archive(path: Path) -> list[GeneratedTable]:
    tables: list[GeneratedTable] = []
    with zipfile.ZipFile(path) as zf:
        manifest = json.loads(zf.read("manifest.json"))
        for meta in manifest.get("tables", []):
            schema = meta["schema"]; name = meta["table"]
            csv_name = f"tables/{schema}.{name}.csv"
            rows = list(csv.DictReader(io.StringIO(zf.read(csv_name).decode("utf-8"))))
            normalized = [{k: (None if v == "" else v) for k, v in row.items()} for row in rows]
            tables.append(GeneratedTable(
                name=name,
                schema_name=schema,
                columns=[ColumnSpec.model_validate(c) for c in meta.get("columns", [])],
                foreign_keys=meta.get("foreign_keys", []),
                rows=normalized,
            ))
    return tables


def load_approved_artifact(path: str, req: AgentLoadApprovalRequest) -> dict:
    if req.config.read_only:
        raise ValueError("Target loading requires read_only=false")
    if req.mode == "truncate" and not req.confirm_destructive:
        raise ValueError("truncate mode requires confirm_destructive=true")
    tables = _tables_from_archive(Path(path))
    results = []
    total = 0
    for table in tables:
        result = load_table(req.config, table, req.batch_size, False, req.mode, req.confirm_destructive)
        results.append(result); total += int(result.get("rows", 0))
    return {"ok": True, "tables": len(tables), "rows": total, "results": results}
