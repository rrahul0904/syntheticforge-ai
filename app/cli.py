from __future__ import annotations

import argparse
import csv
import io
import json
import os
import shutil
import sys
import zipfile
from pathlib import Path
from typing import Any

try:
    import yaml
except Exception:  # optional convenience
    yaml = None

from .connectors import create_connector, connector_statuses
from .loaders import load_table
from .models import (
    AgentRunCreateRequest, ColumnSpec, ConnectorConfig, GeneratedTable, ParseSchemaRequest, ProjectCreateRequest,
    SystemGenerateRequest,
)
from .agent import AgentExecutor
from .persistence import Repository
from .privacy import classify_columns
from .profiling import profile_rows
from .schema_parser import parse_schema
from .system_generator import generate_system, system_to_zip

FORMATS=["ddl","json","openapi","avro","csv","description","sqlite-db"]
DBS=["postgresql","mysql","sqlserver","oracle","snowflake","bigquery","redshift","sqlite"]


def _load_structured(path:Path)->dict[str,Any]:
    text=path.read_text(encoding="utf-8")
    if path.suffix.lower() in {".yaml",".yml"}:
        if not yaml: raise RuntimeError("PyYAML is required for YAML recipes")
        return yaml.safe_load(text)
    return json.loads(text)


def _config_from_env(connector:str,env_name:str|None)->ConnectorConfig:
    raw=os.getenv(env_name or "SYNTHETICFORGE_CONNECTION")
    if not raw: raise RuntimeError(f"Connection JSON not found in environment variable {env_name or 'SYNTHETICFORGE_CONNECTION'}")
    data=json.loads(raw); data["connector"]=connector
    return ConnectorConfig.model_validate(data)


def _request_from_source(args)->SystemGenerateRequest:
    source=Path(args.input); content=str(source.resolve()) if args.input_format=="sqlite-db" else source.read_text(encoding="utf-8")
    parse_req=ParseSchemaRequest(database_type=args.database_type,database_name=args.database_name,schema_name=args.schema_name,input_format=args.input_format,content=content,table_name=source.stem.replace(".schema",""),default_row_count=args.default_row_count)
    tables,warnings=parse_schema(parse_req)
    for w in warnings: print(f"warning: {w}",file=sys.stderr)
    return SystemGenerateRequest(database_type=args.database_type,database_name=args.database_name,schema_name=args.schema_name,default_row_count=args.default_row_count,seed=args.seed,scenario=args.scenario,tables=tables,negative_testing=getattr(args,"negative_testing",False),edge_case_rate=getattr(args,"edge_case_rate",0.0))


def build_parser()->argparse.ArgumentParser:
    p=argparse.ArgumentParser(description="SyntheticForge AI local synthetic-data platform")
    sub=p.add_subparsers(dest="command")

    g=sub.add_parser("generate",help="Generate a system from a schema input or recipe")
    g.add_argument("--input"); g.add_argument("--recipe"); g.add_argument("--format",choices=FORMATS,default="ddl",dest="input_format")
    g.add_argument("--database-type",default="postgresql",choices=DBS); g.add_argument("--database-name",default="synthetic"); g.add_argument("--schema",default="public",dest="schema_name")
    g.add_argument("--rows",type=int,default=50,dest="default_row_count"); g.add_argument("--seed",type=int,default=42); g.add_argument("--scenario"); g.add_argument("--output",default="synthetic-system.zip")
    g.add_argument("--edge-case-rate",type=float,default=0.0); g.add_argument("--negative-testing",action="store_true")

    ins=sub.add_parser("inspect",help="Read-only database introspection")
    ins.add_argument("--connector",required=True,choices=DBS); ins.add_argument("--connection-env",default="SYNTHETICFORGE_CONNECTION"); ins.add_argument("--schema"); ins.add_argument("--output")

    prof=sub.add_parser("profile",help="Profile a CSV sample")
    prof.add_argument("--input",required=True); prof.add_argument("--limit",type=int,default=10_000); prof.add_argument("--output")

    val=sub.add_parser("validate",help="Show stored validation for a dataset")
    val.add_argument("--dataset",required=True); val.add_argument("--state-db")

    exp=sub.add_parser("export",help="Copy a persisted dataset artifact")
    exp.add_argument("--dataset",required=True); exp.add_argument("--output",required=True); exp.add_argument("--state-db")

    ld=sub.add_parser("load",help="Load a persisted dataset into a target database")
    ld.add_argument("--dataset",required=True); ld.add_argument("--target",required=True,choices=DBS); ld.add_argument("--connection-env",default="SYNTHETICFORGE_TARGET"); ld.add_argument("--state-db"); ld.add_argument("--dry-run",action="store_true"); ld.add_argument("--truncate",action="store_true"); ld.add_argument("--confirm-destructive",action="store_true")

    projs=sub.add_parser("projects",help="List or create local projects")
    projs.add_argument("--create"); projs.add_argument("--description"); projs.add_argument("--state-db")

    agent=sub.add_parser("agent",help="Run the constrained SyntheticForge agent")
    agent.add_argument("--goal",required=True); agent.add_argument("--project-id"); agent.add_argument("--database-type",default="postgresql",choices=DBS)
    agent.add_argument("--database-name",default="synthetic"); agent.add_argument("--schema",default="public",dest="schema_name"); agent.add_argument("--rows",type=int,default=50)
    agent.add_argument("--seed",type=int,default=42); agent.add_argument("--quality",type=float,default=95.0); agent.add_argument("--max-repairs",type=int,default=2); agent.add_argument("--state-db")

    con=sub.add_parser("connectors",help="Show connector/driver availability")
    return p


