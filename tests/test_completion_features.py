from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.connectors import create_connector
from app.jobs import JobManager
from app.loaders import load_table
from app.main import app
from app.models import (
    ColumnSpec, ColumnStatistics, ConnectorConfig, CorrelationSpec, GeneratedTable,
    ParseSchemaRequest, ProjectCreateRequest, SystemGenerateRequest, TableSpec,
)
from app.persistence import Repository
from app.privacy import classify_columns
from app.profiling import apply_profile_to_columns, profile_rows
from app.schema_parser import parse_schema
from app.system_generator import generate_system

client=TestClient(app)


def test_sqlite_check_domains_are_generation_domains(tmp_path:Path):
    path=tmp_path/'source.db'; c=sqlite3.connect(path)
    c.execute("CREATE TABLE jobs(id INTEGER PRIMARY KEY,status TEXT NOT NULL CHECK(status IN ('queued','running','done')),score REAL CHECK(score>=0))")
    c.commit(); c.close()
    db=create_connector(ConnectorConfig(connector='sqlite',path=str(path),read_only=True))
    try: spec=db.describe_table('jobs','main')
    finally: db.close()
    status=next(x for x in spec.columns if x.name=='status'); score=next(x for x in spec.columns if x.name=='score')
    assert status.choices==['queued','running','done']
    assert score.min_value==0
    result=generate_system(SystemGenerateRequest(database_type='sqlite',database_name='x',schema_name='main',tables=[spec],default_row_count=100))
    assert result.validation.passed
    assert {r['status'] for r in result.tables[0].rows}<={'queued','running','done'}


def test_profile_has_skew_prefix_suffix_and_temporal_patterns():
    rows=[{'score':x,'stamp':f'2026-09-0{1+x%5}T0{x%9}:15:00','code':f'ABC-{x:03d}-ZZ'} for x in [1,1,1,2,10,30]]
    cols=[ColumnSpec(name='score',data_type='integer'),ColumnSpec(name='stamp',data_type='timestamp'),ColumnSpec(name='code')]
    p=profile_rows(rows,cols)
    assert p.columns['score'].skewness is not None and p.columns['score'].skewness>0
    assert p.columns['stamp'].hour_histogram and p.columns['stamp'].weekday_histogram and p.columns['stamp'].month_histogram
    assert 'ABC' in p.columns['code'].prefixes and '-ZZ' in p.columns['code'].suffixes


def test_profiled_numeric_distribution_is_preserved():
    source=[{'amount':100+(i%7)*5} for i in range(200)]
    cols=[ColumnSpec(name='amount',data_type='decimal',semantic_type='money',nullable=False)]
    prof=profile_rows(source,cols); cols=apply_profile_to_columns(cols,prof)
    table=TableSpec(name='payments',row_count=1000,columns=cols)
    result=generate_system(SystemGenerateRequest(database_type='postgresql',database_name='x',tables=[table],default_row_count=1000,seed=91))
    check=next(c for c in result.validation.checks if c.name=='distribution_mean')
    assert check.passed, check.details


def test_generic_name_is_not_automatically_personal_but_named_person_fields_are():
    out=classify_columns([ColumnSpec(name='name'),ColumnSpec(name='full_name'),ColumnSpec(name='customer_name')])
    assert [c.sensitivity for c in out]==['non-sensitive','personal','personal']


def test_natural_language_hospitality_model_is_deep_and_valid():
    tables,_=parse_schema(ParseSchemaRequest(database_type='postgresql',database_name='hotel',schema_name='prod',input_format='description',content='Create a hospitality reservation platform with hotels, rooms, guests, reservations, payments, cancellations, loyalty accounts, invoices, refunds, rate plans and services.',default_row_count=20))
    names={t.name for t in tables}
    assert {'hotels','rooms','guests','reservations','payments','cancellations','loyalty_accounts','invoices','invoice_lines','refunds','rate_plans','services','service_bookings'}<=names
    assert len(tables)>=15 and sum(len(t.foreign_keys) for t in tables)>=12
    result=generate_system(SystemGenerateRequest(database_type='postgresql',database_name='hotel',schema_name='prod',tables=tables,default_row_count=20,scenario='12% cancellations'))
    assert result.validation.passed and result.validation.quality_score>=95


