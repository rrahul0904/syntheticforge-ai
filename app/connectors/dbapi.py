from __future__ import annotations

import importlib
from typing import Any

from .base import BaseConnector, ConnectorError, DriverUnavailableError, validate_identifier
from ..generator import semantic_from_name
from ..models import CheckConstraintSpec, ColumnSpec, ConnectorConfig, ForeignKeySpec, TableSpec, UniqueConstraintSpec


class InformationSchemaConnector(BaseConnector):
    param = "%s"
    quote_char = '"'
    default_schema = "public"

    def quote(self, name: str) -> str:
        validate_identifier(name)
        if self.quote_char == "[":
            return f"[{name}]"
        return f"{self.quote_char}{name}{self.quote_char}"

    def list_schemas(self) -> list[str]:
        rows = self._query("SELECT schema_name FROM information_schema.schemata ORDER BY schema_name")
        return [str(r[0]) for r in rows]

    def list_tables(self, schema_name: str | None = None) -> list[str]:
        schema = schema_name or self.config.schema_name or self.default_schema
        sql = f"SELECT table_name FROM information_schema.tables WHERE table_schema={self.param} AND table_type IN ('BASE TABLE','TABLE') ORDER BY table_name"
        return [str(r[0]) for r in self._query(sql, [schema])]

    def list_views(self, schema_name: str | None = None) -> list[str]:
        schema = schema_name or self.config.schema_name or self.default_schema
        sql = f"SELECT table_name FROM information_schema.tables WHERE table_schema={self.param} AND table_type IN ('VIEW','MATERIALIZED VIEW') ORDER BY table_name"
        try:
            return [str(r[0]) for r in self._query(sql, [schema])]
        except Exception:
            sql = f"SELECT table_name FROM information_schema.views WHERE table_schema={self.param} ORDER BY table_name"
            try: return [str(r[0]) for r in self._query(sql, [schema])]
            except Exception: return []

    def _column_rows(self, table: str, schema: str) -> list[dict[str, Any]]:
        sql = f"""
        SELECT column_name, data_type, is_nullable, column_default,
               character_maximum_length, numeric_precision, numeric_scale
        FROM information_schema.columns
        WHERE table_schema={self.param} AND table_name={self.param}
        ORDER BY ordinal_position
        """
        return self._query_dicts(sql, [schema, table])

    def _pk_columns(self, table: str, schema: str) -> list[str]:
        sql = f"""
        SELECT kcu.column_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON tc.constraint_name=kcu.constraint_name AND tc.table_schema=kcu.table_schema
        WHERE tc.table_schema={self.param} AND tc.table_name={self.param}
          AND tc.constraint_type='PRIMARY KEY'
        ORDER BY kcu.ordinal_position
        """
        return [str(r[0]) for r in self._query(sql, [schema, table])]

    def _foreign_keys(self, table: str, schema: str) -> list[ForeignKeySpec]:
        sql = f"""
        SELECT kcu.constraint_name, kcu.column_name,
               ccu.table_name AS foreign_table_name, ccu.column_name AS foreign_column_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON tc.constraint_name=kcu.constraint_name AND tc.table_schema=kcu.table_schema
        JOIN information_schema.constraint_column_usage ccu
          ON ccu.constraint_name=tc.constraint_name AND ccu.table_schema=tc.table_schema
        WHERE tc.constraint_type='FOREIGN KEY'
          AND tc.table_schema={self.param} AND tc.table_name={self.param}
        ORDER BY kcu.constraint_name, kcu.ordinal_position
        """
        grouped: dict[tuple[str, str], tuple[list[str], list[str]]] = {}
        for r in self._query(sql, [schema, table]):
            key=(str(r[0]),str(r[2])); child,parent=grouped.setdefault(key,([],[])); child.append(str(r[1])); parent.append(str(r[3]))
        return [ForeignKeySpec(name=name,column=children[0],columns=children,references_table=ref_table,references_column=parents[0],references_columns=parents) for (name,ref_table),(children,parents) in grouped.items()]

    def _unique_constraints(self, table: str, schema: str) -> list[UniqueConstraintSpec]:
        sql = f"""
        SELECT tc.constraint_name, kcu.column_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON tc.constraint_name=kcu.constraint_name AND tc.table_schema=kcu.table_schema
        WHERE tc.constraint_type='UNIQUE' AND tc.table_schema={self.param} AND tc.table_name={self.param}
        ORDER BY tc.constraint_name, kcu.ordinal_position
        """
        grouped: dict[str, list[str]] = {}
        for name, col in self._query(sql, [schema, table]):
            grouped.setdefault(str(name), []).append(str(col))
        return [UniqueConstraintSpec(name=n, columns=cols) for n, cols in grouped.items()]

    def _check_constraints(self, table: str, schema: str) -> list[CheckConstraintSpec]:
        sql = f"""
        SELECT tc.constraint_name, cc.check_clause
        FROM information_schema.table_constraints tc
        JOIN information_schema.check_constraints cc ON cc.constraint_name=tc.constraint_name
        WHERE tc.constraint_type='CHECK' AND tc.table_schema={self.param} AND tc.table_name={self.param}
        ORDER BY tc.constraint_name
        """
        try:
            return [CheckConstraintSpec(name=str(name),expression=str(expr)) for name,expr in self._query(sql,[schema,table]) if expr]
        except Exception:
            return []

    def describe_table(self, table_name: str, schema_name: str | None = None) -> TableSpec:
        table = validate_identifier(table_name)
        schema = validate_identifier(schema_name or self.config.schema_name or self.default_schema)
        pk = self._pk_columns(table, schema)
        columns: list[ColumnSpec] = []
        for row in self._column_rows(table, schema):
            name = str(row.get("column_name"))
            col = ColumnSpec(
                name=name,
                data_type=str(row.get("data_type") or "varchar"),
                nullable=str(row.get("is_nullable", "YES")).upper() == "YES" and name not in pk,
                primary_key=name in pk,
                unique=len(pk) == 1 and name in pk,
                default=row.get("column_default"),
                length=row.get("character_maximum_length"),
                precision=row.get("numeric_precision"),
                scale=row.get("numeric_scale"),
            )
            col.semantic_type = semantic_from_name(col)
            columns.append(col)
        if not columns:
            raise ConnectorError(f"Table not found or inaccessible: {schema}.{table}")
        return TableSpec(
            name=table,
            schema_name=schema,
            columns=columns,
            primary_key_columns=pk,
            foreign_keys=self._foreign_keys(table, schema),
            unique_constraints=self._unique_constraints(table, schema),
            check_constraints=self._check_constraints(table, schema),
        )

    def sample_rows(self, table_name: str, schema_name: str | None = None, limit: int = 1000) -> list[dict[str, Any]]:
        table = validate_identifier(table_name)
        schema = validate_identifier(schema_name or self.config.schema_name or self.default_schema)
        limit = max(1, min(int(limit), 100_000))
        return self._query_dicts(f"SELECT * FROM {self.quote(schema)}.{self.quote(table)} LIMIT {limit}")


