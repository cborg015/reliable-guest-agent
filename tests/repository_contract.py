from __future__ import annotations

from datetime import UTC, datetime
from itertools import count
from uuid import UUID

import pytest

from reliable_guest_agent.application.intake import (
    IdempotencyConflictError,
    IntakeCommand,
    IntakeGuestMessage,
    IntakeRepository,
)
from reliable_guest_agent.domain.enums import RequestType
from reliable_guest_agent.infrastructure.memory import InMemoryReservationAuthorizer


def assert_intake_repository_contract(repository: IntakeRepository) -> None:
    identifiers = count(1)
    use_case = IntakeGuestMessage(
        repository,
        InMemoryReservationAuthorizer({"reservation-contract": "guest-contract"}),
        id_factory=lambda: UUID(int=next(identifiers)),
        clock=lambda: datetime(2026, 9, 30, tzinfo=UTC),
    )
    command = IntakeCommand(
        guest_id="guest-contract",
        reservation_reference="reservation-contract",
        original_message="Please refund this reservation.",
        selected_request_types=(RequestType.REFUND,),
        idempotency_key="contract-key",
    )

    created = use_case.execute(command)
    replay = use_case.execute(command)

    assert replay.message_id == created.message_id
    assert replay.case_id == created.case_id
    assert replay.processing_status is created.processing_status
    assert replay.replayed is True

    with pytest.raises(IdempotencyConflictError, match="different payload"):
        use_case.execute(
            IntakeCommand(
                guest_id=command.guest_id,
                reservation_reference=command.reservation_reference,
                original_message="Please transfer this reservation.",
                selected_request_types=(RequestType.RESERVATION_TRANSFER,),
                idempotency_key=command.idempotency_key,
            )
        )

    status = repository.find_result(
        guest_id=command.guest_id,
        idempotency_key=command.idempotency_key,
    )
    assert status is not None
    assert status.message_id == created.message_id
    assert status.case_id == created.case_id
    assert (
        repository.find_result(
            guest_id="different-guest",
            idempotency_key=command.idempotency_key,
        )
        is None
    )