def test_composite_foreign_key_generation_and_validation():
    parent=TableSpec(name='parent',row_count=20,columns=[ColumnSpec(name='tenant_id',data_type='int',semantic_type='id'),ColumnSpec(name='item_id',data_type='int',semantic_type='id')],primary_key_columns=['tenant_id','item_id'])
    child=TableSpec(name='child',row_count=100,columns=[ColumnSpec(name='child_id',data_type='int',semantic_type='id',primary_key=True),ColumnSpec(name='tenant_id',data_type='int'),ColumnSpec(name='item_id',data_type='int')],foreign_keys=[{'column':'tenant_id','columns':['tenant_id','item_id'],'references_table':'parent','references_column':'tenant_id','references_columns':['tenant_id','item_id']}])
    result=generate_system(SystemGenerateRequest(database_type='postgresql',database_name='x',tables=[parent,child]))
    assert result.validation.passed
    pkeys={(r['tenant_id'],r['item_id']) for r in result.tables[0].rows}
    assert all((r['tenant_id'],r['item_id']) in pkeys for r in result.tables[1].rows)


def test_loader_requires_destructive_confirmation_and_validates_target_schema(tmp_path:Path):
    path=tmp_path/'target.db'; c=sqlite3.connect(path); c.execute('CREATE TABLE people(id INTEGER PRIMARY KEY,name TEXT)'); c.commit(); c.close()
    cfg=ConnectorConfig(connector='sqlite',path=str(path),read_only=False)
    table=GeneratedTable(name='people',schema_name='main',columns=[ColumnSpec(name='id',primary_key=True),ColumnSpec(name='name')],foreign_keys=[],rows=[{'id':1,'name':'A'}])
    with pytest.raises(ValueError): load_table(cfg,table,mode='truncate')
    assert load_table(cfg,table,mode='truncate',confirm_destructive=True)['rows']==1
    bad=GeneratedTable(name='people',schema_name='main',columns=[ColumnSpec(name='id'),ColumnSpec(name='missing')],foreign_keys=[],rows=[{'id':2,'missing':'x'}])
    with pytest.raises(Exception,match='missing columns'): load_table(cfg,bad,dry_run=True)


def test_job_lifecycle_persists_logs_and_failure_without_secrets(tmp_path:Path):
    repo=Repository(tmp_path/'state.db'); p=repo.create_project(ProjectCreateRequest(name='Jobs'))
    manager=JobManager(repo); job=manager.create(p['id'],{'password':'secret','database':'qa'})
    stored=repo.get_job(job.id); assert stored.payload['password']=='***'
    def work(update,cancelled): update(progress=60,step='validating',generated_rows=25,log='quality 100'); return 1
    manager.run(job.id,work); done=repo.get_job(job.id)
    assert done.state=='completed' and done.progress==100 and done.generated_rows==25 and done.logs
    failed=manager.create(p['id'])
    with pytest.raises(RuntimeError): manager.run(failed.id,lambda u,c: (_ for _ in ()).throw(RuntimeError('password=supersecret failed')))
    f=repo.get_job(failed.id); assert f.state=='failed' and 'supersecret' not in (f.error or '') and '***' in (f.error or '')


def test_connector_error_redacts_configured_secret(tmp_path:Path):
    # A configured password is scrubbed from any connector error before it leaves the adapter.
    cfg=ConnectorConfig(connector='postgresql',host='no-such-host.invalid',database='x',username='u',password='dont-show-me')
    db=create_connector(cfg)
    msg=db.safe_error(RuntimeError('login dont-show-me for u failed'))
    assert 'dont-show-me' not in msg and msg.count('***')>=1


def test_api_error_envelope_for_oversized_schema(monkeypatch):
    import app.main as mainmod
    monkeypatch.setattr(mainmod,'max_upload_bytes',lambda:16)
    payload={'database_type':'postgresql','database_name':'x','schema_name':'public','input_format':'ddl','content':'CREATE TABLE t (id INT);'}
    r=client.post('/api/parse-schema',json=payload)
    assert r.status_code==413 and 'size limit' in r.json()['detail']
