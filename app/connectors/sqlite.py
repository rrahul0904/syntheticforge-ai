from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from .base import BaseConnector, ConnectorError, validate_identifier
from ..generator import semantic_from_name
from ..models import CheckConstraintSpec, ColumnSpec, ConnectorConfig, ForeignKeySpec, IndexSpec, TableSpec, UniqueConstraintSpec


class SQLiteConnector(BaseConnector):
    connector_name = "sqlite"
    driver_names = ("sqlite3",)

    def __init__(self, config: ConnectorConfig):
        super().__init__(config)
        if not config.path and not config.database:
            raise ConnectorError("SQLite requires path or database")

    def _connect_impl(self):
        path = Path(self.config.path or self.config.database or "")
        if self.config.read_only:
            return sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
        return sqlite3.connect(path)

    def list_databases(self) -> list[str]:
        return [str(Path(self.config.path or self.config.database or "").resolve())]

    def list_schemas(self) -> list[str]:
        return ["main"]

    def list_tables(self, schema_name: str | None = None) -> list[str]:
        return [r[0] for r in self._query("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]

    def describe_table(self, table_name: str, schema_name: str | None = None) -> TableSpec:
        table = validate_identifier(table_name)
        rows = self._query(f'PRAGMA table_info("{table}")')
        if not rows:
            raise ConnectorError(f"SQLite table not found: {table}")
        columns: list[ColumnSpec] = []
        pk_cols: list[tuple[int, str]] = []
        for _cid, name, dtype, notnull, default, pk_order in rows:
            col = ColumnSpec(
                name=name,
                data_type=dtype or "TEXT",
                nullable=not bool(notnull or pk_order),
                primary_key=bool(pk_order),
                unique=bool(pk_order),
                default=default,
                semantic_type=None,
            )
            col.semantic_type = semantic_from_name(col)
            columns.append(col)
            if pk_order:
                pk_cols.append((int(pk_order), name))
        fks = [ForeignKeySpec(column=r[3], references_table=r[2], references_column=r[4] or "id") for r in self._query(f'PRAGMA foreign_key_list("{table}")')]
        indexes: list[IndexSpec] = []
        unique_constraints: list[UniqueConstraintSpec] = []
        for idx in self._query(f'PRAGMA index_list("{table}")'):
            idx_name = idx[1]
            unique = bool(idx[2])
            cols = [r[2] for r in self._query(f'PRAGMA index_info("{validate_identifier(idx_name)}")')]
            indexes.append(IndexSpec(name=idx_name, columns=cols, unique=unique))
            if unique:
                unique_constraints.append(UniqueConstraintSpec(name=idx_name, columns=cols))
        create_sql_rows = self._query("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", [table])
        checks: list[CheckConstraintSpec] = []
        if create_sql_rows and create_sql_rows[0][0]:
            import re
            create_sql = create_sql_rows[0][0]
            expressions: list[str] = []
            for check_match in re.finditer(r"CHECK\s*\(", create_sql, re.I):
                start = check_match.end()
                depth = 1
                quote = None
                pos = start
                while pos < len(create_sql) and depth:
                    ch = create_sql[pos]
                    if quote:
                        if ch == quote and (pos == 0 or create_sql[pos-1] != "\\"):
                            quote = None
                    elif ch in {"'", '"'}:
                        quote = ch
                    elif ch == "(":
                        depth += 1
                    elif ch == ")":
                        depth -= 1
                    pos += 1
                if depth == 0:
                    expressions.append(create_sql[start:pos-1].strip())
            for i, expr in enumerate(expressions):
                checks.append(CheckConstraintSpec(name=f"check_{i+1}", expression=expr))
                # Promote simple CHECK(col IN (...)) constraints into generation domains.
                match = re.match(r"[\"`\[]?([A-Za-z_][A-Za-z0-9_]*)[\"`\]]?\s+IN\s*\((.+)\)", expr, re.I | re.S)
                if match:
                    col_name, raw_values = match.groups()
                    values = [v.strip().strip("'\"") for v in raw_values.split(',') if v.strip()]
                    for col in columns:
                        if col.name.lower() == col_name.lower() and values:
                            col.choices = values
                            col.enum_values = values
                            col.semantic_type = "choice"
                            break
                bound = re.match(r"[\"`\[]?([A-Za-z_][A-Za-z0-9_]*)[\"`\]]?\s*(>=|>|<=|<)\s*(-?\d+(?:\.\d+)?)", expr, re.I)
                if bound:
                    col_name, op, raw = bound.groups()
                    value = float(raw)
                    for col in columns:
                        if col.name.lower() == col_name.lower():
                            if op in {">=", ">"}:
                                col.min_value = value + (1 if op == ">" and value.is_integer() else 0)
                            else:
                                col.max_value = value - (1 if op == "<" and value.is_integer() else 0)
                            break
        return TableSpec(
            name=table,
            schema_name="main",
            columns=columns,
            foreign_keys=fks,
            primary_key_columns=[name for _, name in sorted(pk_cols)],
            unique_constraints=unique_constraints,
            check_constraints=checks,
            indexes=indexes,
        )

    def sample_rows(self, table_name: str, schema_name: str | None = None, limit: int = 1000) -> list[dict[str, Any]]:
        table = validate_identifier(table_name)
        limit = max(1, min(int(limit), 100_000))
        return self._query_dicts(f'SELECT * FROM "{table}" LIMIT ?', [limit])
