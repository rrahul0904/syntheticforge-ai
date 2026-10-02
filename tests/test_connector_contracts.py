"""Deterministic adapter contract tests; none of these tests connect to a live service."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.connectors import create_connector
from app.connectors.base import ConnectorError
from app.loaders import _load_bigquery
from app.models import ColumnSpec, ConnectorConfig, GeneratedTable


def config(connector: str, **fields) -> ConnectorConfig:
    defaults = {"database": "warehouse", "schema_name": "qa", "username": "test-user", "read_only": True}
    defaults.update(fields)
    return ConnectorConfig(connector=connector, **defaults)


def query_stubs(monkeypatch, adapter, query, query_dicts=None):
    calls = []

    def record(sql, params=None):
        calls.append(("query", sql, tuple(params or ())))
        return query(sql, tuple(params or ()))

    def record_dicts(sql, params=None):
        calls.append(("query_dicts", sql, tuple(params or ())))
        if query_dicts is not None:
            return query_dicts(sql, tuple(params or ()))
        return query(sql, tuple(params or ()))

    monkeypatch.setattr(adapter, "_query", record)
    monkeypatch.setattr(adapter, "_query_dicts", record_dicts)
    return calls


def test_sqlserver_information_schema_contract(monkeypatch):
    adapter = create_connector(config("sqlserver", schema_name="dbo"))

    def query(sql, params):
        if "information_schema.schemata" in sql:
            return [("dbo",)]
        if "information_schema.tables" in sql:
            return [("orders",)]
        if "constraint_type='PRIMARY KEY'" in sql:
            return [("order_id",)]
        if "information_schema.key_column_usage" in sql and "foreign_table_name" in sql:
            return [("fk_orders_customer", "customer_id", "customers", "customer_id")]
        return []

    def dicts(sql, params):
        if "information_schema.columns" in sql:
            return [
                {"column_name": "order_id", "data_type": "int", "is_nullable": "NO", "column_default": None,
                 "character_maximum_length": None, "numeric_precision": 10, "numeric_scale": 0},
                {"column_name": "customer_id", "data_type": "int", "is_nullable": "NO", "column_default": None,
                 "character_maximum_length": None, "numeric_precision": 10, "numeric_scale": 0},
            ]
        if "SELECT TOP" in sql:
            return [{"order_id": 1, "customer_id": 8}]
        return []

    calls = query_stubs(monkeypatch, adapter, query, dicts)
    assert adapter.quote("Order") == "[Order]"
    assert adapter.list_schemas() == ["dbo"]
    assert adapter.list_tables("dbo") == ["orders"]
    table = adapter.describe_table("orders", "dbo")
    assert table.primary_key_columns == ["order_id"]
    assert table.columns[0].primary_key and not table.columns[0].nullable
    assert table.foreign_keys[0].references_table == "customers"
    assert table.foreign_keys[0].columns == ["customer_id"]
    assert adapter.sample_rows("orders", "dbo", 5) == [{"order_id": 1, "customer_id": 8}]
    assert any("?" in sql and params == ("dbo",) for _, sql, params in calls if "information_schema.tables" in sql)
    assert any("SELECT TOP 5 * FROM [dbo].[orders]" in sql for _, sql, _ in calls)


def test_oracle_catalog_contract_and_read_only_transaction(monkeypatch):
    adapter = create_connector(config("oracle", schema_name="APP", username="app"))

    def query(sql, params):
        if "SELECT username FROM all_users" in sql:
            return [("APP",), ("SYSTEM",)]
        if "SELECT table_name FROM all_tables" in sql:
            return [("ORDERS",)]
        if "FROM all_tab_columns" in sql:
            return [("ORDER_ID", "NUMBER", "N", None, 22, 10, 0), ("ACCOUNT_ID", "NUMBER", "Y", "42", 22, 10, 0)]
        if "cons.constraint_type='P'" in sql:
            return [("ORDER_ID",)]
        if "c.constraint_type='R'" in sql:
            return [("FK_ORDERS_ACCOUNT", "ACCOUNT_ID", "ACCOUNTS", "ACCOUNT_ID")]
        if "c.constraint_type='U'" in sql:
            return [("UQ_ORDERS_ACCOUNT", "ACCOUNT_ID")]
        if "constraint_type='C'" in sql:
            return [("CK_ORDERS_ID", "ORDER_ID > 0")]
        if "FETCH FIRST" in sql:
            return [{"ORDER_ID": 3}]
        return []

    calls = query_stubs(monkeypatch, adapter, query)
    assert adapter.list_schemas() == ["APP", "SYSTEM"]
    assert adapter.list_tables("app") == ["ORDERS"]
    table = adapter.describe_table("orders", "app")
    assert table.name == "ORDERS" and table.schema_name == "APP"
    assert table.columns[0].primary_key and not table.columns[0].nullable
    assert table.columns[1].default == "42"
    assert table.foreign_keys[0].references_columns == ["ACCOUNT_ID"]
    assert table.unique_constraints[0].columns == ["ACCOUNT_ID"]
    assert table.check_constraints[0].expression == "ORDER_ID > 0"
    assert adapter.sample_rows("orders", "app", 7) == [{"ORDER_ID": 3}]
    assert any(":1" in sql and params == ("APP",) for _, sql, params in calls if "all_tables" in sql)
    assert any('FETCH FIRST 7 ROWS ONLY' in sql for _, sql, _ in calls)

    class Cursor:
        def __init__(self):
            self.statements = []

        def execute(self, sql):
            self.statements.append(sql)

        def close(self):
            pass

    class Connection:
        def __init__(self):
            self.cur = Cursor()

        def cursor(self):
            return self.cur

    connection = Connection()
    adapter._connection = connection
    adapter._set_read_only_if_supported()
    assert connection.cur.statements == ["SET TRANSACTION READ ONLY"]


def test_snowflake_information_schema_contract(monkeypatch):
    adapter = create_connector(config("snowflake", schema_name="PUBLIC"))

    def query(sql, params):
        if "information_schema.tables" in sql:
            return [("ORDERS",)]
        if "information_schema.columns" in sql:
            assert params == ("PUBLIC", "ORDERS")
            return [("ORDER_ID", "NUMBER", "NO", None, None, 38, 0), ("CUSTOMER_ID", "NUMBER", "YES", None, None, 38, 0)]
        if "constraint_type='PRIMARY KEY'" in sql:
            return [("PK_ORDERS", "ORDER_ID", 1)]
        if "constraint_type='FOREIGN KEY'" in sql:
            return [("FK_ORDERS_CUSTOMER", "CUSTOMER_ID", "UQ_CUSTOMERS", "CUSTOMERS", "ID", 1)]
        if "constraint_type='UNIQUE'" in sql:
            return [("UQ_ORDERS", "ORDER_ID", 1)]
        if "check_constraints" in sql:
            return [("CK_ORDERS", "ORDER_ID > 0")]
        if 'SELECT * FROM "public"."orders" LIMIT 6' in sql:
            return [{"ORDER_ID": 1, "CUSTOMER_ID": 9}]
        return []

    calls = query_stubs(monkeypatch, adapter, query)
    assert adapter.list_tables("public") == ["ORDERS"]
    table = adapter.describe_table("orders", "public")
    assert table.primary_key_columns == ["ORDER_ID"]
    assert table.columns[0].primary_key and not table.columns[0].nullable
    assert table.foreign_keys[0].columns == ["CUSTOMER_ID"]
    assert table.foreign_keys[0].references_table == "CUSTOMERS"
    assert table.unique_constraints[0].columns == ["ORDER_ID"]
    assert table.check_constraints[0].expression == "ORDER_ID > 0"
    assert adapter.sample_rows("orders", "public", 6) == [{"ORDER_ID": 1, "CUSTOMER_ID": 9}]
    assert any("%s" in sql and params == ("PUBLIC",) for _, sql, params in calls if "information_schema.tables" in sql)


class FakeBigQueryRow(dict):
    def items(self):
        return super().items()


def test_bigquery_sdk_contract_including_nested_fields(monkeypatch):
    adapter = create_connector(config("bigquery", project="analytics-project", database=None, schema_name="qa"))
    city = SimpleNamespace(name="city", field_type="STRING", mode="NULLABLE", description=None, fields=[])
    address = SimpleNamespace(name="address", field_type="RECORD", mode="REQUIRED", description="mailing address", fields=[city])
    row_id = SimpleNamespace(name="id", field_type="INT64", mode="REQUIRED", description="stable id", fields=[])
    table_obj = SimpleNamespace(schema=[row_id, address], num_rows=41)

    class Client:
        project = "analytics-project"

        def list_datasets(self, max_results=None):
            return [SimpleNamespace(dataset_id="qa")]

        def list_tables(self, dataset):
            assert dataset == "qa"
            return [SimpleNamespace(table_id="events")]

        def get_table(self, table_ref):
            assert table_ref == "analytics-project.qa.events"
            return table_obj

        def query(self, sql):
            assert sql == "SELECT * FROM `analytics-project.qa.events` LIMIT 4"
            return SimpleNamespace(result=lambda: [FakeBigQueryRow(id=1, address={"city": "X"})])

    monkeypatch.setattr(adapter, "_connection", Client())
    assert adapter.list_databases() == ["analytics-project"]
    assert adapter.test_connection() == {"ok": True, "connector": "bigquery", "result": 1}
    assert adapter.list_schemas() == ["qa"]
    assert adapter.list_tables("qa") == ["events"]
    table = adapter.describe_table("events", "qa")
    assert table.row_count == 41
    assert [column.name for column in table.columns] == ["id", "address", "address.city"]
    assert table.columns[0].primary_key is False
    assert not table.columns[0].nullable
    assert table.columns[1].description == "mailing address"
    assert table.columns[2].data_type == "STRING"
    assert table.columns[2].nullable
    assert adapter.sample_rows("events", "qa", 4) == [{"id": 1, "address": {"city": "X"}}]


def test_bigquery_production_read_only_policy_remains_fail_closed(monkeypatch):
    adapter = create_connector(config("bigquery", project="analytics-project"))
    called = False

    def should_not_connect():
        nonlocal called
        called = True
        raise AssertionError("unverified production source connection was attempted")

    monkeypatch.setenv("SYNTHETICFORGE_ENV", "production")
    monkeypatch.setattr(adapter, "_connect_impl", should_not_connect)
    with pytest.raises(ConnectorError, match="read-only enforcement is unverified"):
        adapter.connect()
    assert not called


def test_bigquery_nested_target_dry_run_and_write_fail_closed(monkeypatch):
    nested = SimpleNamespace(
        name="address", field_type="RECORD", mode="NULLABLE", fields=[
            SimpleNamespace(name="city", field_type="STRING", mode="NULLABLE", fields=[]),
        ],
    )
    target = SimpleNamespace(schema=[SimpleNamespace(name="id", field_type="INT64", fields=[]), nested])

    class Client:
        writes = 0

        def get_table(self, table_ref):
            assert table_ref == "analytics-project.qa.events"
            return target

        def load_table_from_json(self, *_args, **_kwargs):
            self.writes += 1
            return SimpleNamespace(result=lambda: None)

    class Adapter:
        def __init__(self):
            self._connection = Client()

        def connect(self):
            return self

        def close(self):
            pass

    adapter = Adapter()
    monkeypatch.setattr("app.loaders.create_connector", lambda _config: adapter)
    target_config = config("bigquery", project="analytics-project", database=None, read_only=False)
    generated = GeneratedTable(
        name="events", schema_name="qa",
        columns=[ColumnSpec(name="id", data_type="INT64"), ColumnSpec(name="address.city", data_type="STRING")],
        foreign_keys=[], rows=[{"id": 1, "address.city": "X"}],
    )

    for dry_run in (True, False):
        with pytest.raises(ConnectorError, match="Nested BigQuery target fields are not supported"):
            _load_bigquery(target_config, generated, batch_size=10, dry_run=dry_run, mode="append", confirm_destructive=False)
    assert adapter._connection.writes == 0


def test_redshift_postgresql_catalog_contract(monkeypatch):
    adapter = create_connector(config("redshift", schema_name="public"))

    def query(sql, params):
        if "FROM pg_database" in sql:
            return [("warehouse",)]
        if "information_schema.schemata" in sql:
            return [("public",)]
        if "information_schema.tables" in sql:
            return [("facts",)]
        if "constraint_type='PRIMARY KEY'" in sql:
            return [("fact_id",)]
        return []

    def dicts(sql, params):
        if "information_schema.columns" in sql:
            return [{"column_name": "fact_id", "data_type": "bigint", "is_nullable": "NO", "column_default": None,
                     "character_maximum_length": None, "numeric_precision": 64, "numeric_scale": 0}]
        if 'SELECT * FROM "public"."facts" LIMIT 5' in sql:
            return [{"fact_id": 12}]
        return []

    calls = query_stubs(monkeypatch, adapter, query, dicts)
    assert adapter.list_databases() == ["warehouse"]
    assert adapter.list_schemas() == ["public"]
    assert adapter.list_tables("public") == ["facts"]
    table = adapter.describe_table("facts", "public")
    assert table.primary_key_columns == ["fact_id"]
    assert table.columns[0].primary_key and not table.columns[0].nullable
    assert adapter.sample_rows("facts", "public", 5) == [{"fact_id": 12}]
    assert any("%s" in sql and params == ("public",) for _, sql, params in calls if "information_schema.tables" in sql)


def test_mysql_uppercase_metadata_labels_normalize_without_rewriting_row_data(monkeypatch):
    adapter = create_connector(config("mysql", database="sales", schema_name="sales"))

    def query(sql, params):
        if "constraint_type='PRIMARY KEY'" in sql:
            return [("ORDER_ID",)]
        if "referenced_table_name" in sql:
            return [("FK_ORDER_CUSTOMER", "CUSTOMER_ID", "CUSTOMERS", "ID")]
        return []

    def uppercase_metadata(sql, params):
        if "information_schema.columns" in sql:
            return [{
                "COLUMN_NAME": "ORDER_ID", "DATA_TYPE": "BIGINT", "IS_NULLABLE": "NO",
                "COLUMN_DEFAULT": None, "CHARACTER_MAXIMUM_LENGTH": None,
                "NUMERIC_PRECISION": 20, "NUMERIC_SCALE": 0,
            }, {
                "COLUMN_NAME": "CUSTOMER_ID", "DATA_TYPE": "BIGINT", "IS_NULLABLE": "NO",
                "COLUMN_DEFAULT": None, "CHARACTER_MAXIMUM_LENGTH": None,
                "NUMERIC_PRECISION": 20, "NUMERIC_SCALE": 0,
            }]
        if "SELECT * FROM" in sql:
            return [{"ORDER_ID": 7, "CUSTOMER_ID": 3}]
        return []

    calls = query_stubs(monkeypatch, adapter, query, uppercase_metadata)
    columns = adapter._column_rows("orders", "sales")
    assert [column["column_name"] for column in columns] == ["ORDER_ID", "CUSTOMER_ID"]
    assert set(columns[0]) == {
        "column_name", "data_type", "is_nullable", "column_default",
        "character_maximum_length", "numeric_precision", "numeric_scale",
    }
    table = adapter.describe_table("orders", "sales")
    assert table.primary_key_columns == ["ORDER_ID"]
    assert table.columns[0].primary_key and not table.columns[0].nullable
    assert table.columns[0].precision == 20
    assert table.foreign_keys[0].references_table == "CUSTOMERS"
    rows = adapter.sample_rows("orders", "sales", 5)
    assert rows == [{"ORDER_ID": 7, "CUSTOMER_ID": 3}]
    assert "ORDER_ID" in rows[0]  # Row keys remain the driver's original spelling.
    assert any("`sales`.`orders`" in sql and "LIMIT 5" in sql for _, sql, _ in calls if "SELECT * FROM" in sql)