class PostgreSQLConnector(InformationSchemaConnector):
    connector_name = "postgresql"
    driver_names = ("psycopg", "psycopg2")

    def _connect_impl(self):
        driver = self.available_driver()
        if not driver:
            raise DriverUnavailableError("PostgreSQL driver missing. Install syntheticforge-ai[postgres].")
        module = importlib.import_module(driver)
        kwargs = dict(host=self.config.host, port=self.config.port or 5432, dbname=self.config.database, user=self.config.username, password=self.config.password)
        kwargs = {k: v for k, v in kwargs.items() if v is not None}
        conn = module.connect(**kwargs)
        if self.config.read_only:
            try:
                conn.autocommit = True
                cur = conn.cursor(); cur.execute("SET default_transaction_read_only = on"); cur.close()
            except Exception:
                pass
        return conn

    def list_databases(self) -> list[str]:
        return [str(r[0]) for r in self._query("SELECT datname FROM pg_database WHERE datallowconn AND NOT datistemplate ORDER BY datname")]


class RedshiftConnector(PostgreSQLConnector):
    connector_name = "redshift"

    def _connect_impl(self):
        return super()._connect_impl()


class MySQLConnector(InformationSchemaConnector):
    connector_name = "mysql"
    default_schema = "mysql"
    driver_names = ("pymysql", "mysql.connector")
    quote_char = "`"

    def _connect_impl(self):
        driver = self.available_driver()
        if not driver:
            raise DriverUnavailableError("MySQL driver missing. Install syntheticforge-ai[mysql].")
        module = importlib.import_module(driver)
        kwargs = dict(host=self.config.host or "localhost", port=self.config.port or 3306, user=self.config.username, password=self.config.password, database=self.config.database)
        kwargs = {k: v for k, v in kwargs.items() if v is not None}
        if driver == "mysql.connector":
            return module.connect(**kwargs)
        kwargs["read_timeout"] = int(self.config.extra.get("read_timeout", 30))
        return module.connect(**kwargs)

    def list_databases(self) -> list[str]:
        return [str(r[0]) for r in self._query("SHOW DATABASES")]

    def list_schemas(self) -> list[str]:
        return self.list_databases()


