from __future__ import annotations

from importlib import metadata
from typing import Type

from .base import BaseConnector, ConnectorError
from . import CONNECTORS

ENTRYPOINT_GROUP="syntheticforge.connectors"


def register_connector(name:str,connector_cls:Type[BaseConnector],replace:bool=False)->None:
    key=name.strip().lower()
    if not key or not key.replace("_","").isalnum(): raise ConnectorError("Connector name must be alphanumeric/underscore")
    if key in CONNECTORS and not replace: raise ConnectorError(f"Connector already registered: {key}")
    if not issubclass(connector_cls,BaseConnector): raise ConnectorError("Connector must subclass BaseConnector")
    CONNECTORS[key]=connector_cls


def load_entrypoint_connectors()->list[str]:
    loaded=[]
    try: eps=metadata.entry_points(group=ENTRYPOINT_GROUP)
    except TypeError: eps=metadata.entry_points().get(ENTRYPOINT_GROUP,[])
    for ep in eps:
        cls=ep.load(); register_connector(ep.name,cls,replace=True); loaded.append(ep.name)
    return loaded


def connector_contract(connector:BaseConnector,schema_name:str|None=None)->dict[str,bool]:
    """Runtime contract probe reusable by third-party connector tests."""
    checks={}
    result=connector.test_connection(); checks["test_connection"]=bool(result.get("ok"))
    if not checks["test_connection"]: return checks
    schemas=connector.list_schemas(); checks["list_schemas"]=isinstance(schemas,list)
    tables=connector.list_tables(schema_name or (schemas[0] if schemas else None)); checks["list_tables"]=isinstance(tables,list)
    if tables:
        spec=connector.describe_table(tables[0],schema_name or (schemas[0] if schemas else None)); checks["describe_table"]=bool(spec.columns)
        sample=connector.sample_rows(tables[0],schema_name or spec.schema_name,limit=5); checks["sample_rows"]=isinstance(sample,list) and len(sample)<=5
    else:
        checks["describe_table"]=True; checks["sample_rows"]=True
    return checks
