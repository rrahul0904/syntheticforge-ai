"""Disposable PostgreSQL/MySQL integration checks for CI services.

The tests are opt-in locally through DSNs and run as required jobs in CI. They use
separate source and target databases, so loading can never mutate the source.
"""
from __future__ import annotations

import os
from urllib.parse import urlparse

import pytest

from app.connectors import create_connector
from app.loaders import load_table
from app.models import ConnectorConfig, SystemGenerateRequest
from app.system_generator import generate_system


def _config_from_env(prefix: str, connector: str) -> ConnectorConfig | None:
    dsn = os.getenv(f"SYNTHETICFORGE_TEST_{prefix}_DSN")
    if not dsn:
        return None
    parsed = urlparse(dsn)
    return ConnectorConfig(
        connector=connector,
        host=parsed.hostname or "127.0.0.1",
        port=parsed.port,
        database=(parsed.path or "").lstrip("/"),
        username=parsed.username,
        password=parsed.password,
        schema_name="public" if connector == "postgresql" else (parsed.path or "").lstrip("/"),
        read_only=True,
    )


def _raw_connection(config: ConnectorConfig):
    if config.connector == "postgresql":
        import psycopg
        return psycopg.connect(host=config.host, port=config.port or 5432, dbname=config.database, user=config.username, password=config.password, autocommit=True)
    import pymysql
    return pymysql.connect(host=config.host, port=config.port or 3306, database=config.database, user=config.username, password=config.password, autocommit=True)


@pytest.mark.parametrize(
    ("prefix", "connector"),
    [("POSTGRESQL", "postgresql"), ("MYSQL", "mysql")],
)
def test_database_source_is_read_only_and_target_load_is_separate(prefix: str, connector: str):
    source = _config_from_env(prefix, connector)
    target_database = os.getenv(f"SYNTHETICFORGE_TEST_{prefix}_TARGET_DATABASE")
    if source is None or not target_database:
        pytest.skip(f"Set SYNTHETICFORGE_TEST_{prefix}_DSN and target database to run disposable integration test")

    source_db = _raw_connection(source)
    cursor = source_db.cursor()
    try:
        if connector == "postgresql":
            cursor.execute("CREATE TABLE IF NOT EXISTS customers (id INTEGER PRIMARY KEY, name VARCHAR(64) NOT NULL)")
            cursor.execute("CREATE TABLE IF NOT EXISTS orders (id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL REFERENCES customers(id), amount DECIMAL(10,2) NOT NULL)")
            cursor.execute("INSERT INTO customers VALUES (1,'source customer') ON CONFLICT (id) DO NOTHING")
            cursor.execute("INSERT INTO orders VALUES (1,1,12.50) ON CONFLICT (id) DO NOTHING")
            source.schema_name = "public"
        else:
            cursor.execute("CREATE TABLE IF NOT EXISTS customers (id INTEGER PRIMARY KEY, name VARCHAR(64) NOT NULL)")
            cursor.execute("CREATE TABLE IF NOT EXISTS orders (id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL, amount DECIMAL(10,2) NOT NULL, CONSTRAINT fk_orders_customer FOREIGN KEY(customer_id) REFERENCES customers(id))")
            cursor.execute("INSERT IGNORE INTO customers VALUES (1,'source customer')")
            cursor.execute("INSERT IGNORE INTO orders VALUES (1,1,12.50)")
        cursor.execute("SELECT COUNT(*) FROM customers")
        before_customers = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM orders")
        before_orders = cursor.fetchone()[0]
    finally:
        cursor.close()
        source_db.close()

    source.read_only = True
    adapter = create_connector(source)
    try:
        adapter.connect()
        specs = adapter.introspect_system(source.schema_name)
        assert {table.name for table in specs} >= {"customers", "orders"}
        assert adapter.profile_table("orders", source.schema_name, limit=100).row_count >= 1
        with pytest.raises(Exception):
            adapter._query("UPDATE customers SET name='must fail' WHERE id=1")
        assert adapter.policy_receipt()["read_only_enforced"] is True
        with pytest.raises(Exception, match="not found|inaccessible|table"):
            adapter.describe_table("syntheticforge_table_that_does_not_exist", source.schema_name)
    finally:
        adapter.close()

    bad_credentials = create_connector(source.model_copy(update={"password": "intentionally-wrong-credential"}))
    try:
        with pytest.raises(Exception) as failure:
            bad_credentials.connect()
        assert "intentionally-wrong-credential" not in str(failure.value)
    finally:
        bad_credentials.close()

    unavailable = create_connector(source.model_copy(update={"host": "127.0.0.1", "port": 1}))
    try:
        with pytest.raises(Exception):
            unavailable.connect()
    finally:
        unavailable.close()

    # The disposable target lives in its own database. Create the matching fixture.
    maintenance = source.model_copy(update={"database": source.database, "read_only": False})
    connection = _raw_connection(maintenance)
    cursor = connection.cursor()
    try:
        if connector == "postgresql":
            cursor.execute(f'CREATE DATABASE "{target_database}"') if not _database_exists_postgres(connection, target_database) else None
        else:
            cursor.execute(f"CREATE DATABASE IF NOT EXISTS `{target_database}`")
    finally:
        cursor.close()
        connection.close()

    target = source.model_copy(update={"database": target_database, "read_only": False, "schema_name": "public" if connector == "postgresql" else target_database})
    target_conn = _raw_connection(target)
    cur = target_conn.cursor()
    try:
        if connector == "postgresql":
            cur.execute("CREATE TABLE IF NOT EXISTS customers (id INTEGER PRIMARY KEY, name VARCHAR(64) NOT NULL)")
            cur.execute("CREATE TABLE IF NOT EXISTS orders (id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL REFERENCES customers(id), amount DECIMAL(10,2) NOT NULL)")
            cur.execute("DELETE FROM orders")
            cur.execute("DELETE FROM customers")
        else:
            cur.execute("CREATE TABLE IF NOT EXISTS customers (id INTEGER PRIMARY KEY, name VARCHAR(64) NOT NULL)")
            cur.execute("CREATE TABLE IF NOT EXISTS orders (id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL, amount DECIMAL(10,2) NOT NULL, CONSTRAINT fk_orders_customer FOREIGN KEY(customer_id) REFERENCES customers(id))")
            cur.execute("DELETE FROM orders")
            cur.execute("DELETE FROM customers")
    finally:
        cur.close()
        target_conn.close()

    system_request = SystemGenerateRequest(database_type=connector, database_name=target_database, schema_name=target.schema_name, default_row_count=5, seed=17, scenario="disposable database integration", tables=[table.model_copy(update={"schema_name": target.schema_name}) for table in specs])
    generated = generate_system(system_request)
    for table in generated.tables:
        loaded = load_table(target, table, batch_size=2, dry_run=False)
        assert loaded["rows"] == len(table.rows)

    # The source remains unchanged after a separate writable target load.
    source_check = _raw_connection(source)
    cur = source_check.cursor()
    try:
        cur.execute("SELECT COUNT(*) FROM customers")
        assert cur.fetchone()[0] == before_customers
        cur.execute("SELECT COUNT(*) FROM orders")
        assert cur.fetchone()[0] == before_orders
    finally:
        cur.close()
        source_check.close()


def _database_exists_postgres(connection, name: str) -> bool:
    cursor = connection.cursor()
    try:
        cursor.execute("SELECT 1 FROM pg_database WHERE datname=%s", (name,))
        return cursor.fetchone() is not None
    finally:
        cursor.close()
