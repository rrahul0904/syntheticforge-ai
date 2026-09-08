from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from pydantic import BaseModel, Field, model_validator

DatabaseType = Literal[
    "postgresql", "mysql", "sqlserver", "oracle", "snowflake", "bigquery", "redshift", "sqlite"
]
SchemaInputFormat = Literal["ddl", "json", "openapi", "avro", "csv", "description", "sqlite-db"]
SensitivityClass = Literal["non-sensitive", "personal", "sensitive-pii", "potential-phi", "secret"]
JobState = Literal["queued", "profiling", "modeling", "generating", "validating", "exporting", "loading", "completed", "failed", "cancelled"]


class ColumnStatistics(BaseModel):
    count: int = 0
    null_count: int = 0
    null_rate: float = 0.0
    distinct_count: int | None = None
    min_value: Any | None = None
    max_value: Any | None = None
    mean: float | None = None
    median: float | None = None
    stddev: float | None = None
    skewness: float | None = None
    quantiles: dict[str, float] = Field(default_factory=dict)
    histogram: list[dict[str, Any]] = Field(default_factory=list)
    top_values: list[dict[str, Any]] = Field(default_factory=list)
    min_length: int | None = None
    max_length: int | None = None
    mean_length: float | None = None
    patterns: list[str] = Field(default_factory=list)
    prefixes: list[str] = Field(default_factory=list)
    suffixes: list[str] = Field(default_factory=list)
    hour_histogram: dict[str, int] = Field(default_factory=dict)
    weekday_histogram: dict[str, int] = Field(default_factory=dict)
    month_histogram: dict[str, int] = Field(default_factory=dict)


class ColumnSpec(BaseModel):
    name: str = Field(min_length=1, max_length=256)
    data_type: str = "varchar"
    semantic_type: str | None = None
    nullable: bool = True
    unique: bool = False
    primary_key: bool = False
    generated: bool = False
    identity: bool = False
    default: Any | None = None
    description: str | None = None
    enum_values: list[Any] | None = None
    length: int | None = Field(default=None, ge=1)
    precision: int | None = Field(default=None, ge=1)
    scale: int | None = Field(default=None, ge=0)
    min_value: float | None = None
    max_value: float | None = None
    choices: list[Any] | None = None
    null_rate: float = Field(default=0.05, ge=0, le=1)
    sensitivity: SensitivityClass = "non-sensitive"
    statistics: ColumnStatistics | None = None

    @model_validator(mode="after")
    def sync_choices(self):
        if self.choices is None and self.enum_values:
            self.choices = list(self.enum_values)
        if self.enum_values is None and self.choices:
            self.enum_values = list(self.choices)
        if self.primary_key:
            self.nullable = False
            self.unique = True
            self.null_rate = 0
        return self


class ForeignKeySpec(BaseModel):
    column: str
    references_table: str
    references_column: str = "id"
    columns: list[str] = Field(default_factory=list)
    references_columns: list[str] = Field(default_factory=list)
    name: str | None = None
    on_delete: str | None = None
    on_update: str | None = None

    @model_validator(mode="after")
    def normalize_composite(self):
        if not self.columns:
            self.columns = [self.column]
        if not self.references_columns:
            self.references_columns = [self.references_column]
        self.column = self.columns[0]
        self.references_column = self.references_columns[0]
        if len(self.columns) != len(self.references_columns):
            raise ValueError("Composite foreign-key column counts must match")
        return self


class UniqueConstraintSpec(BaseModel):
    name: str | None = None
    columns: list[str] = Field(min_length=1)


class CheckConstraintSpec(BaseModel):
    name: str | None = None
    expression: str


class IndexSpec(BaseModel):
    name: str
    columns: list[str] = Field(min_length=1)
    unique: bool = False


class CorrelationSpec(BaseModel):
    columns: list[str] = Field(min_length=2)
    kind: Literal["pearson", "conditional", "functional", "date-order"] = "pearson"
    coefficient: float | None = None
    mapping: dict[str, Any] = Field(default_factory=dict)
    tolerance: float = 0.15


