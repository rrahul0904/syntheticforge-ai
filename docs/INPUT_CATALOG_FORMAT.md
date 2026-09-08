# SyntheticForge JSON Catalog

For systems that do not already have DDL, JSON Schema, OpenAPI or Avro, use the canonical JSON catalog.

```json
{
  "tables": [
    {
      "name": "customers",
      "schema_name": "app",
      "row_count": 100,
      "columns": [
        {"name": "customer_id", "data_type": "bigint", "primary_key": true, "nullable": false},
        {"name": "email", "data_type": "varchar", "semantic_type": "email", "unique": true, "nullable": false}
      ]
    },
    {
      "name": "orders",
      "schema_name": "app",
      "columns": [
        {"name": "order_id", "data_type": "bigint", "primary_key": true, "nullable": false},
        {"name": "customer_id", "data_type": "bigint", "nullable": false},
        {"name": "status", "data_type": "varchar", "choices": ["pending", "paid", "cancelled"]}
      ],
      "foreign_keys": [
        {"column": "customer_id", "references_table": "customers", "references_column": "customer_id"}
      ]
    }
  ]
}
```

This format is the escape hatch for custom applications and proprietary systems: transform their metadata into the canonical catalog, then use the same generation engine.
