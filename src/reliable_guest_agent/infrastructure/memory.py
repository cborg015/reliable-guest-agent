from __future__ import annotations

from threading import RLock
from uuid import UUID

from reliable_guest_agent.application.intake import (
    IdempotencyConflictError,
    IdempotencyRecord,
    IntakeResult,
    ReservationAccessDeniedError,
    ReservationServiceUnavailableError,
)
from reliable_guest_agent.domain.enums import ProcessingStatus
from reliable_guest_agent.domain.models import Case, InboundMessage, OutboxEvent


class SimulatedOutboxWriteError(RuntimeError):
    """Test-only failure used to prove that intake rolls back atomically."""


class InMemoryIntakeRepository:
    """Copy-on-write behavioral prototype of the future database transaction."""

    def __init__(self) -> None:
        self._messages: dict[UUID, InboundMessage] = {}
        self._cases: dict[UUID, Case] = {}
        self._outbox_events: dict[UUID, OutboxEvent] = {}
        self._idempotency_records: dict[tuple[str, str], IdempotencyRecord] = {}
        self._lock = RLock()
        self.fail_next_outbox_write = False

    def create_or_replay(
        self,
        *,
        message: InboundMessage,
        case: Case,
        outbox_event: OutboxEvent,
        idempotency_record: IdempotencyRecord,
    ) -> IntakeResult:
        lookup_key = (idempotency_record.guest_id, idempotency_record.key)
        with self._lock:
            existing = self._idempotency_records.get(lookup_key)
            if existing is not None:
                if existing.request_payload_hash != idempotency_record.request_payload_hash:
                    raise IdempotencyConflictError(
                        "Idempotency key was already used with a different payload"
                    )
                return IntakeResult(
                    message_id=existing.message_id,
                    case_id=existing.case_id,
                    processing_status=ProcessingStatus.NOT_STARTED,
                    replayed=True,
                )

            messages = dict(self._messages)
            cases = dict(self._cases)
            outbox_events = dict(self._outbox_events)
            records = dict(self._idempotency_records)

            messages[message.id] = message
            cases[case.id] = case
            if self.fail_next_outbox_write:
                self.fail_next_outbox_write = False
                raise SimulatedOutboxWriteError("Simulated outbox insertion failure")
            outbox_events[outbox_event.id] = outbox_event
            records[lookup_key] = idempotency_record

            self._messages = messages
            self._cases = cases
            self._outbox_events = outbox_events
            self._idempotency_records = records

            return IntakeResult(
                message_id=message.id,
                case_id=case.id,
                processing_status=case.processing_status,
                replayed=False,
            )

    def find_result(self, *, guest_id: str, idempotency_key: str) -> IntakeResult | None:
        with self._lock:
            record = self._idempotency_records.get((guest_id, idempotency_key))
            if record is None:
                return None
            case = self._cases[record.case_id]
            return IntakeResult(
                message_id=record.message_id,
                case_id=record.case_id,
                processing_status=case.processing_status,
                replayed=True,
            )

    @property
    def counts(self) -> tuple[int, int, int, int]:
        with self._lock:
            return (
                len(self._messages),
                len(self._cases),
                len(self._outbox_events),
                len(self._idempotency_records),
            )


class InMemoryReservationAuthorizer:
    def __init__(self, booking_guests: dict[str, str] | None = None) -> None:
        self._booking_guests = dict(booking_guests or {})
        self.unavailable = False

    def require_booking_guest(self, *, guest_id: str, reservation_reference: str) -> None:
        if self.unavailable:
            raise ReservationServiceUnavailableError(
                "Reservation authorization is temporarily unavailable"
            )
        if self._booking_guests.get(reservation_reference) != guest_id:
            raise ReservationAccessDeniedError(
                "Reservation was not found or guest is not authorized"
            )