class BusinessRuleSpec(BaseModel):
    name: str
    expression: str
    severity: Literal["info", "warning", "error"] = "error"
    source: Literal["declared", "inferred", "user", "ai"] = "declared"
    description: str | None = None


class GenerateRequest(BaseModel):
    database_type: DatabaseType
    database_name: str = Field(min_length=1, max_length=128)
    schema_name: str = Field(default="public", min_length=1, max_length=128)
    table_name: str = Field(min_length=1, max_length=128)
    row_count: int = Field(default=50, ge=1, le=10_000_000)
    seed: int = Field(default=42, ge=0, le=2_147_483_647)
    locale: str = "en_US"
    scenario: str | None = Field(default=None, max_length=8000)
    columns: list[ColumnSpec] = Field(default_factory=list)
    foreign_keys: list[ForeignKeySpec] = Field(default_factory=list)
    ai_inference: bool = True
    negative_testing: bool = False
    edge_case_rate: float = Field(default=0.0, ge=0, le=1)

    @model_validator(mode="after")
    def ensure_column_names_unique(self):
        names = [c.name.lower() for c in self.columns]
        if len(names) != len(set(names)):
            raise ValueError("Column names must be unique")
        return self


class SchemaInferenceRequest(BaseModel):
    database_type: DatabaseType
    database_name: str
    schema_name: str = "public"
    table_name: str
    scenario: str | None = None


class GenerateResponse(BaseModel):
    inferred_columns: list[ColumnSpec]
    rows: list[dict[str, Any]]
    warnings: list[str] = Field(default_factory=list)
    generation_mode: Literal["smart-local", "ai-assisted", "explicit-schema"]


class TableSpec(BaseModel):
    name: str = Field(min_length=1, max_length=256)
    schema_name: str = Field(default="public", min_length=1, max_length=256)
    table_type: Literal["table", "view", "materialized_view"] = "table"
    description: str | None = None
    row_count: int | None = Field(default=None, ge=1, le=100_000_000)
    columns: list[ColumnSpec] = Field(default_factory=list)
    foreign_keys: list[ForeignKeySpec] = Field(default_factory=list)
    primary_key_columns: list[str] = Field(default_factory=list)
    unique_constraints: list[UniqueConstraintSpec] = Field(default_factory=list)
    check_constraints: list[CheckConstraintSpec] = Field(default_factory=list)
    indexes: list[IndexSpec] = Field(default_factory=list)
    correlations: list[CorrelationSpec] = Field(default_factory=list)
    business_rules: list[BusinessRuleSpec] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_table(self):
        names = [c.name.lower() for c in self.columns]
        if len(names) != len(set(names)):
            raise ValueError(f"Duplicate columns in table {self.name}")
        known = set(names)
        pk_order = list(self.primary_key_columns)
        for column in self.columns:
            if column.primary_key and column.name not in pk_order:
                pk_order.append(column.name)
        self.primary_key_columns = pk_order
        for col_name in self.primary_key_columns:
            if col_name.lower() in known:
                for col in self.columns:
                    if col.name.lower() == col_name.lower():
                        col.primary_key = True
                        col.nullable = False
                        col.unique = len(self.primary_key_columns) == 1
        for fk in self.foreign_keys:
            for col in fk.columns:
                if col.lower() not in known:
                    raise ValueError(f"Foreign key column {col} does not exist in {self.name}")
        return self


class SystemSpec(BaseModel):
    name: str = "Synthetic System"
    database_type: DatabaseType = "postgresql"
    database_name: str = "synthetic"
    tables: list[TableSpec] = Field(default_factory=list)
    business_rules: list[BusinessRuleSpec] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ParseSchemaRequest(BaseModel):
    database_type: DatabaseType
    database_name: str = Field(min_length=1, max_length=128)
    schema_name: str = Field(default="public", min_length=1, max_length=128)
    input_format: SchemaInputFormat = "ddl"
    content: str = Field(min_length=2, max_length=5_000_000)
    table_name: str = Field(default="records", min_length=1, max_length=128)
    default_row_count: int = Field(default=50, ge=1, le=100_000_000)


