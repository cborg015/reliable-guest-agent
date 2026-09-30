# Reliable Guest Agent

A production-oriented AI workflow that transforms unstructured guest messages
into evidence-backed structured cases for host review. The AI may interpret and
organize a request, but it never makes or executes the host's decision.

The first vertical slice handles a same-day message containing refund and
reservation-transfer requests. It uses synthetic data and runs without a paid
model API key.

## Current implementation

The current local-first implementation provides:

- FastAPI entry point with generated Swagger documentation
- authenticated HTTP `POST /v1/intakes` and `GET /v1/intakes/status` endpoints
- demo bearer-token authentication with primary-booker authorization
- documented workflow, privacy boundary, state model, retries, and safety
  invariants
- framework-independent domain entities and legal state transitions
- PostgreSQL and in-memory intake repositories with atomic replay and rollback
- versioned Alembic migrations and a local Docker Compose database
- privacy-safe intake-status lookup scoped to the authenticated guest
- deterministic tests covering domain invariants and workflow failures

## Deferred work

The current implementation atomically creates a pending outbox event in
PostgreSQL, but it does not dispatch or process that event. The following
capabilities remain deferred until their contracts are defined and proven in
sequence:

- asynchronous outbox dispatch and workflow execution
- sensitive-information redaction
- AI interpretation
- policy retrieval
- host review

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate  # Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
cp .env.example .env       # Windows PowerShell: Copy-Item .env.example .env
docker compose up -d --wait postgres
alembic upgrade head
uvicorn reliable_guest_agent.api.main:app --reload
```

The checked-in database credentials are local synthetic defaults only. Docker
binds PostgreSQL to `127.0.0.1`. Alembic uses the migration role, while the API
uses a runtime role that cannot change the schema.

Open `http://127.0.0.1:8000/docs` for Swagger UI.

The local demo includes two synthetic bearer tokens. They are fixtures, not
secrets or production credentials:

- `demo-token-guest-123` authenticates the booking account for
  `reservation-456`.
- `demo-token-guest-999` authenticates a different guest and demonstrates the
  privacy-safe authorization failure.

In Swagger, call `POST /v1/intakes` with `demo-token-guest-123` in the bearer
authorization field, a UUID in `Idempotency-Key`, and this body:

```json
{
  "reservation_reference": "reservation-456",
  "original_message": "Could I receive a refund?",
  "selected_request_types": ["REFUND"]
}
```

## Test

Fast dependency-free tests:

```bash
pytest -m "not postgres"
```

PostgreSQL integration tests recreate only the dedicated
`reliable_guest_agent_test` schema through migrations. They refuse to run the
destructive reset against any other database. In PowerShell:

```powershell
$env:RGA_TEST_DATABASE_URL = "postgresql+psycopg://rga_runtime:local-runtime-only@127.0.0.1:5432/reliable_guest_agent_test"
$env:RGA_TEST_MIGRATION_DATABASE_URL = "postgresql+psycopg://rga_migrator:local-migration-only@127.0.0.1:5432/reliable_guest_agent_test"
.venv\Scripts\python.exe -m pytest tests/integration -m postgres -p no:cacheprovider
```

Run both commands before completing a persistence milestone.

## Project documents

- [Product specification](docs/product-spec.md)
- [Architecture decisions](docs/architecture-decisions.md)
