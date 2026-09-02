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
