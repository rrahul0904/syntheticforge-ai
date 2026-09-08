from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

DDL = """
CREATE TABLE prod.guests (
  guest_id BIGINT PRIMARY KEY,
  first_name VARCHAR(80) NOT NULL,
  last_name VARCHAR(80) NOT NULL,
  email VARCHAR(255) UNIQUE NOT NULL
);

CREATE TABLE prod.reservations (
  reservation_id BIGINT PRIMARY KEY,
  guest_id BIGINT NOT NULL REFERENCES prod.guests(guest_id),
  check_in_date DATE NOT NULL,
  check_out_date DATE NOT NULL,
  status VARCHAR(30) NOT NULL,
  nightly_rate DECIMAL(10,2) NOT NULL,
  CONSTRAINT fk_guest FOREIGN KEY (guest_id) REFERENCES prod.guests(guest_id)
);
"""


def parsed_tables():
    r = client.post("/api/parse-schema", json={
        "database_type": "postgresql",
        "database_name": "hotel",
        "schema_name": "prod",
        "input_format": "ddl",
        "content": DDL,
        "default_row_count": 20,
    })
    assert r.status_code == 200, r.text
    return r.json()


def test_parse_multi_table_ddl_and_relationships():
    data = parsed_tables()
    assert [t["name"] for t in data["tables"]] == ["guests", "reservations"]
    # Inline + named duplicate FK definitions should both parse; generation remains valid.
    assert data["relationship_count"] >= 1
    reservations = data["tables"][1]
    assert any(c["name"] == "nightly_rate" and c["data_type"].lower() == "decimal(10,2)" for c in reservations["columns"])
    assert any(c["name"] == "reservation_id" and c["primary_key"] for c in reservations["columns"])


def test_generate_system_preserves_foreign_keys_and_date_order():
    parsed = parsed_tables()
    r = client.post("/api/generate-system", json={
        "database_type": "postgresql",
        "database_name": "hotel",
        "schema_name": "prod",
        "default_row_count": 20,
        "seed": 2026,
        "scenario": "Hotel QA dataset with 25% cancellations",
        "tables": parsed["tables"],
    })
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["generation_order"][0].endswith("guests")
    by_name = {t["name"]: t for t in data["tables"]}
    guest_ids = {row["guest_id"] for row in by_name["guests"]["rows"]}
    reservation_guest_ids = {row["guest_id"] for row in by_name["reservations"]["rows"]}
    assert reservation_guest_ids <= guest_ids
    for row in by_name["reservations"]["rows"]:
        assert row["check_out_date"] > row["check_in_date"]
    cancelled = [row for row in by_name["reservations"]["rows"] if row["status"] == "cancelled"]
    assert len(cancelled) == 5


def test_export_system_zip_contains_manifest_csv_and_sql():
    parsed = parsed_tables()
    req = {
        "database_type": "postgresql",
        "database_name": "hotel",
        "schema_name": "prod",
        "default_row_count": 5,
        "seed": 7,
        "tables": parsed["tables"],
    }
    r = client.post("/api/export-system", json=req)
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    assert r.content[:2] == b"PK"


def test_parse_json_schema():
    schema = {
        "title": "customers",
        "type": "object",
        "required": ["customer_id", "email"],
        "properties": {
            "customer_id": {"type": "integer", "primary_key": True},
            "email": {"type": "string", "format": "email"},
            "is_active": {"type": "boolean"},
            "created_at": {"type": "string", "format": "date-time"},
        },
    }
    r = client.post("/api/parse-schema", json={
        "database_type": "snowflake",
        "database_name": "analytics",
        "schema_name": "raw",
        "input_format": "json",
        "content": __import__("json").dumps(schema),
    })
    assert r.status_code == 200, r.text
    table = r.json()["tables"][0]
    assert table["name"] == "customers"
    assert any(c["name"] == "customer_id" and c["primary_key"] for c in table["columns"])


def test_parse_openapi_components():
    spec = {
        "openapi": "3.0.3",
        "components": {"schemas": {
            "Guest": {"type": "object", "required": ["guest_id", "email"], "properties": {
                "guest_id": {"type": "integer"}, "email": {"type": "string", "format": "email"}
            }},
            "Reservation": {"type": "object", "required": ["reservation_id", "guest_id"], "properties": {
                "reservation_id": {"type": "integer"}, "guest_id": {"type": "integer"}, "status": {"type": "string", "enum": ["confirmed", "cancelled"]}
            }}
        }}
    }
    r = client.post("/api/parse-schema", json={
        "database_type": "postgresql", "database_name": "api", "schema_name": "public",
        "input_format": "openapi", "content": __import__("json").dumps(spec), "default_row_count": 5
    })
    assert r.status_code == 200, r.text
    data = r.json()
    assert {t["name"] for t in data["tables"]} == {"guest", "reservation"}
    assert data["relationship_count"] == 1


