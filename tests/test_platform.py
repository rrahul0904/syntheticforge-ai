from __future__ import annotations

import json
import sqlite3
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

from app.connectors import connector_statuses, create_connector
from app.connectors.sdk import connector_contract
from app.intelligence import apply_correlations
from app.loaders import load_table
from app.main import app
from app.models import (
    ColumnSpec, ConnectorConfig, CorrelationSpec, GenerateRequest, GeneratedTable,
    ProjectCreateRequest, RecipeSpec, SystemGenerateRequest, TableSpec,
)
from app.persistence import Repository
from app.privacy import classify_columns, deterministic_pseudonym
from app.profiling import apply_profile_to_columns, profile_rows
from app.streaming import iter_rows, stream_csv
from app.system_generator import generate_system, system_to_zip

client=TestClient(app)


def test_connector_registry_has_all_enterprise_targets():
    names={s.connector for s in connector_statuses()}
    assert {"sqlite","postgresql","mysql","sqlserver","oracle","snowflake","bigquery","redshift"} <= names


def test_sqlite_connector_contract_and_profile(tmp_path):
    db=tmp_path/"source.db"; c=sqlite3.connect(db)
    c.executescript("""
      PRAGMA foreign_keys=ON;
      CREATE TABLE parents(parent_id INTEGER PRIMARY KEY, category TEXT NOT NULL);
      CREATE TABLE children(child_id INTEGER PRIMARY KEY, parent_id INTEGER NOT NULL, amount REAL, FOREIGN KEY(parent_id) REFERENCES parents(parent_id));
      INSERT INTO parents VALUES(1,'A'),(2,'B');
      INSERT INTO children VALUES(1,1,10.5),(2,1,12.0),(3,2,50.0);
    """); c.close()
    con=create_connector(ConnectorConfig(connector="sqlite",path=str(db),read_only=True))
    try:
        checks=connector_contract(con,"main")
        assert all(checks.values()), checks
        spec=con.describe_table("children","main")
        assert spec.foreign_keys[0].references_table=="parents"
        prof=con.profile_table("children","main",100)
        assert prof.row_count==3 and prof.columns["amount"].mean > 20
    finally: con.close()


def test_sqlite_read_only_source_rejects_writes(tmp_path):
    db=tmp_path/"source.db"; sqlite3.connect(db).execute("CREATE TABLE t(id INTEGER)").connection.commit()
    con=create_connector(ConnectorConfig(connector="sqlite",path=str(db),read_only=True)).connect()
    try:
        try: con._connection.execute("INSERT INTO t VALUES(1)")
        except sqlite3.OperationalError as exc: assert "readonly" in str(exc).lower()
        else: raise AssertionError("read-only connector accepted a write")
    finally: con.close()


def test_direct_sqlite_load_and_dry_run(tmp_path):
    db=tmp_path/"target.db"; c=sqlite3.connect(db); c.execute("CREATE TABLE people(person_id INTEGER PRIMARY KEY, name TEXT NOT NULL)"); c.commit(); c.close()
    table=GeneratedTable(name="people",schema_name="main",columns=[ColumnSpec(name="person_id",data_type="integer",primary_key=True),ColumnSpec(name="name",nullable=False)],foreign_keys=[],rows=[{"person_id":1,"name":"Ada"},{"person_id":2,"name":"Lin"}])
    cfg=ConnectorConfig(connector="sqlite",path=str(db),read_only=False)
    dry=load_table(cfg,table,dry_run=True); assert dry["rows"]==2
    loaded=load_table(cfg,table,dry_run=False); assert loaded["rows"]==2
    assert sqlite3.connect(db).execute("SELECT COUNT(*) FROM people").fetchone()[0]==2


def test_profile_numeric_categorical_patterns_and_correlation():
    rows=[{"x":i,"y":2*i,"status":"A" if i<8 else "B","email":f"u{i}@example.com"} for i in range(10)]
    cols=classify_columns([ColumnSpec(name="x",data_type="integer"),ColumnSpec(name="y",data_type="integer"),ColumnSpec(name="status"),ColumnSpec(name="email")],rows)
    p=profile_rows(rows,cols)
    assert p.columns["x"].quantiles["p50"]==4.5
    assert "email" in p.columns["email"].patterns
    corr=next(c for c in p.correlations if c.kind=="pearson")
    assert corr.coefficient > .99
    suggested=apply_profile_to_columns(cols,p)
    assert next(c for c in suggested if c.name=="email").choices is None  # PII values are never reused as choices


def test_privacy_classification_and_deterministic_pseudonym():
    cols=classify_columns([ColumnSpec(name="ssn"),ColumnSpec(name="api_key"),ColumnSpec(name="email")])
    classes={c.name:c.sensitivity for c in cols}
    assert classes=={"ssn":"sensitive-pii","api_key":"secret","email":"personal"}
    assert deterministic_pseudonym("abc")==deterministic_pseudonym("abc")
    assert deterministic_pseudonym("abc")!=deterministic_pseudonym("def")