class ParseSchemaResponse(BaseModel):
    tables: list[TableSpec]
    relationship_count: int
    warnings: list[str] = Field(default_factory=list)


class SystemGenerateRequest(BaseModel):
    database_type: DatabaseType
    database_name: str = Field(min_length=1, max_length=128)
    schema_name: str = Field(default="public", min_length=1, max_length=128)
    default_row_count: int = Field(default=50, ge=1, le=100_000_000)
    seed: int = Field(default=42, ge=0, le=2_147_483_647)
    locale: str = "en_US"
    scenario: str | None = Field(default=None, max_length=8000)
    tables: list[TableSpec] = Field(min_length=1, max_length=1000)
    negative_testing: bool = False
    edge_case_rate: float = Field(default=0.0, ge=0, le=1)

    @model_validator(mode="after")
    def validate_unique_tables(self):
        keys = [(t.schema_name.lower(), t.name.lower()) for t in self.tables]
        if len(keys) != len(set(keys)):
            raise ValueError("Table names must be unique within a schema")
        return self


class GeneratedTable(BaseModel):
    name: str
    schema_name: str
    columns: list[ColumnSpec]
    foreign_keys: list[ForeignKeySpec]
    rows: list[dict[str, Any]]


class ValidationCheck(BaseModel):
    name: str
    passed: bool
    details: str
    validator: str | None = None
    expected: Any | None = None
    observed: Any | None = None
    severity: Literal["info", "warning", "error"] = "error"
    table: str | None = None
    column: str | None = None
    remediation: str | None = None


class ValidationReport(BaseModel):
    passed: bool
    checks: list[ValidationCheck] = Field(default_factory=list)
    quality_score: float = Field(default=100.0, ge=0, le=100)


class SystemGenerateResponse(BaseModel):
    generation_order: list[str]
    relationship_count: int
    tables: list[GeneratedTable]
    warnings: list[str] = Field(default_factory=list)
    validation: ValidationReport


class ConnectorConfig(BaseModel):
    connector: DatabaseType
    name: str = "default"
    host: str | None = None
    port: int | None = Field(default=None, ge=1, le=65535)
    database: str | None = None
    schema_name: str | None = None
    username: str | None = None
    password: str | None = None
    account: str | None = None
    warehouse: str | None = None
    project: str | None = None
    path: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)
    read_only: bool = True


class ConnectorStatus(BaseModel):
    connector: str
    available: bool
    driver: str | None = None
    configured: bool = False
    message: str | None = None
    capabilities: list[str] = Field(default_factory=list)


class ProfileRequest(BaseModel):
    columns: list[ColumnSpec] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    max_sample_rows: int = Field(default=10_000, ge=1, le=100_000)


class TableProfile(BaseModel):
    row_count: int
    sampled_rows: int
    columns: dict[str, ColumnStatistics]
    correlations: list[CorrelationSpec] = Field(default_factory=list)


class ProjectCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    system: SystemSpec | None = None
    settings: dict[str, Any] = Field(default_factory=dict)


class RecipeSpec(BaseModel):
    id: str | None = None
    project_id: str
    name: str
    version: int = 1
    seed: int = 42
    row_counts: dict[str, int] = Field(default_factory=dict)
    scenario: str | None = None
    settings: dict[str, Any] = Field(default_factory=dict)


class DatasetRecord(BaseModel):
    id: str
    project_id: str
    version: int
    recipe_id: str | None = None
    seed: int
    schema_hash: str
    rules_hash: str
    generator_version: str
    created_at: datetime
    row_counts: dict[str, int]
    validation: dict[str, Any] = Field(default_factory=dict)
    profile: dict[str, Any] = Field(default_factory=dict)
    exports: list[str] = Field(default_factory=list)
    parent_version: int | None = None


