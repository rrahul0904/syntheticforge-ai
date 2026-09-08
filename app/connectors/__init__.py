from __future__ import annotations

from typing import Type

from .base import BaseConnector, ConnectorError, DriverUnavailableError
from .sqlite import SQLiteConnector
from .dbapi import PostgreSQLConnector, RedshiftConnector, MySQLConnector, SQLServerConnector, OracleConnector
from .cloud import SnowflakeConnector, BigQueryConnector
from ..models import ConnectorConfig, ConnectorStatus

CONNECTORS: dict[str, Type[BaseConnector]] = {
    "sqlite": SQLiteConnector,
    "postgresql": PostgreSQLConnector,
    "mysql": MySQLConnector,
    "sqlserver": SQLServerConnector,
    "oracle": OracleConnector,
    "snowflake": SnowflakeConnector,
    "bigquery": BigQueryConnector,
    "redshift": RedshiftConnector,
}


def create_connector(config: ConnectorConfig) -> BaseConnector:
    cls = CONNECTORS.get(config.connector)
    if not cls:
        raise ConnectorError(f"Unsupported connector: {config.connector}")
    return cls(config)


def connector_statuses() -> list[ConnectorStatus]:
    result=[]
    for name, cls in CONNECTORS.items():
        driver=cls.available_driver()
        result.append(ConnectorStatus(connector=name, available=cls.is_available(), driver=driver, configured=False, capabilities=list(cls.capabilities), message=None if cls.is_available() else "Optional driver not installed"))
    return result

__all__=["BaseConnector","ConnectorError","DriverUnavailableError","create_connector","connector_statuses","CONNECTORS"]
