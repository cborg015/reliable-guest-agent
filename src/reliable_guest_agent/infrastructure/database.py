from __future__ import annotations

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    MetaData,
    PrimaryKeyConstraint,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.engine import make_url

from reliable_guest_agent.domain.enums import (
    LifecycleStatus,
    OutboxEventStatus,
    OutboxEventType,
    ProcessingStage,
    ProcessingStatus,
    RequestType,
)

SCHEMA_NAME = "reliable_guest_agent"
IDEMPOTENCY_UNIQUE_CONSTRAINT = "uq_idempotency_records_guest_key"
EXPECTED_TEST_DATABASE = "reliable_guest_agent_test"

metadata = MetaData(schema=SCHEMA_NAME)


class UnsafeTestDatabaseError(RuntimeError):
    """Raised before a destructive reset targets a non-test database."""


def require_safe_test_database(configured_url: str, actual_database_name: str) -> None:
    configured_name = make_url(configured_url).database
    if configured_name != EXPECTED_TEST_DATABASE:
        raise UnsafeTestDatabaseError(
            f"Configured database must be {EXPECTED_TEST_DATABASE!r}; got {configured_name!r}"
        )
    if actual_database_name != EXPECTED_TEST_DATABASE:
        raise UnsafeTestDatabaseError(
            f"PostgreSQL reported {actual_database_name!r}, not the dedicated test database"
        )


def _allowed_values(values: list[str]) -> str:
    return ", ".join(f"'{value}'" for value in values)


inbound_messages = Table(
    "inbound_messages",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("reservation_reference", Text, nullable=False),
    Column("sender_reference", Text, nullable=False),
    Column("original_text", Text, nullable=False),
    Column("received_at", DateTime(timezone=True), nullable=False),
    CheckConstraint(
        "btrim(reservation_reference) <> ''",
        name="ck_inbound_messages_reservation_reference_nonempty",
    ),
    CheckConstraint(
        "btrim(sender_reference) <> ''",
        name="ck_inbound_messages_sender_reference_nonempty",
    ),
    CheckConstraint(
        "btrim(original_text) <> ''",
        name="ck_inbound_messages_original_text_nonempty",
    ),
)

inbound_message_request_types = Table(
    "inbound_message_request_types",
    metadata,
    Column(
        "message_id",
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.inbound_messages.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("request_type", String(64), nullable=False),
    PrimaryKeyConstraint("message_id", "request_type"),
    CheckConstraint(
        f"request_type IN ({_allowed_values([item.value for item in RequestType])})",
        name="ck_inbound_message_request_types_value",
    ),
)

cases = Table(
    "cases",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column(
        "message_id",
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.inbound_messages.id"),
        nullable=False,
        unique=True,
    ),
    Column("processing_status", String(32), nullable=False),
    Column("current_stage", String(32), nullable=False),
    Column("lifecycle_status", String(32), nullable=False),
    CheckConstraint(
        f"processing_status IN "
        f"({_allowed_values([item.value for item in ProcessingStatus])})",
        name="ck_cases_processing_status_value",
    ),
    CheckConstraint(
        f"current_stage IN ({_allowed_values([item.value for item in ProcessingStage])})",
        name="ck_cases_current_stage_value",
    ),
    CheckConstraint(
        f"lifecycle_status IN "
        f"({_allowed_values([item.value for item in LifecycleStatus])})",
        name="ck_cases_lifecycle_status_value",
    ),
)

outbox_events = Table(
    "outbox_events",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column(
        "case_id",
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.cases.id"),
        nullable=False,
    ),
    Column("event_type", String(64), nullable=False),
    Column("status", String(32), nullable=False),
    Column("attempt_count", Integer, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    CheckConstraint(
        f"event_type IN ({_allowed_values([item.value for item in OutboxEventType])})",
        name="ck_outbox_events_event_type_value",
    ),
    CheckConstraint(
        f"status IN ({_allowed_values([item.value for item in OutboxEventStatus])})",
        name="ck_outbox_events_status_value",
    ),
    CheckConstraint("attempt_count >= 0", name="ck_outbox_events_attempt_count_nonnegative"),
)

idempotency_records = Table(
    "idempotency_records",
    metadata,
    Column("guest_id", Text, nullable=False),
    Column("idempotency_key", Text, nullable=False),
    Column("request_payload_hash", String(64), nullable=False),
    Column("message_id", UUID(as_uuid=True), nullable=False),
    Column("case_id", UUID(as_uuid=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    PrimaryKeyConstraint(
        "guest_id",
        "idempotency_key",
        name=IDEMPOTENCY_UNIQUE_CONSTRAINT,
    ),
    UniqueConstraint("message_id", name="uq_idempotency_records_message_id"),
    UniqueConstraint("case_id", name="uq_idempotency_records_case_id"),
    ForeignKeyConstraint(
        ["message_id"],
        [f"{SCHEMA_NAME}.inbound_messages.id"],
        name="fk_idempotency_records_message_id",
    ),
    ForeignKeyConstraint(
        ["case_id"],
        [f"{SCHEMA_NAME}.cases.id"],
        name="fk_idempotency_records_case_id",
    ),
    CheckConstraint("btrim(guest_id) <> ''", name="ck_idempotency_records_guest_id_nonempty"),
    CheckConstraint(
        "btrim(idempotency_key) <> ''",
        name="ck_idempotency_records_key_nonempty",
    ),
    CheckConstraint(
        "request_payload_hash ~ '^[0-9a-f]{64}$'",
        name="ck_idempotency_records_payload_hash_sha256",
    ),
)
