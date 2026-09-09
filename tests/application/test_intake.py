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
    IntakeRepositoryUnavailableError,
    ReservationAccessDeniedError,
    ReservationServiceUnavailableError,
)
from reliable_guest_agent.domain.enums import ProcessingStatus, RequestType
from reliable_guest_agent.domain.errors import DomainInvariantError
from reliable_guest_agent.infrastructure.memory import (
    InMemoryIntakeRepository,
    InMemoryReservationAuthorizer,
    SimulatedOutboxWriteError,
)


class CountingReservationAuthorizer(InMemoryReservationAuthorizer):
    def __init__(self, booking_guests: dict[str, str]) -> None:
        super().__init__(booking_guests)
        self.call_count = 0

    def require_booking_guest(self, *, guest_id: str, reservation_reference: str) -> None:
        self.call_count += 1
        super().require_booking_guest(
            guest_id=guest_id,
            reservation_reference=reservation_reference,
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
        InMemoryReservationAuthorizer({"reservation-456": "guest-123"}),
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


def test_matching_replay_does_not_repeat_reservation_authorization(
    repository: InMemoryIntakeRepository,
) -> None:
    authorizer = CountingReservationAuthorizer({"reservation-456": "guest-123"})
    use_case = IntakeGuestMessage(repository, authorizer)
    first = use_case.execute(make_command())
    authorizer.unavailable = True

    replay = use_case.execute(make_command())

    assert replay.message_id == first.message_id
    assert replay.case_id == first.case_id
    assert replay.processing_status is first.processing_status
    assert replay.replayed is True
    assert authorizer.call_count == 1


def test_conflicting_replay_is_rejected_before_reservation_authorization(
    repository: InMemoryIntakeRepository,
) -> None:
    authorizer = CountingReservationAuthorizer({"reservation-456": "guest-123"})
    use_case = IntakeGuestMessage(repository, authorizer)
    use_case.execute(make_command())
    authorizer.unavailable = True

    with pytest.raises(IdempotencyConflictError, match="different payload"):
        use_case.execute(make_command(original_message="Please transfer the reservation."))

    assert authorizer.call_count == 1
    assert repository.counts == (1, 1, 1, 1)


def test_unavailable_replay_lookup_fails_closed_before_authorization_or_write(
    repository: InMemoryIntakeRepository,
) -> None:
    authorizer = CountingReservationAuthorizer({"reservation-456": "guest-123"})
    use_case = IntakeGuestMessage(repository, authorizer)
    repository.fail_next_replay_lookup = True

    with pytest.raises(IntakeRepositoryUnavailableError, match="temporarily unavailable"):
        use_case.execute(make_command())

    assert authorizer.call_count == 0
    assert repository.counts == (0, 0, 0, 0)


def test_command_validation_precedes_replay_lookup(
    repository: InMemoryIntakeRepository,
) -> None:
    repository.fail_next_replay_lookup = True
    use_case = IntakeGuestMessage(
        repository,
        InMemoryReservationAuthorizer({"reservation-456": "guest-123"}),
    )

    with pytest.raises(DomainInvariantError, match="original_message must not be empty"):
        use_case.execute(make_command(original_message=" "))

    assert repository.fail_next_replay_lookup is True
    assert repository.counts == (0, 0, 0, 0)


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


def test_request_type_order_is_ignored_for_idempotency(
    repository: InMemoryIntakeRepository,
    use_case: IntakeGuestMessage,
) -> None:
    first = use_case.execute(
        make_command(
            selected_request_types=(RequestType.REFUND, RequestType.RESERVATION_TRANSFER)
        )
    )

    replay = use_case.execute(
        make_command(
            selected_request_types=(RequestType.RESERVATION_TRANSFER, RequestType.REFUND)
        )
    )

    assert replay.message_id == first.message_id
    assert replay.case_id == first.case_id
    assert replay.replayed is True
    assert repository.counts == (1, 1, 1, 1)


def test_approved_unicode_and_whitespace_variations_are_idempotent(
    repository: InMemoryIntakeRepository,
    use_case: IntakeGuestMessage,
) -> None:
    first = use_case.execute(
        make_command(original_message="  Cafe\u0301   refund\r\n\r\n\r\nplease.  ")
    )

    replay = use_case.execute(make_command(original_message="Caf\u00e9 refund\n\nplease."))

    assert replay.message_id == first.message_id
    assert replay.case_id == first.case_id
    assert replay.replayed is True
    assert repository.counts == (1, 1, 1, 1)


@pytest.mark.parametrize(
    "changed_message",
    [
        "Please Refund the reservation.",
        "Please refund the reservation!",
        "Please refund 2 reservations.",
        "Please refnd the reservation.",
        "Please do not refund the reservation.",
    ],
)
def test_meaningful_message_changes_are_idempotency_conflicts(
    repository: InMemoryIntakeRepository,
    use_case: IntakeGuestMessage,
    changed_message: str,
) -> None:
    use_case.execute(make_command())

    with pytest.raises(IdempotencyConflictError, match="different payload"):
        use_case.execute(make_command(original_message=changed_message))

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


def test_status_lookup_reports_repository_unavailability(
    repository: InMemoryIntakeRepository,
) -> None:
    repository.fail_next_status_lookup = True

    with pytest.raises(IntakeRepositoryUnavailableError, match="temporarily unavailable"):
        CheckIntakeStatus(repository).execute(
            guest_id="guest-123",
            idempotency_key="018f-idempotency-key",
        )


def test_unauthorized_guest_is_rejected_before_any_intake_is_stored(
    repository: InMemoryIntakeRepository,
) -> None:
    use_case = IntakeGuestMessage(
        repository,
        InMemoryReservationAuthorizer({"reservation-456": "guest-999"}),
    )

    with pytest.raises(ReservationAccessDeniedError):
        use_case.execute(make_command())

    assert repository.counts == (0, 0, 0, 0)


def test_reservation_outage_is_rejected_before_any_intake_is_stored(
    repository: InMemoryIntakeRepository,
) -> None:
    authorizer = InMemoryReservationAuthorizer({"reservation-456": "guest-123"})
    authorizer.unavailable = True
    use_case = IntakeGuestMessage(repository, authorizer)

    with pytest.raises(ReservationServiceUnavailableError):
        use_case.execute(make_command())

    assert repository.counts == (0, 0, 0, 0)
