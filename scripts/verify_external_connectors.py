#!/usr/bin/env python3
"""Credential-gated read-only smoke verification for external database connectors."""
from __future__ import annotations
import json, os, sys
from app.connectors import create_connector
from app.models import ConnectorConfig

CONNECTORS = ["postgresql", "mysql", "sqlserver", "oracle", "snowflake", "bigquery", "redshift"]

def main() -> int:
    attempted = passed = 0
    for name in CONNECTORS:
        env = f"SYNTHETICFORGE_{name.upper()}"
        raw = os.getenv(env)
        if not raw:
            print(f"SKIP {name:11} {env} not set")
            continue
        attempted += 1
        try:
            payload = json.loads(raw); payload["connector"] = name; payload["read_only"] = True
            cfg = ConnectorConfig.model_validate(payload)
            connector = create_connector(cfg)
            try:
                test = connector.test_connection()
                if not test.get("ok"):
                    raise RuntimeError(test.get("error") or "connection test failed")
                schemas = connector.list_schemas()
                schema = cfg.schema_name or (schemas[0] if schemas else None)
                tables = connector.list_tables(schema) if schema else []
                if tables:
                    connector.describe_table(tables[0], schema)
                print(f"PASS {name:11} schemas={len(schemas)} sample_tables={len(tables)}")
                passed += 1
            finally:
                connector.close()
        except Exception as exc:
            print(f"FAIL {name:11} {exc}")
    print(f"verified={passed}/{attempted}; configured={attempted}/{len(CONNECTORS)}")
    return 0 if attempted and passed == attempted else (2 if attempted else 3)

if __name__ == "__main__":
    raise SystemExit(main())
