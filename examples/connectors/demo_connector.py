"""Minimal third-party connector example. Copy this into your own package and expose it via
`syntheticforge.connectors` Python entry points to register it without changing the core engine.
"""
from app.connectors.base import BaseConnector
from app.models import ColumnSpec, TableSpec

class DemoConnector(BaseConnector):
    connector_name="demo"
    driver_names=()
    def _connect_impl(self): return self
    def cursor(self): return _Cursor()
    def close(self): self._connection=None
    def list_schemas(self): return ["demo"]
    def list_tables(self,schema_name=None): return ["widgets"]
    def describe_table(self,table_name,schema_name=None):
        return TableSpec(name="widgets",schema_name="demo",columns=[ColumnSpec(name="widget_id",data_type="bigint",primary_key=True),ColumnSpec(name="name")])
    def sample_rows(self,table_name,schema_name=None,limit=1000): return [{"widget_id":1,"name":"synthetic"}][:limit]

class _Cursor:
    description=[("value",)]
    def execute(self,*args,**kwargs): return self
    def fetchall(self): return [(1,)]
    def close(self): pass