class SQLServerConnector(InformationSchemaConnector):
    connector_name = "sqlserver"
    default_schema = "dbo"
    driver_names = ("pyodbc",)
    param = "?"
    quote_char = "["

    def _connect_impl(self):
        if not self.available_driver():
            raise DriverUnavailableError("SQL Server driver missing. Install syntheticforge-ai[sqlserver] and an ODBC driver.")
        pyodbc = importlib.import_module("pyodbc")
        if self.config.extra.get("connection_string"):
            return pyodbc.connect(self.config.extra["connection_string"])
        driver = self.config.extra.get("odbc_driver", "ODBC Driver 18 for SQL Server")
        parts = [f"DRIVER={{{driver}}}", f"SERVER={self.config.host},{self.config.port or 1433}"]
        if self.config.database: parts.append(f"DATABASE={self.config.database}")
        if self.config.username: parts.append(f"UID={self.config.username}")
        if self.config.password: parts.append(f"PWD={self.config.password}")
        parts.append("TrustServerCertificate=yes")
        return pyodbc.connect(";".join(parts) + ";")

    def list_databases(self) -> list[str]:
        return [str(r[0]) for r in self._query("SELECT name FROM sys.databases ORDER BY name")]

    def sample_rows(self, table_name: str, schema_name: str | None = None, limit: int = 1000) -> list[dict[str, Any]]:
        table = validate_identifier(table_name); schema = validate_identifier(schema_name or self.config.schema_name or "dbo")
        limit = max(1, min(int(limit), 100_000))
        return self._query_dicts(f"SELECT TOP {limit} * FROM {self.quote(schema)}.{self.quote(table)}")


