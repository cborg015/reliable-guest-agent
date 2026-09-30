# Architecture decisions

## ADR-001: REST API with generated Swagger documentation

**Status:** Accepted

FastAPI provides a testable HTTP boundary and interactive documentation without
requiring frontend development. Domain and workflow logic remain independent of
the HTTP layer.

## ADR-002: AI provides evidence-backed interpretation, not decisions

**Status:** Accepted

The model proposes structured request items, claimed reasons, missing
information, and supporting evidence. Hosts or support make all final business
decisions. This boundary is enforced below the API and model layers.

## ADR-003: Deterministic redaction precedes model access

**Status:** Accepted

The original guest message is stored for authorized human review but never sent
to a model provider. Typed placeholders preserve useful sentence structure.
Uncertain redaction fails closed and bypasses AI.

## ADR-004: Local and deterministic model providers

**Status:** Accepted

Automated tests use a deterministic provider requiring no GPU, network, or
secret. Interactive development will use a quantized local model suitable for a
6 GB NVIDIA GPU. Provider interfaces prevent vendor coupling.

## ADR-005: Atomic intake with transactional outbox

**Status:** Accepted

The inbound message, empty case shell, and processing-requested outbox event are
committed atomically. The API returns both identifiers only after commit.
Idempotency keys deduplicate caller retries, and workers tolerate at-least-once
event delivery.

The frontend generates and temporarily retains each key before submission. The
backend stores the guest-scoped key, a canonical request-payload hash, and the
resulting identifiers in the intake transaction. An identical replay returns
the original result; reuse with a different payload is rejected with
`409 Conflict`. No separate key-reservation request is required.

## ADR-006: Hybrid synchronous/asynchronous execution

**Status:** Accepted

Authentication, primary-booker authorization, and intake persistence are
synchronous. Redaction, AI interpretation, evidence validation, context
retrieval, and retries execute asynchronously from durable checkpoints.

## ADR-007: Orthogonal state dimensions

**Status:** Accepted

Processing progress, business lifecycle, current stage, request-item status,
ownership, and the actor blocking progress are modeled separately. Derived case
resolution and bottlenecks are calculated from request items.

## ADR-008: Request-item ownership and waiting actor are distinct

**Status:** Accepted

Each request item stores `assigned_to` for accountable ownership and
`waiting_on` for the actor whose next action is required. Independent items do
not block one another.

## ADR-009: Per-stage retries resume from checkpoints

**Status:** Accepted

Retry counters and policies are stage-specific. Transient failures retry with
backoff, while missing or conflicting business data goes to support. Completed
AI work is reused when downstream processing fails.

## ADR-010: Build one vertical slice before platform expansion

**Status:** Accepted

The first slice proves reliable intake through actionable host review using
synthetic data. External brokers, real platform integrations, Kubernetes, and
advanced observability are added only after the vertical slice establishes a
concrete need.

## ADR-011: Immutable domain objects with snapshot persistence

**Status:** Accepted

Domain entities are immutable. Business transitions return a new in-memory
object while preserving its identity. Persistence adapters update the existing
database row rather than inserting a complete copy for every transition. A
version column will provide optimistic concurrency control so competing host,
guest, support, or worker updates fail explicitly instead of overwriting one
another. Audit history remains a separate concern and can be added selectively
without requiring event sourcing.

## ADR-012: Prove intake behavior with an in-memory adapter first

**Status:** Accepted

Workflow correctness is the highest-risk unknown for the first milestone. A
thread-safe, copy-on-write adapter therefore proves atomic create-or-replay,
payload-conflict detection, complete rollback, and owner-scoped status lookup
before database setup is introduced. The repository interface keeps the
application service independent of this adapter. PostgreSQL remains required
to prove real transactional and concurrency guarantees in the next persistence
milestone.

## ADR-013: Authorize the primary booking account before intake persistence

**Status:** Accepted (supersedes the authorization timing in the original
intake workflow)