def test_parse_avro_record():
    avro = {
        "type": "record", "name": "CustomerEvent", "fields": [
            {"name": "customer_event_id", "type": "long"},
            {"name": "customer_id", "type": "long"},
            {"name": "event_type", "type": {"type": "enum", "name": "EventType", "symbols": ["CREATED", "UPDATED"]}},
            {"name": "processed", "type": ["null", "boolean"]}
        ]
    }
    r = client.post("/api/parse-schema", json={
        "database_type": "snowflake", "database_name": "events", "schema_name": "raw",
        "input_format": "avro", "content": __import__("json").dumps(avro)
    })
    assert r.status_code == 200, r.text
    table = r.json()["tables"][0]
    assert table["name"] == "customer_event"
    assert any(c["name"] == "event_type" and c["choices"] == ["CREATED", "UPDATED"] for c in table["columns"])


def test_parse_csv_sample_profiles_types_and_choices():
    content = "customer_id,email,status,total\n1,a@example.com,active,19.95\n2,b@example.com,pending,25.00\n3,c@example.com,active,31.50\n4,d@example.com,pending,14.20\n"
    r = client.post("/api/parse-schema", json={
        "database_type": "mysql", "database_name": "sample", "schema_name": "app",
        "input_format": "csv", "table_name": "customers", "content": content
    })
    assert r.status_code == 200, r.text
    table = r.json()["tables"][0]
    by_name = {c["name"]: c for c in table["columns"]}
    assert by_name["customer_id"]["primary_key"]
    assert by_name["total"]["data_type"] == "decimal(18,4)"
    assert set(by_name["status"]["choices"]) == {"active", "pending"}


def test_natural_language_hospitality_system_inference():
    r = client.post("/api/parse-schema", json={
        "database_type": "postgresql", "database_name": "hotel_ai", "schema_name": "prod",
        "input_format": "description", "content": "Online hospitality reservation platform with guests, rooms, bookings and payments",
        "default_row_count": 12
    })
    assert r.status_code == 200, r.text
    data = r.json()
    names = {t["name"] for t in data["tables"]}
    assert {"hotels", "guests", "rooms", "reservations", "payments"} <= names
    assert data["relationship_count"] >= 4


def test_description_scenario_percentage_is_exact():
    p = client.post("/api/parse-schema", json={
        "database_type": "postgresql", "database_name": "hotel", "schema_name": "prod",
        "input_format": "description", "content": "hotel reservation platform", "default_row_count": 10
    })
    assert p.status_code == 200
    g = client.post("/api/generate-system", json={
        "database_type": "postgresql", "database_name": "hotel", "schema_name": "prod",
        "default_row_count": 10, "seed": 42, "scenario": "12% cancellations", "tables": p.json()["tables"]
    })
    assert g.status_code == 200
    reservations = next(t for t in g.json()["tables"] if t["name"] == "reservations")
    assert sum(1 for r in reservations["rows"] if r["status"] == "cancelled") == round(len(reservations["rows"]) * 0.12)


def test_generated_system_returns_passing_validation_report():
    parsed = parsed_tables()
    r = client.post("/api/generate-system", json={
        "database_type": "postgresql", "database_name": "hotel", "schema_name": "prod",
        "default_row_count": 10, "seed": 55, "scenario": "20% cancellations", "tables": parsed["tables"]
    })
    assert r.status_code == 200, r.text
    report = r.json()["validation"]
    assert report["passed"] is True
    names = {c["name"] for c in report["checks"]}
    assert {"not_null", "primary_key_uniqueness", "foreign_key_integrity", "date_coherence", "scenario_percentages"} <= names


def test_read_only_sqlite_metadata_introspection(tmp_path):
    import sqlite3
    db = tmp_path / "shop.db"
    conn = sqlite3.connect(db)
    conn.executescript("""
    PRAGMA foreign_keys=ON;
    CREATE TABLE customers (
      customer_id INTEGER PRIMARY KEY,
      email TEXT NOT NULL UNIQUE
    );
    CREATE TABLE orders (
      order_id INTEGER PRIMARY KEY,
      customer_id INTEGER NOT NULL,
      status TEXT,
      FOREIGN KEY(customer_id) REFERENCES customers(customer_id)
    );
    """)
    conn.close()
    r = client.post("/api/parse-schema", json={
        "database_type": "sqlite", "database_name": "shop", "schema_name": "main",
        "input_format": "sqlite-db", "content": str(db), "default_row_count": 8
    })
    assert r.status_code == 200, r.text
    data = r.json()
    assert {t["name"] for t in data["tables"]} == {"customers", "orders"}
    assert data["relationship_count"] == 1
    orders = next(t for t in data["tables"] if t["name"] == "orders")
    assert orders["foreign_keys"][0]["references_table"] == "customers"
    assert "no source rows were copied" in data["warnings"][0].lower()
