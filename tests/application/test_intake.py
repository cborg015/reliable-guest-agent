from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from itertools import count
from uuid import UUID

import pytest

from reliable_guest_agent.application.intake import (
    CheckIntakeStatus,
    IdempotencyConflictError,
    IntakeCommand,
    IntakeGuestMessage,
)
from reliable_guest_agent.domain.enums import ProcessingStatus, RequestType
from reliable_guest_agent.infrastructure.memory import (
    InMemoryIntakeRepository,
    SimulatedOutboxWriteError,
)


def sequential_ids() -> Callable[[], UUID]:
    values = count(1)
    return lambda: UUID(int=next(values))


def make_command(**overrides: object) -> IntakeCommand:
    values = {
        "guest_id": "guest-123",
        "reservation_reference": "reservation-456",
        "original_message": "Please refund the reservation.",
        "selected_request_types": (RequestType.REFUND,),
        "idempotency_key": "018f-idempotency-key",
    }
    values.update(overrides)
    return IntakeCommand(**values)  # type: ignore[arg-type]


@pytest.fixture
def repository() -> InMemoryIntakeRepository:
    return InMemoryIntakeRepository()


@pytest.fixture
def use_case(repository: InMemoryIntakeRepository) -> IntakeGuestMessage:
    return IntakeGuestMessage(
        repository,
        id_factory=sequential_ids(),
        clock=lambda: datetime(2026, 8, 31, 12, 0, tzinfo=UTC),
    )


def test_intake_atomically_creates_all_records(
    repository: InMemoryIntakeRepository,
    use_case: IntakeGuestMessage,
) -> None:
    result = use_case.execute(make_command())

    assert result.message_id == UUID(int=1)
    assert result.case_id == UUID(int=2)
    assert result.processing_status is ProcessingStatus.NOT_STARTED
    assert result.replayed is False
    assert repository.counts == (1, 1, 1, 1)


def test_identical_replay_returns_original_ids_without_duplicates(
    repository: InMemoryIntakeRepository,
    use_case: IntakeGuestMessage,
) -> None:
    first = use_case.execute(make_command())
    replay = use_case.execute(make_command())

    assert replay.message_id == first.message_id
    assert replay.case_id == first.case_id
    assert replay.replayed is True
    assert repository.counts == (1, 1, 1, 1)


def test_concurrent_replays_create_only_one_intake(
    repository: InMemoryIntakeRepository,
    use_case: IntakeGuestMessage,
) -> None:
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _: use_case.execute(make_command()), range(20)))

    assert len({result.message_id for result in results}) == 1
    assert len({result.case_id for result in results}) == 1
    assert sum(not result.replayed for result in results) == 1
    assert repository.counts == (1, 1, 1, 1)


def test_same_key_with_changed_payload_is_rejected(
    repository: InMemoryIntakeRepository,
    use_case: IntakeGuestMessage,
) -> None:
    use_case.execute(make_command())

    with pytest.raises(IdempotencyConflictError, match="different payload"):
        use_case.execute(make_command(original_message="Please transfer the reservation."))

    assert repository.counts == (1, 1, 1, 1)


def test_outbox_failure_rolls_back_entire_intake(
    repository: InMemoryIntakeRepository,
    use_case: IntakeGuestMessage,
) -> None:
    repository.fail_next_outbox_write = True

    with pytest.raises(SimulatedOutboxWriteError):
        use_case.execute(make_command())

    assert repository.counts == (0, 0, 0, 0)


def test_retry_after_rollback_creates_one_complete_intake(
    repository: InMemoryIntakeRepository,
    use_case: IntakeGuestMessage,
) -> None:
    repository.fail_next_outbox_write = True
    with pytest.raises(SimulatedOutboxWriteError):
        use_case.execute(make_command())

    result = use_case.execute(make_command())

    assert result.replayed is False
    assert repository.counts == (1, 1, 1, 1)


def test_status_lookup_returns_committed_intake_for_owner(
    repository: InMemoryIntakeRepository,
    use_case: IntakeGuestMessage,
) -> None:
    created = use_case.execute(make_command())

    found = CheckIntakeStatus(repository).execute(
        guest_id="guest-123",
        idempotency_key="018f-idempotency-key",
    )

    assert found is not None
    assert found.message_id == created.message_id
    assert found.case_id == created.case_id


def test_status_lookup_does_not_disclose_another_guests_intake(
    repository: InMemoryIntakeRepository,
    use_case: IntakeGuestMessage,
) -> None:
    use_case.execute(make_command())

    found = CheckIntakeStatus(repository).execute(
        guest_id="different-guest",
        idempotency_key="018f-idempotency-key",
    )

    assert found is None