class JobRecord(BaseModel):
    id: str
    project_id: str | None = None
    state: JobState = "queued"
    progress: float = Field(default=0, ge=0, le=100)
    current_step: str = "queued"
    generated_rows: int = 0
    created_at: datetime
    updated_at: datetime
    error: str | None = None
    logs: list[str] = Field(default_factory=list)
    payload: dict[str, Any] = Field(default_factory=dict)

class ConnectorIntrospectRequest(BaseModel):
    config: ConnectorConfig
    schema_name: str | None = None
    include_profiles: bool = False
    profile_limit: int = Field(default=1000, ge=1, le=100_000)


class DirectLoadRequest(BaseModel):
    config: ConnectorConfig
    table: GeneratedTable
    batch_size: int = Field(default=1000, ge=1, le=100_000)
    dry_run: bool = True
    mode: Literal["append", "truncate"] = "append"
    confirm_destructive: bool = False


class ProjectGenerateRequest(BaseModel):
    request: SystemGenerateRequest
    recipe_id: str | None = None
    parent_version: int | None = None


class JobGenerateRequest(BaseModel):
    project_id: str | None = None
    request: SystemGenerateRequest

class SystemModelingRequest(BaseModel):
    database_type: DatabaseType = "postgresql"
    database_name: str = "synthetic"
    schema_name: str = "public"
    description: str = Field(min_length=3, max_length=20_000)
    default_row_count: int = Field(default=50, ge=1, le=1_000_000)
    ai_inference: bool = True

AgentRunState = Literal[
    "queued", "planning", "running", "repairing", "awaiting_approval", "completed", "failed", "cancelled"
]


class AgentTraceEvent(BaseModel):
    id: str
    created_at: datetime
    kind: Literal["plan", "tool", "observation", "validation", "repair", "approval", "result", "error"]
    step: str
    summary: str
    details: dict[str, Any] = Field(default_factory=dict)


class AgentRunCreateRequest(BaseModel):
    goal: str = Field(min_length=3, max_length=20_000)
    project_id: str | None = None
    database_type: DatabaseType = "postgresql"
    database_name: str = "synthetic"
    schema_name: str = "public"
    default_row_count: int = Field(default=50, ge=1, le=1_000_000)
    seed: int = Field(default=42, ge=0, le=2_147_483_647)
    quality_threshold: float = Field(default=95.0, ge=0, le=100)
    max_repairs: int = Field(default=2, ge=0, le=10)
    source_config: ConnectorConfig | None = None
    input_format: SchemaInputFormat | None = None
    content: str | None = Field(default=None, max_length=5_000_000)
    scenario: str | None = Field(default=None, max_length=8_000)
    ai_planning: bool = True


class AgentRunRecord(BaseModel):
    id: str
    goal: str
    project_id: str | None = None
    state: AgentRunState = "queued"
    current_step: str = "queued"
    quality_threshold: float = 95.0
    quality_score: float | None = None
    repair_attempts: int = 0
    plan: list[str] = Field(default_factory=list)
    trace: list[AgentTraceEvent] = Field(default_factory=list)
    request: dict[str, Any] = Field(default_factory=dict)
    dataset_id: str | None = None
    artifact_path: str | None = None
    approval_required: bool = True
    approval_state: Literal["not-requested", "pending", "approved", "declined"] = "not-requested"
    created_at: datetime
    updated_at: datetime
    error: str | None = None


class AgentLoadApprovalRequest(BaseModel):
    config: ConnectorConfig
    batch_size: int = Field(default=1000, ge=1, le=100_000)
    mode: Literal["append", "truncate"] = "append"
    confirm_destructive: bool = False


class AIProviderSessionRequest(BaseModel):
    provider: Literal["none", "ollama", "openai-compatible"] = "none"
    model: str | None = Field(default=None, max_length=256)
    base_url: str | None = Field(default=None, max_length=2048)
    api_key: str | None = Field(default=None, max_length=8192)
