from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from uuid import UUID

from reliable_guest_agent.domain.enums import (
    Actor,
    DerivedResolution,
    EvidenceStatus,
    LifecycleStatus,
    OutboxEventStatus,
    OutboxEventType,
    ProcessingStage,
    ProcessingStatus,
    RequestItemStatus,
    RequestType,
)
from reliable_guest_agent.domain.errors import DomainInvariantError, InvalidTransitionError


@dataclass(frozen=True, slots=True)
class Evidence:
    excerpt: str
    status: EvidenceStatus

    def __post_init__(self) -> None:
        if self.status is EvidenceStatus.VERIFIED and not self.excerpt.strip():
            raise DomainInvariantError("Verified evidence requires a non-empty excerpt")


@dataclass(frozen=True, slots=True)
class InboundMessage:
    id: UUID
    reservation_reference: str
    sender_reference: str
    original_text: str
    selected_request_types: tuple[RequestType, ...]
    idempotency_key: str
    received_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        required_values = {
            "reservation_reference": self.reservation_reference,
            "sender_reference": self.sender_reference,
            "original_text": self.original_text,
            "idempotency_key": self.idempotency_key,
        }
        for name, value in required_values.items():
            if not value.strip():
                raise DomainInvariantError(f"{name} must not be empty")
        if not self.selected_request_types:
            raise DomainInvariantError("At least one request type must be selected")
        if len(set(self.selected_request_types)) != len(self.selected_request_types):
            raise DomainInvariantError("Selected request types must not contain duplicates")


@dataclass(frozen=True, slots=True)
class RequestItem:
    id: UUID
    request_type: RequestType
    status: RequestItemStatus = RequestItemStatus.PENDING
    assigned_to: Actor = Actor.HOST
    waiting_on: Actor = Actor.HOST
    evidence: tuple[Evidence, ...] = ()
    information_request_count: int = 0
    response_deadline: datetime | None = None
    escalation_reason: str | None = None
    escalated_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.assigned_to not in {Actor.HOST, Actor.SUPPORT}:
            raise DomainInvariantError("Request items must be assigned to HOST or SUPPORT")
        if self.information_request_count < 0:
            raise DomainInvariantError("information_request_count must not be negative")
        if self.status is RequestItemStatus.MORE_INFO_REQUESTED:
            if self.waiting_on is not Actor.GUEST:
                raise DomainInvariantError("MORE_INFO_REQUESTED must wait on GUEST")
            if self.response_deadline is None:
                raise DomainInvariantError("MORE_INFO_REQUESTED requires a response deadline")
        elif self.status.is_terminal:
            if self.waiting_on is not Actor.NONE:
                raise DomainInvariantError("Terminal request items must wait on NONE")
        elif self.status is RequestItemStatus.PENDING and self.waiting_on is not self.assigned_to:
            raise DomainInvariantError("PENDING request items must wait on their owner")

    def request_more_information(self, deadline: datetime) -> RequestItem:
        if self.status.is_terminal:
            raise InvalidTransitionError("Cannot request information for a terminal item")
        if deadline <= datetime.now(UTC):
            raise DomainInvariantError("Response deadline must be in the future")
        return replace(
            self,
            status=RequestItemStatus.MORE_INFO_REQUESTED,
            waiting_on=Actor.GUEST,
            information_request_count=self.information_request_count + 1,
            response_deadline=deadline,
        )

    def receive_guest_information(self) -> RequestItem:
        if self.status is not RequestItemStatus.MORE_INFO_REQUESTED:
            raise InvalidTransitionError("Item is not waiting for guest information")
        return replace(
            self,
            status=RequestItemStatus.PENDING,
            waiting_on=self.assigned_to,
            response_deadline=None,
        )

    def transfer_to_support(
        self,
        *,
        reason: str,
        transferred_at: datetime | None = None,
        preserve_guest_wait: bool = False,
    ) -> RequestItem:
        if self.status.is_terminal:
            raise InvalidTransitionError("Cannot transfer a terminal item")
        if not reason.strip():
            raise DomainInvariantError("Support transfer requires a reason")
        still_waiting_for_guest = (
            preserve_guest_wait and self.status is RequestItemStatus.MORE_INFO_REQUESTED
        )
        return replace(
            self,
            status=(
                RequestItemStatus.MORE_INFO_REQUESTED
                if still_waiting_for_guest
                else RequestItemStatus.PENDING
            ),
            assigned_to=Actor.SUPPORT,
            waiting_on=Actor.GUEST if still_waiting_for_guest else Actor.SUPPORT,
            response_deadline=self.response_deadline if still_waiting_for_guest else None,
            escalation_reason=reason,
            escalated_at=transferred_at or datetime.now(UTC),
        )

    def resolve(self, resolution: RequestItemStatus) -> RequestItem:
        if not resolution.is_terminal:
            raise InvalidTransitionError("Resolution must be a terminal request-item status")
        if self.status.is_terminal:
            raise InvalidTransitionError("Request item is already terminal")
        return replace(
            self,
            status=resolution,
            waiting_on=Actor.NONE,
            response_deadline=None,
        )


@dataclass(frozen=True, slots=True)
class Case:
    id: UUID
    message_id: UUID
    processing_status: ProcessingStatus = ProcessingStatus.NOT_STARTED
    current_stage: ProcessingStage = ProcessingStage.NONE
    lifecycle_status: LifecycleStatus = LifecycleStatus.OPEN
    request_items: tuple[RequestItem, ...] = ()

    def __post_init__(self) -> None:
        if (
            self.processing_status is ProcessingStatus.IN_PROGRESS
            and self.current_stage is ProcessingStage.NONE
        ):
            raise DomainInvariantError("IN_PROGRESS cases require a current processing stage")
        if (
            self.processing_status in {ProcessingStatus.NOT_STARTED, ProcessingStatus.COMPLETED}
            and self.current_stage is not ProcessingStage.NONE
        ):
            raise DomainInvariantError(
                f"{self.processing_status} cases must have current_stage=NONE"
            )
        if (
            self.processing_status is ProcessingStatus.FAILED
            and self.current_stage is ProcessingStage.NONE
        ):
            raise DomainInvariantError("FAILED cases must preserve the stage that failed")
        if self.lifecycle_status is LifecycleStatus.CLOSED and not self.is_resolved:
            raise DomainInvariantError("A case can close only when every request item is terminal")

    @property
    def derived_resolution(self) -> DerivedResolution:
        if not self.request_items:
            return DerivedResolution.UNRESOLVED
        terminal_count = sum(item.status.is_terminal for item in self.request_items)
        if terminal_count == 0:
            return DerivedResolution.UNRESOLVED
        if terminal_count == len(self.request_items):
            return DerivedResolution.RESOLVED
        return DerivedResolution.PARTIALLY_RESOLVED

    @property
    def is_resolved(self) -> bool:
        return self.derived_resolution is DerivedResolution.RESOLVED

    @property
    def waiting_on_actors(self) -> frozenset[Actor]:
        return frozenset(
            item.waiting_on for item in self.request_items if item.waiting_on is not Actor.NONE
        )


@dataclass(frozen=True, slots=True)
class OutboxEvent:
    id: UUID
    case_id: UUID
    event_type: OutboxEventType = OutboxEventType.CASE_PROCESSING_REQUESTED
    status: OutboxEventStatus = OutboxEventStatus.PENDING
    attempt_count: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if self.attempt_count < 0:
            raise DomainInvariantError("attempt_count must not be negative")
