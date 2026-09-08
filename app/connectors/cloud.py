from __future__ import annotations

import importlib
from typing import Any

from .base import BaseConnector, ConnectorError, DriverUnavailableError, validate_identifier
from ..generator import semantic_from_name
from ..models import CheckConstraintSpec, ColumnSpec, ConnectorConfig, ForeignKeySpec, TableSpec, UniqueConstraintSpec


class SnowflakeConnector(BaseConnector):
    connector_name = "snowflake"
    driver_names = ("snowflake.connector",)

    def _connect_impl(self):
        if not self.available_driver():
            raise DriverUnavailableError("Snowflake connector missing. Install syntheticforge-ai[snowflake].")
        sf = importlib.import_module("snowflake.connector")
        kwargs = {"account": self.config.account, "user": self.config.username, "password": self.config.password, "database": self.config.database, "schema": self.config.schema_name, "warehouse": self.config.warehouse}
        kwargs.update(self.config.extra)
        return sf.connect(**{k:v for k,v in kwargs.items() if v is not None})

    def list_databases(self) -> list[str]:
        return [str(r[1] if len(r)>1 else r[0]) for r in self._query("SHOW DATABASES")]

    def list_schemas(self) -> list[str]:
        return [str(r[1] if len(r)>1 else r[0]) for r in self._query("SHOW SCHEMAS")]

    def list_tables(self, schema_name: str | None = None) -> list[str]:
        schema=validate_identifier(schema_name or self.config.schema_name or "PUBLIC")
        rows=self._query("SELECT table_name FROM information_schema.tables WHERE table_schema=%s AND table_type='BASE TABLE' ORDER BY table_name",[schema.upper()])
        return [str(r[0]) for r in rows]

    def list_views(self, schema_name: str | None = None) -> list[str]:
        schema=validate_identifier(schema_name or self.config.schema_name or "PUBLIC")
        rows=self._query("SELECT table_name FROM information_schema.views WHERE table_schema=%s ORDER BY table_name",[schema.upper()])
        return [str(r[0]) for r in rows]

    def describe_table(self, table_name: str, schema_name: str | None = None) -> TableSpec:
        table=validate_identifier(table_name); schema=validate_identifier(schema_name or self.config.schema_name or "PUBLIC")
        rows=self._query("SELECT column_name,data_type,is_nullable,column_default,character_maximum_length,numeric_precision,numeric_scale FROM information_schema.columns WHERE table_schema=%s AND table_name=%s ORDER BY ordinal_position",[schema.upper(),table.upper()])
        columns=[]
        for name,dtype,nullable,default,length,precision,scale in rows:
            col=ColumnSpec(name=str(name),data_type=str(dtype),nullable=str(nullable).upper()=="YES",default=default,length=length,precision=precision,scale=scale)
            col.semantic_type=semantic_from_name(col); columns.append(col)
        if not columns: raise ConnectorError(f"Snowflake table not found: {schema}.{table}")
        pk=[]; fks=[]; uniques=[]; checks=[]
        try:
            rows=self._query("""SELECT tc.constraint_name,kcu.column_name,kcu.ordinal_position FROM information_schema.table_constraints tc JOIN information_schema.key_column_usage kcu ON tc.constraint_catalog=kcu.constraint_catalog AND tc.constraint_schema=kcu.constraint_schema AND tc.constraint_name=kcu.constraint_name WHERE tc.table_schema=%s AND tc.table_name=%s AND tc.constraint_type='PRIMARY KEY' ORDER BY kcu.ordinal_position""",[schema.upper(),table.upper()])
            pk=[str(r[1]) for r in rows]
        except Exception: pass
        try:
            rows=self._query("""SELECT tc.constraint_name,kcu.column_name,rc.unique_constraint_name,ukcu.table_name,ukcu.column_name,kcu.ordinal_position FROM information_schema.table_constraints tc JOIN information_schema.key_column_usage kcu ON tc.constraint_catalog=kcu.constraint_catalog AND tc.constraint_schema=kcu.constraint_schema AND tc.constraint_name=kcu.constraint_name JOIN information_schema.referential_constraints rc ON rc.constraint_catalog=tc.constraint_catalog AND rc.constraint_schema=tc.constraint_schema AND rc.constraint_name=tc.constraint_name JOIN information_schema.key_column_usage ukcu ON ukcu.constraint_catalog=rc.unique_constraint_catalog AND ukcu.constraint_schema=rc.unique_constraint_schema AND ukcu.constraint_name=rc.unique_constraint_name AND ukcu.ordinal_position=kcu.position_in_unique_constraint WHERE tc.table_schema=%s AND tc.table_name=%s AND tc.constraint_type='FOREIGN KEY' ORDER BY tc.constraint_name,kcu.ordinal_position""",[schema.upper(),table.upper()])
            grouped={}
            for cname,child,_unique_name,ref_table,parent,_pos in rows:
                key=(str(cname),str(ref_table)); pair=grouped.setdefault(key,([],[]));pair[0].append(str(child));pair[1].append(str(parent))
            fks=[ForeignKeySpec(name=n,column=cs[0],columns=cs,references_table=rt,references_column=ps[0],references_columns=ps) for (n,rt),(cs,ps) in grouped.items()]
        except Exception: pass
        try:
            rows=self._query("""SELECT tc.constraint_name,kcu.column_name,kcu.ordinal_position FROM information_schema.table_constraints tc JOIN information_schema.key_column_usage kcu ON tc.constraint_catalog=kcu.constraint_catalog AND tc.constraint_schema=kcu.constraint_schema AND tc.constraint_name=kcu.constraint_name WHERE tc.table_schema=%s AND tc.table_name=%s AND tc.constraint_type='UNIQUE' ORDER BY tc.constraint_name,kcu.ordinal_position""",[schema.upper(),table.upper()])
            grouped={}
            for cname,col,_pos in rows: grouped.setdefault(str(cname),[]).append(str(col))
            uniques=[UniqueConstraintSpec(name=n,columns=cs) for n,cs in grouped.items()]
        except Exception: pass
        try:
            rows=self._query("""SELECT tc.constraint_name,cc.check_clause FROM information_schema.table_constraints tc JOIN information_schema.check_constraints cc ON tc.constraint_catalog=cc.constraint_catalog AND tc.constraint_schema=cc.constraint_schema AND tc.constraint_name=cc.constraint_name WHERE tc.table_schema=%s AND tc.table_name=%s AND tc.constraint_type='CHECK'""",[schema.upper(),table.upper()])
            checks=[CheckConstraintSpec(name=str(n),expression=str(e)) for n,e in rows if e]
        except Exception: pass
        for col in columns:
            if col.name in pk: col.primary_key=True; col.nullable=False; col.unique=len(pk)==1
        return TableSpec(name=table,schema_name=schema,columns=columns,primary_key_columns=pk,foreign_keys=fks,unique_constraints=uniques,check_constraints=checks)

    def sample_rows(self, table_name: str, schema_name: str | None = None, limit: int = 1000) -> list[dict[str, Any]]:
        table=validate_identifier(table_name); schema=validate_identifier(schema_name or self.config.schema_name or "PUBLIC"); limit=max(1,min(int(limit),100_000))
        return self._query_dicts(f'SELECT * FROM "{schema}"."{table}" LIMIT {limit}')