def _load_tables_from_archive(path:Path)->list[GeneratedTable]:
    tables=[]
    with zipfile.ZipFile(path) as zf:
        manifest=json.loads(zf.read("manifest.json")); meta={(t["schema"],t["table"]):t for t in manifest["tables"]}
        for (schema,name),m in meta.items():
            csv_name=f"tables/{schema}.{name}.csv"; rows=list(csv.DictReader(io.StringIO(zf.read(csv_name).decode()))
            cols=[ColumnSpec.model_validate(c) for c in m["columns"]]
            # CSV preserves transport strings; database drivers can coerce common types, and nulls are normalized.
            normalized=[]
            for r in rows: normalized.append({k:(None if v=="" else v) for k,v in r.items()})
            tables.append(GeneratedTable(name=name,schema_name=schema,columns=cols,foreign_keys=m.get("foreign_keys",[]),rows=normalized))
    return tables


def cmd_generate(args)->int:
    if args.recipe:
        req=SystemGenerateRequest.model_validate(_load_structured(Path(args.recipe)))
    elif args.input:
        req=_request_from_source(args)
    else: raise RuntimeError("generate requires --input or --recipe")
    result=generate_system(req); Path(args.output).write_bytes(system_to_zip(req,result)); total=sum(len(t.rows) for t in result.tables)
    print(f"Generated {total} rows across {len(result.tables)} tables -> {args.output}")
    print(f"Validation: {'PASS' if result.validation.passed else 'FAIL'}; quality={result.validation.quality_score:.2f}")
    return 0 if result.validation.passed or req.negative_testing else 2


def main()->int:
    # Backward compatibility: old CLI accepted a positional schema path.
    argv=sys.argv[1:]
    if argv and argv[0] not in {"generate","inspect","profile","validate","export","load","projects","agent","connectors","-h","--help"}:
        argv=["generate","--input",argv[0],*argv[1:]]
    args=build_parser().parse_args(argv)
    if not args.command: build_parser().print_help(); return 0
    try:
        if args.command=="generate": return cmd_generate(args)
        if args.command=="connectors":
            for s in connector_statuses(): print(f"{s.connector:12} {'available' if s.available else 'driver-missing':14} {s.driver or '-'}")
            return 0
        if args.command=="inspect":
            cfg=_config_from_env(args.connector,args.connection_env); db=create_connector(cfg)
            try: tables=db.introspect_system(args.schema)
            finally: db.close()
            payload={"tables":[t.model_dump(mode="json") for t in tables],"relationships":sum(len(t.foreign_keys) for t in tables)}
            text=json.dumps(payload,indent=2,default=str); Path(args.output).write_text(text) if args.output else print(text); return 0
        if args.command=="profile":
            rows=list(csv.DictReader(Path(args.input).open(encoding="utf-8")))[:args.limit]; cols=classify_columns([ColumnSpec(name=n) for n in (rows[0] if rows else [])],rows); result=profile_rows(rows,cols,args.limit).model_dump(mode="json")
            text=json.dumps(result,indent=2,default=str); Path(args.output).write_text(text) if args.output else print(text); return 0
        repository=Repository(args.state_db) if getattr(args,"state_db",None) else Repository()
        if args.command=="agent":
            executor=AgentExecutor(repository); req=AgentRunCreateRequest(goal=args.goal,project_id=args.project_id,database_type=args.database_type,database_name=args.database_name,schema_name=args.schema_name,default_row_count=args.rows,seed=args.seed,quality_threshold=args.quality,max_repairs=args.max_repairs)
            run=executor.create(req); run=executor.execute(run.id,req)
            print(json.dumps(run.model_dump(mode="json"),indent=2,default=str)); return 0 if run.state=="completed" else 2
        if args.command=="validate":
            d=repository.get_dataset(args.dataset); print(json.dumps(d.validation,indent=2)); return 0 if d.validation.get("passed",False) else 2
        if args.command=="export":
            d=repository.get_dataset(args.dataset)
            if not d.exports: raise RuntimeError("Dataset has no persisted export")
            shutil.copy2(d.exports[0],args.output); print(args.output); return 0
        if args.command=="load":
            d=repository.get_dataset(args.dataset)
            if not d.exports: raise RuntimeError("Dataset has no persisted export")
            cfg=_config_from_env(args.target,args.connection_env); tables=_load_tables_from_archive(Path(d.exports[0])); total=0
            for table in tables:
                result=load_table(cfg,table,dry_run=args.dry_run,mode="truncate" if args.truncate else "append",confirm_destructive=args.confirm_destructive); total+=int(result["rows"])
            print(f"{'Would load' if args.dry_run else 'Loaded'} {total} rows across {len(tables)} tables"); return 0
        if args.command=="projects":
            if args.create: print(json.dumps(repository.create_project(ProjectCreateRequest(name=args.create,description=args.description)),indent=2))
            else: print(json.dumps(repository.list_projects(),indent=2))
            return 0
    except Exception as exc:
        print(f"error: {exc}",file=sys.stderr); return 1
    return 0


if __name__=="__main__": raise SystemExit(main())