The API authenticates a synthetic bearer token and verifies that its canonical
guest identity matches the reservation's booking account before storing message
text or creating a case. Other listed guests are not authorized in v1 because
case data is primarily tied to the booking account. Missing reservations and
ownership mismatches share a generic `404` response to prevent enumeration.
Dependency outages return `503`. Host, listing, and policy conditions remain
background eligibility or routing concerns rather than authorization gates.

Successful intake returns `202 Accepted`: the records are durable, but the
guest-visible processing workflow remains incomplete.

## ADR-014: Classify committed replay before mutable authorization

**Status:** Accepted (refines the sequencing in ADR-013 for committed replays)

Every intake request still requires authentication and request validation. The
application then calculates a canonical payload hash and asks the repository to
classify the guest-scoped idempotency key before contacting the reservation
service. A matching committed record returns only the original intake receipt.
A conflicting payload returns `409 Conflict`. Reservation authorization runs
only after the repository confirms that no committed record exists.

This ordering makes recovery deterministic when reservation data changes or the
reservation service is temporarily unavailable. Returning a receipt does not
authorize access to full case data; future case endpoints must independently
authorize the caller. POST replays return the immutable original `PROCESSING`
receipt, while the status endpoint remains responsible for current state.

The repository owns replay-versus-conflict classification so stored hashes and
record details do not cross the persistence boundary. The atomic
`create_or_replay` operation repeats classification because concurrent requests
may both observe an initial miss. This second check preserves the single
guest-and-key mapping under races.

Canonical hashing sorts request types and applies deterministic, narrowly scoped
Unicode and whitespace normalization to message text. It preserves case,
punctuation, spelling, numbers, wording, and meaning. The immutable original
message remains unchanged in storage. Semantic deduplication is a separate
concern and is excluded from intake idempotency because probabilistic matching
could silently discard a changed request and would conflict with the pre-model
privacy boundary.

An unavailable idempotency repository is not treated as a missing record. The
operation fails closed before reservation authorization or writes, and the API
returns `503 Service Unavailable` with `Retry-After: 10`. Status-lookup outages
use the same response contract. Repository adapters translate recognized
storage-availability failures into `IntakeRepositoryUnavailableError`; unknown
programming and data-integrity failures remain visible as distinct failures.

## ADR-015: PostgreSQL persistence with explicit relational boundaries

**Status:** Accepted

The production-oriented adapter uses synchronous SQLAlchemy Core with psycopg
and versioned Alembic migrations. Persistence tables remain separate from the
framework-independent domain models. Fixed values use readable text columns
with named `CHECK` constraints rather than PostgreSQL-native enums. PostgreSQL
enforces structural and deterministic row invariants; Python continues to own
authorization, replay classification, workflow transitions, privacy behavior,
and human decision authority.

The idempotency record solely owns the guest-scoped key and references its
message and case with foreign keys. Guest-selected request types are immutable
child rows with a composite primary key because their order is irrelevant. The
message does not duplicate the idempotency key.

All intake rows commit in one `READ COMMITTED` transaction. Concurrent initial
misses use optimistic concurrency: a named guest-and-key constraint selects the
winner, the loser rolls back completely, and the repository rereads the winner
to classify replay or conflict. Only that named constraint is handled as an
idempotency race; other integrity errors remain visible.

Local PostgreSQL runs as a visible Docker Compose service. Alembic uses a
migration role that owns schema changes, while the API uses a least-privileged
runtime role. Tests use a dedicated database and refuse schema reset unless both
the configured URL and PostgreSQL's reported database name equal
`reliable_guest_agent_test`. Ordinary adapter tests use rollback isolation;
commit-sensitive concurrency tests use separate connections and explicit
cleanup. The same behavioral contract runs against the in-memory and PostgreSQL
adapters.

All current data is synthetic. Field-level encryption and production key
management are deliberately deferred rather than represented by an incomplete
key boundary. The local database is bound to loopback, original messages must
not be logged, and direct database or backup compromise remains outside the
current protection boundary.
