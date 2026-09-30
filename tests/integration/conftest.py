from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, Engine, create_engine, text

from reliable_guest_agent.infrastructure.database import (
    SCHEMA_NAME,
    require_safe_test_database,
)
from reliable_guest_agent.infrastructure.postgres import PostgresIntakeRepository


def _required_environment(name: str) -> str:
    value = os.environ.get(name)
    if value is None:
        pytest.fail(f"{name} is required for PostgreSQL integration tests")
    return value


def _verify_safe_test_database(url: str, engine: Engine) -> None:
    with engine.connect() as connection:
        actual_name = connection.execute(text("SELECT current_database()")).scalar_one()
    require_safe_test_database(url, actual_name)


@pytest.fixture(scope="session")
def migration_engine() -> Iterator[Engine]:
    migration_url = _required_environment("RGA_TEST_MIGRATION_DATABASE_URL")
    engine = create_engine(migration_url, pool_pre_ping=True)
    _verify_safe_test_database(migration_url, engine)

    previous_url = os.environ.get("RGA_MIGRATION_DATABASE_URL")
    os.environ["RGA_MIGRATION_DATABASE_URL"] = migration_url
    configuration = Config("alembic.ini")
    command.downgrade(configuration, "base")
    command.upgrade(configuration, "head")
    try:
        yield engine
    finally:
        if previous_url is None:
            os.environ.pop("RGA_MIGRATION_DATABASE_URL", None)
        else:
            os.environ["RGA_MIGRATION_DATABASE_URL"] = previous_url
        engine.dispose()


@pytest.fixture(scope="session")
def postgres_engine(migration_engine: Engine) -> Iterator[Engine]:
    del migration_engine
    runtime_url = _required_environment("RGA_TEST_DATABASE_URL")
    engine = create_engine(runtime_url, pool_pre_ping=True)
    yield engine
    engine.dispose()


@pytest.fixture
def postgres_connection(postgres_engine: Engine) -> Iterator[Connection]:
    with postgres_engine.connect() as connection:
        transaction = connection.begin()
        yield connection
        transaction.rollback()


@pytest.fixture
def postgres_repository(
    postgres_engine: Engine,
    postgres_connection: Connection,
) -> PostgresIntakeRepository:
    return PostgresIntakeRepository(postgres_engine, connection=postgres_connection)


@pytest.fixture
def committed_postgres_repository(
    postgres_engine: Engine,
    migration_engine: Engine,
) -> Iterator[PostgresIntakeRepository]:
    yield PostgresIntakeRepository(postgres_engine)
    with migration_engine.begin() as connection:
        connection.execute(text(f"TRUNCATE TABLE {SCHEMA_NAME}.inbound_messages CASCADE"))
