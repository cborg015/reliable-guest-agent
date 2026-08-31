from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from reliable_guest_agent.domain.enums import (
    Actor,
    DerivedResolution,
    LifecycleStatus,
    ProcessingStage,
    ProcessingStatus,
    RequestItemStatus,
    RequestType,
)
from reliable_guest_agent.domain.errors import DomainInvariantError, InvalidTransitionError
from reliable_guest_agent.domain.models import Case, RequestItem


def make_item(**overrides: object) -> RequestItem:
    values = {
        "id": uuid4(),
        "request_type": RequestType.REFUND,
    }
    values.update(overrides)
    return RequestItem(**values)  # type: ignore[arg-type]


def test_pending_item_waits_on_its_owner() -> None:
    with pytest.raises(DomainInvariantError, match="wait on their owner"):
        make_item(waiting_on=Actor.GUEST)


def test_requesting_information_moves_bottleneck_to_guest() -> None:
    deadline = datetime.now(UTC) + timedelta(days=2)

    updated = make_item().request_more_information(deadline)

    assert updated.status is RequestItemStatus.MORE_INFO_REQUESTED
    assert updated.assigned_to is Actor.HOST
    assert updated.waiting_on is Actor.GUEST
    assert updated.information_request_count == 1
    assert updated.response_deadline == deadline


def test_guest_response_returns_item_to_owner() -> None:
    waiting = make_item().request_more_information(datetime.now(UTC) + timedelta(days=2))

    updated = waiting.receive_guest_information()

    assert updated.status is RequestItemStatus.PENDING
    assert updated.assigned_to is Actor.HOST
    assert updated.waiting_on is Actor.HOST
    assert updated.response_deadline is None


def test_support_can_take_ownership_while_still_waiting_for_guest() -> None:
    waiting = make_item().request_more_information(datetime.now(UTC) + timedelta(days=2))

    updated = waiting.transfer_to_support(
        reason="Host exceeded clarification limit",
        preserve_guest_wait=True,
    )

    assert updated.status is RequestItemStatus.MORE_INFO_REQUESTED
    assert updated.assigned_to is Actor.SUPPORT
    assert updated.waiting_on is Actor.GUEST


def test_support_transfer_can_make_support_the_next_actor() -> None:
    updated = make_item().transfer_to_support(reason="Policy conflict")

    assert updated.status is RequestItemStatus.PENDING
    assert updated.assigned_to is Actor.SUPPORT
    assert updated.waiting_on is Actor.SUPPORT


@pytest.mark.parametrize(
    "resolution",
    [
        RequestItemStatus.ACCEPTED,
        RequestItemStatus.DENIED,
        RequestItemStatus.WITHDRAWN,
        RequestItemStatus.EXPIRED,
    ],
)
def test_terminal_resolution_has_no_waiting_actor(resolution: RequestItemStatus) -> None:
    updated = make_item().resolve(resolution)

    assert updated.status is resolution
    assert updated.waiting_on is Actor.NONE


def test_terminal_item_cannot_transition_again() -> None:
    resolved = make_item().resolve(RequestItemStatus.DENIED)

    with pytest.raises(InvalidTransitionError, match="already terminal"):
        resolved.resolve(RequestItemStatus.ACCEPTED)


def test_case_derives_partial_resolution_and_bottlenecks() -> None:
    accepted = make_item().resolve(RequestItemStatus.ACCEPTED)
    waiting_for_guest = make_item(
        request_type=RequestType.RESERVATION_TRANSFER
    ).request_more_information(datetime.now(UTC) + timedelta(days=2))
    case = Case(id=uuid4(), message_id=uuid4(), request_items=(accepted, waiting_for_guest))

    assert case.derived_resolution is DerivedResolution.PARTIALLY_RESOLVED
    assert case.waiting_on_actors == frozenset({Actor.GUEST})


def test_case_cannot_close_with_unresolved_items() -> None:
    with pytest.raises(DomainInvariantError, match="every request item is terminal"):
        Case(
            id=uuid4(),
            message_id=uuid4(),
            lifecycle_status=LifecycleStatus.CLOSED,
            request_items=(make_item(),),
        )


def test_processing_case_requires_current_stage() -> None:
    with pytest.raises(DomainInvariantError, match="require a current processing stage"):
        Case(
            id=uuid4(),
            message_id=uuid4(),
            processing_status=ProcessingStatus.IN_PROGRESS,
        )


def test_failed_case_preserves_failed_stage() -> None:
    case = Case(
        id=uuid4(),
        message_id=uuid4(),
        processing_status=ProcessingStatus.FAILED,
        current_stage=ProcessingStage.CONTEXT_RETRIEVAL,
    )

    assert case.current_stage is ProcessingStage.CONTEXT_RETRIEVAL


def test_completed_case_has_no_current_processing_stage() -> None:
    with pytest.raises(DomainInvariantError, match="must have current_stage=NONE"):
        Case(
            id=uuid4(),
            message_id=uuid4(),
            processing_status=ProcessingStatus.COMPLETED,
            current_stage=ProcessingStage.AI_INTERPRETATION,
        )
