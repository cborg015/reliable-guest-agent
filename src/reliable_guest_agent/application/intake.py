from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid4

from reliable_guest_agent.domain.enums import ProcessingStatus, RequestType
from reliable_guest_agent.domain.errors import DomainInvariantError
from reliable_guest_agent.domain.models import Case, InboundMessage, OutboxEvent


class IdempotencyConflictError(Exception):
    """Raised when a key is reused for a different request payload."""


class ReservationAccessDeniedError(Exception):
    """Raised for both missing reservations and unauthorized guests."""


class ReservationServiceUnavailableError(Exception):
    """Raised when reservation authorization cannot currently be completed."""


@dataclass(frozen=True, slots=True)
class IntakeCommand:
    guest_id: str
    reservation_reference: str
    original_message: str
    selected_request_types: tuple[RequestType, ...]
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class IdempotencyRecord:
    guest_id: str
    key: str
    request_payload_hash: str
    message_id: UUID
    case_id: UUID
    created_at: datetime


@dataclass(frozen=True, slots=True)
class IntakeResult:
    message_id: UUID
    case_id: UUID
    processing_status: ProcessingStatus
    replayed: bool


class IntakeRepository(Protocol):
    def create_or_replay(
        self,
        *,
        message: InboundMessage,
        case: Case,
        outbox_event: OutboxEvent,
        idempotency_record: IdempotencyRecord,
    ) -> IntakeResult: ...

    def find_result(self, *, guest_id: str, idempotency_key: str) -> IntakeResult | None: ...


class ReservationAuthorizer(Protocol):
    def require_booking_guest(self, *, guest_id: str, reservation_reference: str) -> None: ...


def canonical_payload_hash(command: IntakeCommand) -> str:
    payload = {
        "guest_id": command.guest_id,
        "original_message": command.original_message,
        "reservation_reference": command.reservation_reference,
        "selected_request_types": sorted(item.value for item in command.selected_request_types),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


class IntakeGuestMessage:
    def __init__(
        self,
        repository: IntakeRepository,
        reservation_authorizer: ReservationAuthorizer,
        *,
        id_factory: Callable[[], UUID] = uuid4,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._repository = repository
        self._reservation_authorizer = reservation_authorizer
        self._id_factory = id_factory
        self._clock = clock

    def execute(self, command: IntakeCommand) -> IntakeResult:
        self._validate(command)
        self._reservation_authorizer.require_booking_guest(
            guest_id=command.guest_id,
            reservation_reference=command.reservation_reference,
        )
        created_at = self._clock()
        message_id = self._id_factory()
        case_id = self._id_factory()

        message = InboundMessage(
            id=message_id,
            reservation_reference=command.reservation_reference,
            sender_reference=command.guest_id,
            original_text=command.original_message,
            selected_request_types=command.selected_request_types,
            idempotency_key=command.idempotency_key,
            received_at=created_at,
        )
        case = Case(id=case_id, message_id=message_id)
        outbox_event = OutboxEvent(
            id=self._id_factory(),
            case_id=case_id,
            created_at=created_at,
        )
        record = IdempotencyRecord(
            guest_id=command.guest_id,
            key=command.idempotency_key,
            request_payload_hash=canonical_payload_hash(command),
            message_id=message_id,
            case_id=case_id,
            created_at=created_at,
        )
        return self._repository.create_or_replay(
            message=message,
            case=case,
            outbox_event=outbox_event,
            idempotency_record=record,
        )

    @staticmethod
    def _validate(command: IntakeCommand) -> None:
        values = {
            "guest_id": command.guest_id,
            "reservation_reference": command.reservation_reference,
            "original_message": command.original_message,
            "idempotency_key": command.idempotency_key,
        }
        for name, value in values.items():
            if not value.strip():
                raise DomainInvariantError(f"{name} must not be empty")
        if not command.selected_request_types:
            raise DomainInvariantError("At least one request type must be selected")
        if len(set(command.selected_request_types)) != len(command.selected_request_types):
            raise DomainInvariantError("Selected request types must not contain duplicates")


class CheckIntakeStatus:
    def __init__(self, repository: IntakeRepository) -> None:
        self._repository = repository

    def execute(self, *, guest_id: str, idempotency_key: str) -> IntakeResult | None:
        if not guest_id.strip() or not idempotency_key.strip():
            raise DomainInvariantError("guest_id and idempotency_key must not be empty")
        return self._repository.find_result(
            guest_id=guest_id,
            idempotency_key=idempotency_key,
        )
