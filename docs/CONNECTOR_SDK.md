# Connector SDK

SyntheticForge connectors subclass `app.connectors.base.BaseConnector`. A connector implements connection creation, schema/table discovery, table metadata extraction, and bounded sampling. The generation engine does not require vendor-specific code.

Required methods:

- `_connect_impl()`
- `list_schemas()`
- `list_tables(schema_name)`
- `describe_table(table_name, schema_name)`
- `sample_rows(table_name, schema_name, limit)`

Use `connector_contract()` from `app.connectors.sdk` in connector packages. Third-party packages may publish a Python entry point in group `syntheticforge.connectors`; `load_entrypoint_connectors()` discovers them.

Source connectors should default to read-only configuration. Sampling implementations must honor the requested bound and should never select an entire unbounded production table.

See `examples/connectors/demo_connector.py`.
