# Reliable Guest Agent

A production-oriented AI workflow that transforms unstructured guest messages
into evidence-backed structured cases for host review. The AI may interpret and
organize a request, but it never makes or executes the host's decision.

The first vertical slice handles a same-day message containing refund and
reservation-transfer requests. It uses synthetic data and runs without a paid
model API key.

## Current milestone

Milestone 0 establishes the collaboratively designed product contract and its
first executable application boundary:

- FastAPI entry point with generated Swagger documentation
- health endpoint
- documented workflow, privacy boundary, state model, retries, and safety
  invariants
- framework-independent domain entities and legal state transitions
- in-memory atomic intake prototype with idempotent replay and rollback
- privacy-safe intake-status lookup scoped to the authenticated guest
- deterministic tests covering domain invariants and workflow failures

HTTP intake routes, PostgreSQL persistence, agent orchestration, retrieval, and
local-model inference are deliberately deferred until their contracts are
defined and proven in sequence.

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate  # Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
uvicorn reliable_guest_agent.api.main:app --reload
```

Open `http://127.0.0.1:8000/docs` for Swagger UI.

## Test

```bash
pytest
```

## Project documents

- [Product specification](docs/product-spec.md)
- [Architecture decisions](docs/architecture-decisions.md)
