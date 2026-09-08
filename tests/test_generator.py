from app.generator import generate_rows, infer_schema_local, rows_to_sql
from app.models import GenerateRequest, SchemaInferenceRequest


def test_reservation_inference_and_coherence():
    meta = SchemaInferenceRequest(database_type="postgresql", database_name="hotel", schema_name="prod", table_name="reservations")
    cols = infer_schema_local(meta)
    assert any(c.name == "confirmation_code" for c in cols)
    req = GenerateRequest(database_type="postgresql", database_name="hotel", schema_name="prod", table_name="reservations", row_count=20, seed=7, columns=cols)
    rows, warnings = generate_rows(req, cols)
    assert len(rows) == 20
    for row in rows:
        if row["check_in_date"]:
            assert row["check_out_date"] > row["check_in_date"]


def test_deterministic_generation():
    cols = infer_schema_local(SchemaInferenceRequest(database_type="postgresql", database_name="x", schema_name="public", table_name="users"))
    req = GenerateRequest(database_type="postgresql", database_name="x", schema_name="public", table_name="users", row_count=3, seed=101, columns=cols)
    a, _ = generate_rows(req, cols)
    b, _ = generate_rows(req, cols)
    assert a == b


def test_sql_export_escapes_and_targets_table():
    cols = infer_schema_local(SchemaInferenceRequest(database_type="postgresql", database_name="x", schema_name="public", table_name="users"))
    req = GenerateRequest(database_type="postgresql", database_name="x", schema_name="public", table_name="users", row_count=2, seed=1, columns=cols)
    rows, _ = generate_rows(req, cols)
    sql = rows_to_sql(req, cols, rows)
    assert 'INSERT INTO "public"."users"' in sql
    assert sql.count("INSERT INTO") == 2