class BigQueryConnector(BaseConnector):
    connector_name = "bigquery"
    driver_names = ("google.cloud.bigquery",)

    def _connect_impl(self):
        if not self.available_driver():
            raise DriverUnavailableError("BigQuery library missing. Install syntheticforge-ai[bigquery].")
        bq=importlib.import_module("google.cloud.bigquery")
        return bq.Client(project=self.config.project or self.config.database, **self.config.extra)

    def _query(self, sql: str, params=None):
        if params:
            raise ConnectorError("BigQuery connector uses fully parameterized high-level methods")
        return [tuple(row.values()) for row in self.connect().query(sql).result()]

    def test_connection(self) -> dict[str, Any]:
        try:
            client=self.connect(); next(iter(client.list_datasets(max_results=1)), None)
            return {"ok":True,"connector":"bigquery","result":1}
        except Exception as exc:
            return {"ok":False,"connector":"bigquery","error":self.safe_error(exc)}

    def list_databases(self) -> list[str]:
        return [self.config.project or getattr(self.connect(),"project","")]

    def list_schemas(self) -> list[str]:
        return [d.dataset_id for d in self.connect().list_datasets()]

    def list_tables(self, schema_name: str | None = None) -> list[str]:
        dataset=validate_identifier(schema_name or self.config.schema_name or "")
        return [t.table_id for t in self.connect().list_tables(dataset)]

    def describe_table(self, table_name: str, schema_name: str | None = None) -> TableSpec:
        table=validate_identifier(table_name); dataset=validate_identifier(schema_name or self.config.schema_name or "")
        project=self.config.project or self.config.database or self.connect().project
        obj=self.connect().get_table(f"{project}.{dataset}.{table}")
        columns=[]
        def add_field(field,prefix=""):
            name=f"{prefix}{field.name}"
            dtype=field.field_type + ("[]" if field.mode=="REPEATED" else "")
            col=ColumnSpec(name=name,data_type=dtype,nullable=field.mode!="REQUIRED",description=getattr(field,"description",None))
            col.semantic_type=semantic_from_name(col); columns.append(col)
            for child in getattr(field,"fields",()) or ():
                add_field(child,prefix=name+".")
        for f in obj.schema: add_field(f)
        return TableSpec(name=table,schema_name=dataset,columns=columns,row_count=getattr(obj,"num_rows",None) or None)

    def sample_rows(self, table_name: str, schema_name: str | None = None, limit: int = 1000) -> list[dict[str, Any]]:
        table=validate_identifier(table_name); dataset=validate_identifier(schema_name or self.config.schema_name or ""); project=self.config.project or self.config.database or self.connect().project
        limit=max(1,min(int(limit),100_000))
        query=f"SELECT * FROM `{project}.{dataset}.{table}` LIMIT {limit}"
        return [dict(row.items()) for row in self.connect().query(query).result()]

    def close(self) -> None:
        if self._connection is not None and hasattr(self._connection,"close"):
            self._connection.close()
        self._connection=None
