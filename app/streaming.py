from __future__ import annotations

import csv
import io
import json
import random
from pathlib import Path
from typing import Any, Callable, Iterator

from faker import Faker

from .generator import _make_value, sql_literal, quote_identifier
from .models import ColumnSpec, GenerateRequest


def iter_rows(req:GenerateRequest,columns:list[ColumnSpec],start_index:int=0,count:int|None=None,cancelled:Callable[[],bool]|None=None)->Iterator[dict[str,Any]]:
    total=req.row_count-start_index if count is None else min(count,req.row_count-start_index)
    # Reconstruct deterministic RNG state by replaying; slower for non-zero offsets but guarantees stable batch boundaries.
    fake=Faker(req.locale); fake.seed_instance(req.seed); rng=random.Random(req.seed)
    for i in range(start_index):
        for col in columns:
            if col.nullable and not col.primary_key and rng.random() < col.null_rate:
                continue
            _make_value(fake,rng,col,i)
    for i in range(start_index,start_index+total):
        if cancelled and cancelled(): return
        row={}
        for col in columns:
            if col.nullable and not col.primary_key and rng.random()<col.null_rate: row[col.name]=None
            else: row[col.name]=_make_value(fake,rng,col,i)
        yield row


def iter_batches(req:GenerateRequest,columns:list[ColumnSpec],batch_size:int=5000,cancelled:Callable[[],bool]|None=None)->Iterator[list[dict[str,Any]]]:
    batch=[]
    for row in iter_rows(req,columns,cancelled=cancelled):
        batch.append(row)
        if len(batch)>=batch_size: yield batch; batch=[]
    if batch: yield batch


def stream_ndjson(req:GenerateRequest,columns:list[ColumnSpec],target:Path,batch_size:int=5000,progress:Callable[[int],None]|None=None,cancelled:Callable[[],bool]|None=None)->int:
    written=0
    with target.open("w",encoding="utf-8") as fh:
        for batch in iter_batches(req,columns,batch_size,cancelled):
            for row in batch: fh.write(json.dumps(row,default=str,separators=(",",":"))+"\n")
            written+=len(batch)
            if progress: progress(written)
    return written


def stream_csv(req:GenerateRequest,columns:list[ColumnSpec],target:Path,batch_size:int=5000,progress:Callable[[int],None]|None=None,cancelled:Callable[[],bool]|None=None)->int:
    written=0; names=[c.name for c in columns]
    with target.open("w",encoding="utf-8",newline="") as fh:
        writer=csv.DictWriter(fh,fieldnames=names); writer.writeheader()
        for batch in iter_batches(req,columns,batch_size,cancelled):
            for row in batch: writer.writerow({k:json.dumps(v) if isinstance(v,(dict,list)) else v for k,v in row.items()})
            written+=len(batch)
            if progress: progress(written)
    return written


def stream_sql(req:GenerateRequest,columns:list[ColumnSpec],target:Path,batch_size:int=1000,progress:Callable[[int],None]|None=None,cancelled:Callable[[],bool]|None=None)->int:
    d=req.database_type; qcols=", ".join(quote_identifier(c.name,d) for c in columns)
    target_name=f"`{req.database_name}.{req.schema_name}.{req.table_name}`" if d=="bigquery" else ".".join(quote_identifier(x,d) for x in [req.schema_name,req.table_name])
    written=0
    with target.open("w",encoding="utf-8") as fh:
        for batch in iter_batches(req,columns,batch_size,cancelled):
            for row in batch:
                vals=", ".join(sql_literal(row.get(c.name)) for c in columns); fh.write(f"INSERT INTO {target_name} ({qcols}) VALUES ({vals});\n")
            written+=len(batch)
            if progress: progress(written)
    return written