class OracleConnector(BaseConnector):
    connector_name = "oracle"
    driver_names = ("oracledb",)

    def _connect_impl(self):
        if not self.available_driver():
            raise DriverUnavailableError("Oracle driver missing. Install syntheticforge-ai[oracle].")
        oracledb = importlib.import_module("oracledb")
        dsn = self.config.extra.get("dsn") or oracledb.makedsn(self.config.host or "localhost", self.config.port or 1521, service_name=self.config.database)
        return oracledb.connect(user=self.config.username, password=self.config.password, dsn=dsn)

    def list_databases(self) -> list[str]:
        return [self.config.database or "ORACLE"]

    def list_schemas(self) -> list[str]:
        return [str(r[0]) for r in self._query("SELECT username FROM all_users ORDER BY username")]

    def list_tables(self, schema_name: str | None = None) -> list[str]:
        schema = (schema_name or self.config.schema_name or self.config.username or "").upper()
        return [str(r[0]) for r in self._query("SELECT table_name FROM all_tables WHERE owner=:1 ORDER BY table_name", [schema])]

    def _check_constraints(self, table: str, schema: str) -> list[CheckConstraintSpec]:
        sql = f"""
        SELECT tc.constraint_name, cc.check_clause
        FROM information_schema.table_constraints tc
        JOIN information_schema.check_constraints cc ON cc.constraint_name=tc.constraint_name
        WHERE tc.constraint_type='CHECK' AND tc.table_schema={self.param} AND tc.table_name={self.param}
        ORDER BY tc.constraint_name
        """
        try:
            return [CheckConstraintSpec(name=str(name),expression=str(expr)) for name,expr in self._query(sql,[schema,table]) if expr]
        except Exception:
            return []

    def describe_table(self, table_name: str, schema_name: str | None = None) -> TableSpec:
        table = validate_identifier(table_name).upper(); schema = validate_identifier(schema_name or self.config.schema_name or self.config.username or "").upper()
        col_rows = self._query("SELECT column_name,data_type,nullable,data_default,data_length,data_precision,data_scale FROM all_tab_columns WHERE owner=:1 AND table_name=:2 ORDER BY column_id", [schema, table])
        pk_rows = self._query("SELECT cols.column_name FROM all_constraints cons JOIN all_cons_columns cols ON cons.owner=cols.owner AND cons.constraint_name=cols.constraint_name WHERE cons.constraint_type='P' AND cons.owner=:1 AND cons.table_name=:2 ORDER BY cols.position", [schema, table])
        pk = [str(r[0]) for r in pk_rows]
        columns=[]
        for name,dtype,nullable,default,length,precision,scale in col_rows:
            col=ColumnSpec(name=str(name),data_type=str(dtype),nullable=str(nullable)=="Y" and str(name) not in pk,primary_key=str(name) in pk,default=str(default).strip() if default is not None else None,length=length,precision=precision,scale=scale)
            col.semantic_type=semantic_from_name(col); columns.append(col)
        if not columns: raise ConnectorError(f"Oracle table not found: {schema}.{table}")
        fk_sql="""SELECT c.constraint_name,cc.column_name,r.table_name,rcc.column_name FROM all_constraints c JOIN all_cons_columns cc ON c.owner=cc.owner AND c.constraint_name=cc.constraint_name JOIN all_constraints r ON c.r_owner=r.owner AND c.r_constraint_name=r.constraint_name JOIN all_cons_columns rcc ON r.owner=rcc.owner AND r.constraint_name=rcc.constraint_name AND cc.position=rcc.position WHERE c.constraint_type='R' AND c.owner=:1 AND c.table_name=:2 ORDER BY c.constraint_name,cc.position"""
        grouped={}
        for cname,child,ref_table,parent in self._query(fk_sql,[schema,table]):
            key=(str(cname),str(ref_table)); pair=grouped.setdefault(key,([],[])); pair[0].append(str(child)); pair[1].append(str(parent))
        fks=[ForeignKeySpec(name=name,column=children[0],columns=children,references_table=ref,references_column=parents[0],references_columns=parents) for (name,ref),(children,parents) in grouped.items()]
        unique_sql="""SELECT c.constraint_name,cc.column_name FROM all_constraints c JOIN all_cons_columns cc ON c.owner=cc.owner AND c.constraint_name=cc.constraint_name WHERE c.constraint_type='U' AND c.owner=:1 AND c.table_name=:2 ORDER BY c.constraint_name,cc.position"""
        uniques={}
        for cname,col in self._query(unique_sql,[schema,table]): uniques.setdefault(str(cname),[]).append(str(col))
        checks=[]
        for cname,expr in self._query("SELECT constraint_name,search_condition_vc FROM all_constraints WHERE constraint_type='C' AND owner=:1 AND table_name=:2 AND generated='USER NAME' ORDER BY constraint_name",[schema,table]):
            if expr: checks.append(CheckConstraintSpec(name=str(cname),expression=str(expr)))
        return TableSpec(name=table,schema_name=schema,columns=columns,primary_key_columns=pk,foreign_keys=fks,unique_constraints=[UniqueConstraintSpec(name=n,columns=c) for n,c in uniques.items()],check_constraints=checks)

    def sample_rows(self, table_name: str, schema_name: str | None = None, limit: int = 1000) -> list[dict[str, Any]]:
        table=validate_identifier(table_name); schema=validate_identifier(schema_name or self.config.schema_name or self.config.username or "")
        limit=max(1,min(int(limit),100_000))
        return self._query_dicts(f'SELECT * FROM "{schema}"."{table}" FETCH FIRST {limit} ROWS ONLY')
