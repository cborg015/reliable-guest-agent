from enum import StrEnum


class Actor(StrEnum):
    SYSTEM = "SYSTEM"
    HOST = "HOST"
    GUEST = "GUEST"
    SUPPORT = "SUPPORT"
    NONE = "NONE"


class ProcessingStatus(StrEnum):
    NOT_STARTED = "NOT_STARTED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class ProcessingStage(StrEnum):
    RESERVATION_VALIDATION = "RESERVATION_VALIDATION"
    REDACTION = "REDACTION"
    AI_INTERPRETATION = "AI_INTERPRETATION"
    EVIDENCE_VALIDATION = "EVIDENCE_VALIDATION"
    CONTEXT_RETRIEVAL = "CONTEXT_RETRIEVAL"
    NONE = "NONE"


class LifecycleStatus(StrEnum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"


class DerivedResolution(StrEnum):
    UNRESOLVED = "UNRESOLVED"
    PARTIALLY_RESOLVED = "PARTIALLY_RESOLVED"
    RESOLVED = "RESOLVED"


class RequestType(StrEnum):
    REFUND = "REFUND"
    RESERVATION_TRANSFER = "RESERVATION_TRANSFER"


class RequestItemStatus(StrEnum):
    PENDING = "PENDING"
    MORE_INFO_REQUESTED = "MORE_INFO_REQUESTED"
    ACCEPTED = "ACCEPTED"
    DENIED = "DENIED"
    WITHDRAWN = "WITHDRAWN"
    EXPIRED = "EXPIRED"

    @property
    def is_terminal(self) -> bool:
        return self in {
            RequestItemStatus.ACCEPTED,
            RequestItemStatus.DENIED,
            RequestItemStatus.WITHDRAWN,
            RequestItemStatus.EXPIRED,
        }


class EvidenceStatus(StrEnum):
    VERIFIED = "VERIFIED"
    UNVERIFIED = "UNVERIFIED"


class OutboxEventType(StrEnum):
    CASE_PROCESSING_REQUESTED = "CASE_PROCESSING_REQUESTED"


class OutboxEventStatus(StrEnum):
    PENDING = "PENDING"
    PUBLISHED = "PUBLISHED"
    FAILED = "FAILED"

