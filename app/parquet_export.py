from __future__ import annotations

import io
import importlib.util
from pathlib import Path
from typing import Any, Iterable


def parquet_available()->bool:
    return importlib.util.find_spec("pyarrow") is not None


def rows_to_parquet(rows:list[dict[str,Any]])->bytes:
    if not parquet_available():
        raise RuntimeError("Parquet support requires the optional 'parquet' dependency: pip install 'syntheticforge-ai[parquet]'")
    import pyarrow as pa
    import pyarrow.parquet as pq
    table=pa.Table.from_pylist(rows)
    out=io.BytesIO(); pq.write_table(table,out,compression="snappy"); return out.getvalue()


def stream_parquet(batches:Iterable[list[dict[str,Any]]],target:Path)->int:
    if not parquet_available():
        raise RuntimeError("Parquet support requires the optional 'parquet' dependency: pip install 'syntheticforge-ai[parquet]'")
    import pyarrow as pa
    import pyarrow.parquet as pq
    writer=None; total=0
    try:
        for batch in batches:
            if not batch: continue
            table=pa.Table.from_pylist(batch)
            if writer is None: writer=pq.ParquetWriter(target,table.schema,compression="snappy")
            writer.write_table(table); total+=len(batch)
    finally:
        if writer is not None: writer.close()
    return total
