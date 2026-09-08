from app.models import ParseSchemaRequest
from app.schema_parser import parse_schema
import json


def test_ddl_composite_constraints_alter_defaults_identity():
    ddl='''
    CREATE TABLE sales.parent (a INT NOT NULL, b INT NOT NULL, code VARCHAR(8) DEFAULT 'X', CONSTRAINT pk_parent PRIMARY KEY (a,b), CONSTRAINT uq_code UNIQUE(code), CHECK (a >= 0));
    CREATE TABLE sales.child (id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, a INT NOT NULL, b INT NOT NULL);
    ALTER TABLE sales.child ADD CONSTRAINT fk_ab FOREIGN KEY (a,b) REFERENCES sales.parent(a,b);
    '''
    tables,_=parse_schema(ParseSchemaRequest(database_type="postgresql",database_name="x",schema_name="sales",input_format="ddl",content=ddl))
    parent=next(t for t in tables if t.name=="parent"); child=next(t for t in tables if t.name=="child")
    assert parent.primary_key_columns==["a","b"]
    assert parent.unique_constraints[0].columns==["code"]
    assert parent.check_constraints
    assert next(c for c in parent.columns if c.name=="code").default=="'X'"
    assert next(c for c in child.columns if c.name=="id").identity
    fk=child.foreign_keys[0]; assert fk.columns==["a","b"] and fk.references_columns==["a","b"]


def test_json_schema_nested_ref_and_array_become_relations():
    doc={"title":"Order","type":"object","required":["order_id","customer"],"$defs":{"Customer":{"type":"object","required":["customer_id"],"properties":{"customer_id":{"type":"integer"},"email":{"type":["string","null"],"format":"email"}}}},"properties":{"order_id":{"type":"integer","primary_key":True},"customer":{"$ref":"#/$defs/Customer"},"items":{"type":"array","items":{"type":"object","properties":{"sku":{"type":"string"},"quantity":{"type":"integer","minimum":1}}}}}}
    tables,_=parse_schema(ParseSchemaRequest(database_type="postgresql",database_name="x",input_format="json",content=json.dumps(doc)))
    names={t.name for t in tables}; assert {"order","customer","items"} <= names
    order=next(t for t in tables if t.name=="order"); assert any(f.references_table=="customer" for f in order.foreign_keys)
    items=next(t for t in tables if t.name=="items"); assert any(f.references_table=="order" for f in items.foreign_keys)


def test_openapi_request_body_inline_schema_is_ingested():
    spec={"openapi":"3.0.3","paths":{"/widgets":{"post":{"requestBody":{"content":{"application/json":{"schema":{"type":"object","properties":{"widget_id":{"type":"integer"},"name":{"type":"string"}}}}}},"responses":{"200":{"description":"ok"}}}}},"components":{"schemas":{"Widget":{"type":"object","properties":{"widget_id":{"type":"integer"}}}}}}
    tables,_=parse_schema(ParseSchemaRequest(database_type="postgresql",database_name="x",input_format="openapi",content=json.dumps(spec)))
    assert any("request" in t.name for t in tables)


def test_avro_nested_record_array_and_logical_types():
    doc={"type":"record","name":"Order","fields":[{"name":"order_id","type":"long"},{"name":"created_on","type":{"type":"int","logicalType":"date"}},{"name":"lines","type":{"type":"array","items":{"type":"record","name":"Line","fields":[{"name":"line_id","type":"long"},{"name":"amount","type":{"type":"bytes","logicalType":"decimal","precision":12,"scale":2}}]}}}]}
    tables,_=parse_schema(ParseSchemaRequest(database_type="snowflake",database_name="x",input_format="avro",content=json.dumps(doc)))
    assert {t.name for t in tables}=={"order","line"}
    line=next(t for t in tables if t.name=="line"); assert any(f.references_table=="order" for f in line.foreign_keys)
    assert next(c for c in line.columns if c.name=="amount").data_type=="decimal(12,2)"
