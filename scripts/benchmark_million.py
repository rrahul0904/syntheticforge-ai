from __future__ import annotations
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
import json, resource, tempfile, time
from app.models import ColumnSpec, GenerateRequest
from app.streaming import stream_ndjson

req=GenerateRequest(database_type="postgresql",database_name="bench",schema_name="public",table_name="events",row_count=1_000_000,seed=2026,ai_inference=False,columns=[])
cols=[
    ColumnSpec(name="event_id",data_type="bigint",semantic_type="id",primary_key=True),
    ColumnSpec(name="event_type",data_type="varchar",semantic_type="choice",choices=["created","updated","processed","completed"],nullable=False),
    ColumnSpec(name="score",data_type="integer",semantic_type="integer",min_value=1,max_value=100,nullable=False),
]
with tempfile.TemporaryDirectory() as td:
    out=Path(td)/"million.jsonl"; start=time.perf_counter(); count=stream_ndjson(req,cols,out,batch_size=10_000); elapsed=time.perf_counter()-start
    rss=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    rss_mb=rss/1024 if rss>10_000 else rss/(1024*1024)
    report={"rows":count,"elapsed_seconds":round(elapsed,3),"rows_per_second":round(count/elapsed,1),"peak_rss_mb":round(rss_mb,2),"output_bytes":out.stat().st_size,"batch_size":10_000}
    print(json.dumps(report,indent=2))
