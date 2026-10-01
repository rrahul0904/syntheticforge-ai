from __future__ import annotations

import importlib.util
import hashlib
import os
import re
from abc import ABC, abstractmethod
from contextlib import suppress
from typing import Any, Iterable

from ..models import ColumnSpec, ConnectorConfig, ForeignKeySpec, TableProfile, TableSpec

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_$#]*$")


class ConnectorError(RuntimeError):
    """Safe, user-actionable connector error."""


class DriverUnavailableError(ConnectorError):
    pass


def validate_identifier(value: str) -> str:
    if not _IDENT.match(value):
        raise ConnectorError(f"Unsafe identifier: {value!r}")
    return value


class BaseConnector(ABC):
    connector_name = "base"
    driver_names: tuple[str, ...] = ()
    capabilities = ("introspection", "sampling", "profiling")

    def __init__(self, config: ConnectorConfig):
        self.config = config
        self._connection: Any | None = None

    @classmethod
    def available_driver(cls) -> str | None:
        for name in cls.driver_names:
            try:
                if importlib.util.find_spec(name) is not None:
                    return name
            except (ModuleNotFoundError, ValueError):
                continue
        return None

    @classmethod
    def is_available(cls) -> bool:
        return cls.available_driver() is not None or cls.connector_name == "sqlite"

    def connect(self) -> "BaseConnector":
        if self._connection is None:
            if self.config.read_only and os.getenv("SYNTHETICFORGE_ENV", "development").lower()=="production" and not self.production_read_only_supported:
                raise ConnectorError(f"Production read-only enforcement is unverified for {self.connector_name}; use a certified read-only database role or a supported enforcement adapter")
            try:self._connection = self._connect_impl()
            except Exception as exc:raise ConnectorError(self.safe_error(exc)) from exc
            try:
                if self.config.read_only:self._set_read_only_if_supported()
            except Exception as exc:
                self.close()
                raise ConnectorError(f"Could not enforce source read-only policy for {self.connector_name}: {self.safe_error(exc)}") from exc
        return self

    @property
    def production_read_only_supported(self)->bool:
        return self.connector_name in {"sqlite","postgresql","mysql","oracle"}

    def policy_receipt(self)->dict[str,Any]:
        return {"connector":self.connector_name,"requested_read_only":self.config.read_only,"read_only_enforced":bool(self.config.read_only and self.production_read_only_supported),"read_only_mechanism":self.read_only_mechanism if self.config.read_only else "explicit-writable-target","capabilities":self.capabilities}

    @property
    def read_only_mechanism(self)->str:
        return "unproven"

    @abstractmethod
    def _connect_impl(self) -> Any:
        raise NotImplementedError

    def _set_read_only_if_supported(self) -> None:
        if os.getenv("SYNTHETICFORGE_ENV", "development").lower()=="production":
            raise ConnectorError("Connector does not implement enforced read-only mode")

    def close(self) -> None:
        if self._connection is not None:
            with suppress(Exception):
                self._connection.close()
            self._connection = None

    def __enter__(self):
        return self.connect()

    def __exit__(self, exc_type, exc, tb):
        self.close()

    def test_connection(self) -> dict[str, Any]:
        try:
            self.connect()
            rows = self._query("SELECT 1")
            return {"ok": True, "connector": self.connector_name, "result": rows[0][0] if rows else 1}
        except Exception as exc:
            return {"ok": False, "connector": self.connector_name, "error": self.safe_error(exc)}

    def safe_error(self, exc: Exception) -> str:
        text = str(exc)
        secrets=[self.config.password,self.config.username,self.config.account]
        secrets.extend(str(v) for k,v in self.config.extra.items() if any(word in str(k).lower() for word in ("token","secret","password","credential","key")))
        for secret in secrets:
            if secret:
                text = text.replace(secret, "***")
        return text[:1000]

    def _query(self, sql: str, params: Iterable[Any] | None = None) -> list[tuple[Any, ...]]:
        self.connect()
        cur = self._connection.cursor()
        try:
            cur.execute(sql, tuple(params or ()))
            rows=list(cur.fetchall()) if getattr(cur, "description", None) else []
            self._query_receipt(sql,len(rows),"success")
            return rows
        except Exception:
            self._query_receipt(sql,0,"failure")
            raise
        finally:
            with suppress(Exception):
                cur.close()

    def _query_dicts(self, sql: str, params: Iterable[Any] | None = None) -> list[dict[str, Any]]:
        self.connect()
        cur = self._connection.cursor()
        try:
            cur.execute(sql, tuple(params or ()))
            names = [d[0] for d in (cur.description or [])]
            rows=[dict(zip(names, row)) for row in cur.fetchall()]
            self._query_receipt(sql,len(rows),"success")
            return rows
        except Exception:
            self._query_receipt(sql,0,"failure")
            raise
        finally:
            with suppress(Exception):
                cur.close()

    def _query_receipt(self,sql:str,rows:int,status:str)->None:
        # Receipts describe the action without retaining raw SQL, parameters, rows or connection credentials.
        sink=getattr(self,"receipt_sink",None)
        if sink:
            statement=sql.lstrip().split(None,1)[0].upper() if sql.strip() else "EMPTY"
            sink(action="connector.query",connector=self.connector_name,policy=status,query_hash=hashlib.sha256(" ".join(sql.split()).encode()).hexdigest(),metadata={"statement_type":statement,"rows_returned":rows,"read_only":self.config.read_only})

    def list_databases(self) -> list[str]:
        return [self.config.database] if self.config.database else []

    @abstractmethod
    def list_schemas(self) -> list[str]:
        raise NotImplementedError

    @abstractmethod
    def list_tables(self, schema_name: str | None = None) -> list[str]:
        raise NotImplementedError

    def list_views(self, schema_name: str | None = None) -> list[str]:
        """Return logical views when the backend exposes them."""
        return []

    @abstractmethod
    def describe_table(self, table_name: str, schema_name: str | None = None) -> TableSpec:
        raise NotImplementedError

    def get_constraints(self, table_name: str, schema_name: str | None = None) -> dict[str, Any]:
        table = self.describe_table(table_name, schema_name)
        return {
            "primary_key_columns": table.primary_key_columns,
            "foreign_keys": [fk.model_dump() for fk in table.foreign_keys],
            "unique_constraints": [u.model_dump() for u in table.unique_constraints],
            "check_constraints": [c.model_dump() for c in table.check_constraints],
        }

    def get_relationships(self, schema_name: str | None = None) -> list[ForeignKeySpec]:
        rels: list[ForeignKeySpec] = []
        for name in self.list_tables(schema_name):
            rels.extend(self.describe_table(name, schema_name).foreign_keys)
        return rels

    @abstractmethod
    def sample_rows(self, table_name: str, schema_name: str | None = None, limit: int = 1000) -> list[dict[str, Any]]:
        raise NotImplementedError

    def profile_table(self, table_name: str, schema_name: str | None = None, limit: int = 10_000) -> TableProfile:
        from ..profiling import profile_rows
        table = self.describe_table(table_name, schema_name)
        rows = self.sample_rows(table_name, schema_name, limit)
        return profile_rows(rows, table.columns, max_sample_rows=limit)

    def introspect_system(self, schema_name: str | None = None) -> list[TableSpec]:
        result = [self.describe_table(name, schema_name) for name in self.list_tables(schema_name)]
        table_names = {t.name.lower() for t in result}
        for name in self.list_views(schema_name):
            if name.lower() in table_names:
                continue
            view = self.describe_table(name, schema_name)
            view.table_type = "view"
            result.append(view)
        return result