def test_profiled_pearson_generation_preserves_correlation():
    table=TableSpec(name="rates",columns=[
        ColumnSpec(name="x",data_type="decimal",semantic_type="decimal",nullable=False),
        ColumnSpec(name="y",data_type="decimal",semantic_type="decimal",nullable=False),
    ],correlations=[CorrelationSpec(columns=["x","y"],kind="pearson",coefficient=.9,tolerance=.2)])
    # Attach source moments used by the correlation generator.
    source=[{"x":i,"y":2*i+1} for i in range(1,101)]; p=profile_rows(source,table.columns)
    table.columns=apply_profile_to_columns(table.columns,p)
    rows=[{"x":float(i),"y":0.0} for i in range(1,101)]
    apply_correlations(table,rows,42)
    out=profile_rows(rows,table.columns)
    r=next(c.coefficient for c in out.correlations if c.kind=="pearson")
    assert r > .75


def test_streaming_is_deterministic_and_bounded(tmp_path):
    req=GenerateRequest(database_type="postgresql",database_name="x",table_name="events",row_count=5000,seed=9,columns=[])
    cols=[ColumnSpec(name="id",data_type="bigint",semantic_type="id",primary_key=True),ColumnSpec(name="status",choices=["a","b"],nullable=False)]
    first=list(iter_rows(req,cols,count=10)); again=list(iter_rows(req,cols,count=10)); assert first==again
    out=tmp_path/"rows.csv"; assert stream_csv(req,cols,out,batch_size=127)==5000
    assert out.read_text().count("\n")==5001


def test_negative_testing_is_explicit():
    parent=TableSpec(name="parents",row_count=10,columns=[ColumnSpec(name="parent_id",data_type="bigint",semantic_type="id",primary_key=True)])
    child=TableSpec(name="children",row_count=20,columns=[ColumnSpec(name="child_id",data_type="bigint",semantic_type="id",primary_key=True),ColumnSpec(name="parent_id",data_type="bigint",semantic_type="foreign_id",nullable=False)],foreign_keys=[{"column":"parent_id","references_table":"parents","references_column":"parent_id"}])
    normal=generate_system(SystemGenerateRequest(database_type="postgresql",database_name="x",tables=[parent,child],edge_case_rate=.2,negative_testing=False))
    assert normal.validation.passed
    negative=generate_system(SystemGenerateRequest(database_type="postgresql",database_name="x",tables=[parent,child],edge_case_rate=.2,negative_testing=True))
    assert not negative.validation.passed


def test_repository_projects_recipes_dataset_versions_and_redaction(tmp_path):
    r=Repository(tmp_path/"state.db")
    p=r.create_project(ProjectCreateRequest(name="Hotel",settings={"password":"do-not-store","theme":"dark"}))
    assert p["settings"]["password"]=="***"
    rec=r.save_recipe(RecipeSpec(project_id=p["id"],name="QA",seed=7,settings={"api_key":"hidden"}))
    assert r.list_recipes(p["id"])[0].settings["api_key"]=="***"
    d1=r.create_dataset(p["id"],7,{"tables":1},{"rules":1},{"t":10},{"passed":True})
    d2=r.create_dataset(p["id"],8,{"tables":2},{"rules":1},{"t":12},{"passed":True},parent_version=1)
    cmp=r.compare_datasets(d1.id,d2.id)
    assert cmp["schema_changed"] and cmp["row_count_changes"]["t"]["delta"]==2


def test_system_zip_contains_quality_artifacts():
    t=TableSpec(name="users",row_count=3,columns=[ColumnSpec(name="user_id",data_type="bigint",semantic_type="id",primary_key=True),ColumnSpec(name="email",semantic_type="email",nullable=False)])
    req=SystemGenerateRequest(database_type="postgresql",database_name="x",tables=[t]); result=generate_system(req)
    data=system_to_zip(req,result)
    with zipfile.ZipFile(__import__("io").BytesIO(data)) as z:
        names=set(z.namelist())
        assert {"manifest.json","validation.json","relationships.json","profile.json","all_inserts.sql"} <= names
        assert any(n.endswith(".jsonl") for n in names)


def test_new_api_health_connectors_profile_and_projects(tmp_path, monkeypatch):
    import app.main as mainmod
    mainmod._repo=Repository(tmp_path/"api.db")
    assert client.get("/api/readiness").status_code==200
    assert client.get("/api/connectors").status_code==200
    pr=client.post("/api/profile",json={"columns":[{"name":"score","data_type":"integer"}],"rows":[{"score":1},{"score":2},{"score":3}]})
    assert pr.status_code==200 and pr.json()["profile"]["columns"]["score"]["mean"]==2
    project=client.post("/api/projects",json={"name":"API Test","settings":{"password":"secret"}})
    assert project.status_code==200 and project.json()["settings"]["password"]=="***"
    assert client.get("/api/metrics").status_code==200


def test_project_generation_persists_versioned_zip(tmp_path):
    import app.main as mainmod
    mainmod._repo=Repository(tmp_path/"api2.db")
    p=client.post("/api/projects",json={"name":"Persisted"}).json()
    request={"database_type":"postgresql","database_name":"x","tables":[{"name":"things","row_count":5,"columns":[{"name":"thing_id","data_type":"bigint","semantic_type":"id","primary_key":True}]}]}
    r=client.post(f"/api/projects/{p['id']}/generate",json={"request":request})
    assert r.status_code==200,r.text
    d=r.json()["dataset"]; assert d["version"]==1 and d["exports"] and Path(d["exports"][0]).is_file()
