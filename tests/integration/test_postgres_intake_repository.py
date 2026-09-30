from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, insert, inspect, select, text
from sqlalchemy.exc import IntegrityError, ProgrammingError

from reliable_guest_agent.api.main import create_app
from reliable_guest_agent.application.intake import IntakeCommand, IntakeGuestMessage
from reliable_guest_agent.domain.enums import RequestType
from reliable_guest_agent.infrastructure.auth import SyntheticBearerAuthenticator
from reliable_guest_agent.infrastructure.database import (
    SCHEMA_NAME,
    cases,
    inbound_message_request_types,
    inbound_messages,
)
from reliable_guest_agent.infrastructure.memory import InMemoryReservationAuthorizer
from reliable_guest_agent.infrastructure.postgres import PostgresIntakeRepository
from reliable_guest_agent.infrastructure.settings import ApplicationSettings
from tests.repository_contract import assert_intake_repository_contract

pytestmark = pytest.mark.postgres


def test_postgres_repository_satisfies_intake_contract(
    postgres_repository: PostgresIntakeRepository,
    postgres_connection,
) -> None:
    assert_intake_repository_contract(postgres_repository)

    request_type_count = postgres_connection.execute(
        select(func.count()).select_from(inbound_message_request_types)
    ).scalar_one()
    assert request_type_count == 1


def test_migrations_create_expected_tables(migration_engine: Engine) -> None:
    table_names = set(inspect(migration_engine).get_table_names(schema=SCHEMA_NAME))

    assert table_names == {
        "cases",
        "idempotency_records",
        "inbound_message_request_types",
        "inbound_messages",
        "outbox_events",
    }


def test_failed_write_rolls_back_all_intake_rows(
    postgres_engine: Engine,
    postgres_connection,
) -> None:
    def fail_after_case_write() -> None:
        raise RuntimeError("simulated failure after case write")

    repository = PostgresIntakeRepository(
        postgres_engine,
        connection=postgres_connection,
        after_case_write=fail_after_case_write,
    )
    use_case = IntakeGuestMessage(
        repository,
        InMemoryReservationAuthorizer({"reservation-rollback": "guest-rollback"}),
    )

    with pytest.raises(RuntimeError, match="simulated failure"):
        use_case.execute(
            IntakeCommand(
                guest_id="guest-rollback",
                reservation_reference="reservation-rollback",
                original_message="Please refund this reservation.",
                selected_request_types=(RequestType.REFUND,),
                idempotency_key=str(uuid4()),
            )
        )

    for table_name in (
        "inbound_messages",
        "cases",
        "outbox_events",
        "idempotency_records",
    ):
        count = postgres_connection.execute(
            text(f"SELECT count(*) FROM {SCHEMA_NAME}.{table_name}")
        ).scalar_one()
        assert count == 0


def test_concurrent_identical_intakes_commit_one_result(
    committed_postgres_repository: PostgresIntakeRepository,
) -> None:
    command = IntakeCommand(
        guest_id="guest-concurrent",
        reservation_reference="reservation-concurrent",
        original_message="Please refund this reservation.",
        selected_request_types=(RequestType.REFUND,),
        idempotency_key=str(uuid4()),
    )
    authorizer = InMemoryReservationAuthorizer(
        {"reservation-concurrent": "guest-concurrent"}
    )

    def submit(_: int):
        return IntakeGuestMessage(
            committed_postgres_repository,
            authorizer,
        ).execute(command)

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(submit, range(16)))

    assert len({result.message_id for result in results}) == 1
    assert len({result.case_id for result in results}) == 1
    assert sum(not result.replayed for result in results) == 1


def test_runtime_role_cannot_create_tables(postgres_engine: Engine) -> None:
    with pytest.raises(ProgrammingError), postgres_engine.begin() as connection:
        connection.execute(text(f"CREATE TABLE {SCHEMA_NAME}.forbidden (id integer)"))


def test_database_rejects_unrecognized_processing_status(postgres_connection) -> None:
    message_id = uuid4()
    postgres_connection.execute(
        insert(inbound_messages).values(
            id=message_id,
            reservation_reference="reservation-constraint",
            sender_reference="guest-constraint",
            original_text="Please refund this reservation.",
            received_at=datetime.now(UTC),
        )
    )

    with pytest.raises(IntegrityError), postgres_connection.begin_nested():
        postgres_connection.execute(
            insert(cases).values(
                id=uuid4(),
                message_id=message_id,
                processing_status="NOT_A_REAL_STATUS",
                current_stage="NONE",
                lifecycle_status="OPEN",
            )
        )


def test_fastapi_uses_postgres_when_database_url_is_configured(
    committed_postgres_repository: PostgresIntakeRepository,
) -> None:
    del committed_postgres_repository
    runtime_url = os.environ["RGA_TEST_DATABASE_URL"]
    application = create_app(
        settings=ApplicationSettings(database_url=runtime_url, _env_file=None),
        reservation_authorizer=InMemoryReservationAuthorizer(
            {"reservation-api": "guest-api"}
        ),
        authenticator=SyntheticBearerAuthenticator({"token-api": "guest-api"}),
    )
    key = str(uuid4())
    headers = {
        "Authorization": "Bearer token-api",
        "Idempotency-Key": key,
    }
    body = {
        "reservation_reference": "reservation-api",
        "original_message": "Please refund this reservation.",
        "selected_request_types": ["REFUND"],
    }

    with TestClient(application) as client:
        created = client.post("/v1/intakes", json=body, headers=headers)
        status_response = client.get("/v1/intakes/status", headers=headers)

    assert created.status_code == 202
    assert status_response.status_code == 200
    assert status_response.json() == created.json()
