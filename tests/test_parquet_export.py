from __future__ import annotations

import pytest

pa = pytest.importorskip("pyarrow")
pq = pytest.importorskip("pyarrow.parquet")

from app.parquet_export import rows_to_parquet, stream_parquet


def test_parquet_roundtrip_types_nulls_nested_values_and_streaming(tmp_path):
    rows = [
        {"id": 1, "score": 1.5, "note": None, "labels": ["new", "vip"], "profile": {"region": "west"}},
        {"id": 2, "score": 2.0, "note": "ok", "labels": [], "profile": {"region": "east"}},
    ]
    data = rows_to_parquet(rows)
    table = pq.read_table(pa.BufferReader(data))
    assert table.num_rows == 2
    assert table.schema.field("id").type == pa.int64()
    assert table.schema.field("score").type == pa.float64()
    assert table.column("note").null_count == 1
    assert table.to_pylist() == rows

    output = tmp_path / "stream.parquet"
    schema = pa.schema([
        ("id", pa.int64()),
        ("score", pa.float64()),
        ("note", pa.string()),
        ("labels", pa.list_(pa.string())),
        ("profile", pa.struct([("region", pa.string())])),
    ])
    written = stream_parquet([rows[:1], rows[1:]], output, schema=schema)
    assert written == 2
    assert pq.read_table(output).to_pylist() == rows
