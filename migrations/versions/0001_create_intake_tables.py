"""Create reliable intake persistence tables.

Revision ID: 0001
Revises:
Create Date: 2026-09-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from reliable_guest_agent.domain.enums import (
    LifecycleStatus,
    OutboxEventStatus,
    OutboxEventType,
    ProcessingStage,
    ProcessingStatus,
    RequestType,
)
from reliable_guest_agent.infrastructure.database import (
    IDEMPOTENCY_UNIQUE_CONSTRAINT,
    SCHEMA_NAME,
)

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _allowed(values: list[str]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    op.execute(sa.schema.CreateSchema(SCHEMA_NAME))
    op.create_table(
        "inbound_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reservation_reference", sa.Text(), nullable=False),
        sa.Column("sender_reference", sa.Text(), nullable=False),
        sa.Column("original_text", sa.Text(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "btrim(reservation_reference) <> ''",
            name="ck_inbound_messages_reservation_reference_nonempty",
        ),
        sa.CheckConstraint(
            "btrim(sender_reference) <> ''",
            name="ck_inbound_messages_sender_reference_nonempty",
        ),
        sa.CheckConstraint(
            "btrim(original_text) <> ''",
            name="ck_inbound_messages_original_text_nonempty",
        ),
        sa.PrimaryKeyConstraint("id"),
        schema=SCHEMA_NAME,
    )
    op.create_table(
        "inbound_message_request_types",
        sa.Column("message_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("request_type", sa.String(length=64), nullable=False),
        sa.CheckConstraint(
            f"request_type IN ({_allowed([item.value for item in RequestType])})",
            name="ck_inbound_message_request_types_value",
        ),
        sa.ForeignKeyConstraint(
            ["message_id"],
            [f"{SCHEMA_NAME}.inbound_messages.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("message_id", "request_type"),
        schema=SCHEMA_NAME,
    )
    op.create_table(
        "cases",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("message_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("processing_status", sa.String(length=32), nullable=False),
        sa.Column("current_stage", sa.String(length=32), nullable=False),
        sa.Column("lifecycle_status", sa.String(length=32), nullable=False),
        sa.CheckConstraint(
            f"processing_status IN ({_allowed([item.value for item in ProcessingStatus])})",
            name="ck_cases_processing_status_value",
        ),
        sa.CheckConstraint(
            f"current_stage IN ({_allowed([item.value for item in ProcessingStage])})",
            name="ck_cases_current_stage_value",
        ),
        sa.CheckConstraint(
            f"lifecycle_status IN ({_allowed([item.value for item in LifecycleStatus])})",
            name="ck_cases_lifecycle_status_value",
        ),
        sa.ForeignKeyConstraint(["message_id"], [f"{SCHEMA_NAME}.inbound_messages.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("message_id"),
        schema=SCHEMA_NAME,
    )
    op.create_table(
        "outbox_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            f"event_type IN ({_allowed([item.value for item in OutboxEventType])})",
            name="ck_outbox_events_event_type_value",
        ),
        sa.CheckConstraint(
            f"status IN ({_allowed([item.value for item in OutboxEventStatus])})",
            name="ck_outbox_events_status_value",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name="ck_outbox_events_attempt_count_nonnegative",
        ),
        sa.ForeignKeyConstraint(["case_id"], [f"{SCHEMA_NAME}.cases.id"]),
        sa.PrimaryKeyConstraint("id"),
        schema=SCHEMA_NAME,
    )
    op.create_table(
        "idempotency_records",
        sa.Column("guest_id", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("request_payload_hash", sa.String(length=64), nullable=False),
        sa.Column("message_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "btrim(guest_id) <> ''",
            name="ck_idempotency_records_guest_id_nonempty",
        ),
        sa.CheckConstraint(
            "btrim(idempotency_key) <> ''",
            name="ck_idempotency_records_key_nonempty",
        ),
        sa.CheckConstraint(
            "request_payload_hash ~ '^[0-9a-f]{64}$'",
            name="ck_idempotency_records_payload_hash_sha256",
        ),
        sa.ForeignKeyConstraint(
            ["case_id"],
            [f"{SCHEMA_NAME}.cases.id"],
            name="fk_idempotency_records_case_id",
        ),
        sa.ForeignKeyConstraint(
            ["message_id"],
            [f"{SCHEMA_NAME}.inbound_messages.id"],
            name="fk_idempotency_records_message_id",
        ),
        sa.PrimaryKeyConstraint(
            "guest_id",
            "idempotency_key",
            name=IDEMPOTENCY_UNIQUE_CONSTRAINT,
        ),
        sa.UniqueConstraint("case_id", name="uq_idempotency_records_case_id"),
        sa.UniqueConstraint("message_id", name="uq_idempotency_records_message_id"),
        schema=SCHEMA_NAME,
    )
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rga_runtime') THEN
                GRANT USAGE ON SCHEMA {SCHEMA_NAME} TO rga_runtime;
                GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA {SCHEMA_NAME}
                    TO rga_runtime;
                ALTER DEFAULT PRIVILEGES IN SCHEMA {SCHEMA_NAME}
                    GRANT SELECT, INSERT, UPDATE ON TABLES TO rga_runtime;
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.execute(sa.schema.DropSchema(SCHEMA_NAME, cascade=True))
