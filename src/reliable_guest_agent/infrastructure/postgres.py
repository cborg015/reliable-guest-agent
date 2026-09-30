from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import Connection, Engine, RowMapping, insert, select
from sqlalchemy.exc import IntegrityError, OperationalError

from reliable_guest_agent.application.intake import (
    IdempotencyConflictError,
    IdempotencyRecord,
    IntakeRepositoryUnavailableError,
    IntakeResult,
)
from reliable_guest_agent.domain.enums import ProcessingStatus
from reliable_guest_agent.domain.models import Case, InboundMessage, OutboxEvent
from reliable_guest_agent.infrastructure.database import (
    IDEMPOTENCY_UNIQUE_CONSTRAINT,
    cases,
    idempotency_records,
    inbound_message_request_types,
    inbound_messages,
    outbox_events,
)


class PostgresIntakeRepository:
    def __init__(
        self,
        engine: Engine,
        *,
        connection: Connection | None = None,
        after_case_write: Callable[[], None] | None = None,
    ) -> None:
        self._engine = engine
        self._connection = connection
        self._after_case_write = after_case_write

    def find_replay(
        self,
        *,
        guest_id: str,
        idempotency_key: str,
        request_payload_hash: str,
    ) -> IntakeResult | None:
        try:
            with self._read_connection() as connection:
                row = connection.execute(
                    select(idempotency_records).where(
                        idempotency_records.c.guest_id == guest_id,
                        idempotency_records.c.idempotency_key == idempotency_key,
                    )
                ).mappings().one_or_none()
        except OperationalError as error:
            raise IntakeRepositoryUnavailableError(
                "Intake replay lookup is temporarily unavailable"
            ) from error
        return self._classify_replay(row, request_payload_hash=request_payload_hash)

    def create_or_replay(
        self,
        *,
        message: InboundMessage,
        case: Case,
        outbox_event: OutboxEvent,
        idempotency_record: IdempotencyRecord,
    ) -> IntakeResult:
        try:
            with self._write_connection() as connection:
                connection.execute(
                    insert(inbound_messages).values(
                        id=message.id,
                        reservation_reference=message.reservation_reference,
                        sender_reference=message.sender_reference,
                        original_text=message.original_text,
                        received_at=message.received_at,
                    )
                )
                connection.execute(
                    insert(inbound_message_request_types),
                    [
                        {"message_id": message.id, "request_type": request_type.value}
                        for request_type in message.selected_request_types
                    ],
                )
                connection.execute(
                    insert(cases).values(
                        id=case.id,
                        message_id=case.message_id,
                        processing_status=case.processing_status.value,
                        current_stage=case.current_stage.value,
                        lifecycle_status=case.lifecycle_status.value,
                    )
                )
                if self._after_case_write is not None:
                    self._after_case_write()
                connection.execute(
                    insert(outbox_events).values(
                        id=outbox_event.id,
                        case_id=outbox_event.case_id,
                        event_type=outbox_event.event_type.value,
                        status=outbox_event.status.value,
                        attempt_count=outbox_event.attempt_count,
                        created_at=outbox_event.created_at,
                    )
                )
                connection.execute(
                    insert(idempotency_records).values(
                        guest_id=idempotency_record.guest_id,
                        idempotency_key=idempotency_record.key,
                        request_payload_hash=idempotency_record.request_payload_hash,
                        message_id=idempotency_record.message_id,
                        case_id=idempotency_record.case_id,
                        created_at=idempotency_record.created_at,
                    )
                )
        except IntegrityError as error:
            if not self._is_idempotency_conflict(error):
                raise
            return self._read_after_conflict(idempotency_record)
        except OperationalError as error:
            raise IntakeRepositoryUnavailableError(
                "Intake persistence is temporarily unavailable"
            ) from error

        return IntakeResult(
            message_id=message.id,
            case_id=case.id,
            processing_status=case.processing_status,
            replayed=False,
        )

    def find_result(self, *, guest_id: str, idempotency_key: str) -> IntakeResult | None:
        statement = (
            select(
                idempotency_records.c.message_id,
                idempotency_records.c.case_id,
                cases.c.processing_status,
            )
            .join(cases, cases.c.id == idempotency_records.c.case_id)
            .where(
                idempotency_records.c.guest_id == guest_id,
                idempotency_records.c.idempotency_key == idempotency_key,
            )
        )
        try:
            with self._read_connection() as connection:
                row = connection.execute(statement).mappings().one_or_none()
        except OperationalError as error:
            raise IntakeRepositoryUnavailableError(
                "Intake status lookup is temporarily unavailable"
            ) from error
        if row is None:
            return None
        return IntakeResult(
            message_id=row["message_id"],
            case_id=row["case_id"],
            processing_status=ProcessingStatus(row["processing_status"]),
            replayed=True,
        )

    def _read_after_conflict(self, record: IdempotencyRecord) -> IntakeResult:
        replay = self.find_replay(
            guest_id=record.guest_id,
            idempotency_key=record.key,
            request_payload_hash=record.request_payload_hash,
        )
        if replay is None:
            raise IntakeRepositoryUnavailableError(
                "Concurrent intake result could not be confirmed"
            )
        return replay

    @staticmethod
    def _classify_replay(
        row: RowMapping | None,
        *,
        request_payload_hash: str,
    ) -> IntakeResult | None:
        if row is None:
            return None
        if row["request_payload_hash"] != request_payload_hash:
            raise IdempotencyConflictError(
                "Idempotency key was already used with a different payload"
            )
        return IntakeResult(
            message_id=row["message_id"],
            case_id=row["case_id"],
            processing_status=ProcessingStatus.NOT_STARTED,
            replayed=True,
        )

    @staticmethod
    def _is_idempotency_conflict(error: IntegrityError) -> bool:
        diagnostic: Any = getattr(error.orig, "diag", None)
        return getattr(diagnostic, "constraint_name", None) == IDEMPOTENCY_UNIQUE_CONSTRAINT

    @contextmanager
    def _read_connection(self) -> Iterator[Connection]:
        if self._connection is not None:
            yield self._connection
            return
        with self._engine.connect() as connection:
            yield connection

    @contextmanager
    def _write_connection(self) -> Iterator[Connection]:
        if self._connection is not None:
            with self._connection.begin_nested():
                yield self._connection
            return
        with self._engine.begin() as connection:
            yield connection
