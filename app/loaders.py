from __future__ import annotations

import time
from contextlib import suppress
from typing import Any

from .connectors import create_connector
from .connectors.base import ConnectorError, validate_identifier
from .models import ConnectorConfig, GeneratedTable


def _placeholder(connector:str)->str:
    return "?" if connector in {"sqlite","sqlserver"} else ":1" if connector=="oracle" else "%s"


def _quote(name:str,connector:str)->str:
    validate_identifier(name)
    if connector=="mysql": return f"`{name}`"
    if connector=="sqlserver": return f"[{name}]"
    return f'"{name}"'


def _schema_compatible(config:ConnectorConfig,table:GeneratedTable,db:Any)->None:
    """Fail before writing when a target table is missing required generated columns."""
    try:
        target=db.describe_table(table.name,table.schema_name)
    except Exception as exc:
        raise ConnectorError(f"Target schema validation failed for {table.schema_name}.{table.name}: {db.safe_error(exc)}") from exc
    available={c.name.lower() for c in target.columns}
    missing=[c.name for c in table.columns if c.name.lower() not in available]
    if missing:
        raise ConnectorError(f"Target table {table.schema_name}.{table.name} is missing columns: {', '.join(missing)}")


def _load_bigquery(config:ConnectorConfig,table:GeneratedTable,batch_size:int,dry_run:bool,mode:str,confirm_destructive:bool)->dict[str,Any]:
    if mode=="truncate" and not confirm_destructive: raise ValueError("truncate mode requires explicit confirm_destructive=true")
    db=create_connector(config.model_copy(update={"read_only":False}))
    try:
        client=db.connect()._connection
        project=config.project or config.database or getattr(client,"project",None)
        if not project: raise ConnectorError("BigQuery target requires project or database")
        dataset=validate_identifier(table.schema_name); name=validate_identifier(table.name); target=f"{project}.{dataset}.{name}"
        obj=client.get_table(target)
        available={f.name.lower() for f in obj.schema}
        missing=[c.name for c in table.columns if "." not in c.name and c.name.lower() not in available]
        if missing: raise ConnectorError(f"Target table {target} is missing columns: {', '.join(missing)}")
        if dry_run:return {"ok":True,"dry_run":True,"rows":len(table.rows),"target":target,"connector":"bigquery"}
        try:
            from google.cloud import bigquery as bq
            job_config=bq.LoadJobConfig(write_disposition=bq.WriteDisposition.WRITE_TRUNCATE if mode=="truncate" else bq.WriteDisposition.WRITE_APPEND)
        except Exception:
            job_config=None
        loaded=0; first=True
        for i in range(0,len(table.rows),batch_size):
            rows=table.rows[i:i+batch_size]
            config_for_batch=job_config
            if job_config is not None and mode=="truncate" and not first:
                from google.cloud import bigquery as bq
                config_for_batch=bq.LoadJobConfig(write_disposition=bq.WriteDisposition.WRITE_APPEND)
            client.load_table_from_json(rows,target,job_config=config_for_batch).result(); loaded+=len(rows); first=False
        return {"ok":True,"rows":loaded,"target":target,"connector":"bigquery"}
    finally: db.close()


def load_table(config:ConnectorConfig,table:GeneratedTable,batch_size:int=1000,dry_run:bool=False,mode:str="append",confirm_destructive:bool=False)->dict[str,Any]:
    """Load one generated table into an explicit TARGET connection.

    Source connectors are read-only by default; callers must pass a separate target config.
    Target schema is checked before writes, destructive replacement requires explicit confirmation,
    and transaction-capable drivers roll back on failure.
    """
    if mode not in {"append","truncate"}: raise ValueError("mode must be append or truncate")
    if batch_size<1 or batch_size>100_000: raise ValueError("batch_size must be between 1 and 100000")
    if config.connector=="bigquery": return _load_bigquery(config,table,batch_size,dry_run,mode,confirm_destructive)
    if config.read_only: config=config.model_copy(update={"read_only":False})
    if mode=="truncate" and not confirm_destructive: raise ValueError("truncate mode requires explicit confirm_destructive=true")
    schema=table.schema_name; name=table.name; connector=config.connector
    cols=[c.name for c in table.columns]
    target=_quote(name,connector) if connector=="sqlite" else f"{_quote(schema,connector)}.{_quote(name,connector)}"
    ph=_placeholder(connector)
    placeholders=", ".join([ph]*len(cols)) if connector!="oracle" else ", ".join(f":{i+1}" for i in range(len(cols)))
    insert=f"INSERT INTO {target} ({', '.join(_quote(c,connector) for c in cols)}) VALUES ({placeholders})"
    db=create_connector(config); db.connect(); conn=db._connection
    _schema_compatible(config,table,db)
    if dry_run:
        db.close(); return {"ok":True,"dry_run":True,"rows":len(table.rows),"statement":insert,"target":f"{schema}.{name}","connector":connector}
    cur=conn.cursor(); loaded=0; retries=max(0,min(int(config.extra.get("load_retries",2)),5))
    try:
        if mode=="truncate": cur.execute(f"DELETE FROM {target}" if connector=="sqlite" else f"TRUNCATE TABLE {target}")
        for i in range(0,len(table.rows),batch_size):
            batch=table.rows[i:i+batch_size]; values=[tuple(r.get(c) for c in cols) for r in batch]
            attempt=0
            while True:
                try:
                    cur.executemany(insert,values); loaded+=len(batch); break
                except Exception:
                    if attempt>=retries: raise
                    attempt+=1; time.sleep(min(0.25*(2**(attempt-1)),1.0))
        if hasattr(conn,"commit"):conn.commit()
        return {"ok":True,"rows":loaded,"target":f"{schema}.{name}","connector":connector}
    except Exception:
        if hasattr(conn,"rollback"):
            with suppress(Exception):conn.rollback()
        raise
    finally:
        with suppress(Exception):cur.close()
        db.close()
