from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from reliable_guest_agent.api.main import AUTHORIZATION_FAILURE_DETAIL, create_app
from reliable_guest_agent.infrastructure.auth import SyntheticBearerAuthenticator
from reliable_guest_agent.infrastructure.memory import (
    InMemoryIntakeRepository,
    InMemoryReservationAuthorizer,
)


@pytest.fixture
def repository() -> InMemoryIntakeRepository:
    return InMemoryIntakeRepository()


@pytest.fixture
def authorizer() -> InMemoryReservationAuthorizer:
    return InMemoryReservationAuthorizer({"reservation-456": "guest-123"})


@pytest.fixture
def client(
    repository: InMemoryIntakeRepository,
    authorizer: InMemoryReservationAuthorizer,
) -> TestClient:
    application = create_app(
        intake_repository=repository,
        reservation_authorizer=authorizer,
        authenticator=SyntheticBearerAuthenticator(
            {
                "token-booker": "guest-123",
                "token-other": "guest-999",
            }
        ),
    )
    return TestClient(application)


def request_body(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "reservation_reference": "reservation-456",
        "original_message": "Could I receive a refund?",
        "selected_request_types": ["REFUND"],
    }
    body.update(overrides)
    return body


def headers(*, token: str = "token-booker", key: str | None = None) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Idempotency-Key": key or str(uuid4()),
    }


def test_authorized_booking_guest_receives_202_after_durable_intake(
    client: TestClient,
    repository: InMemoryIntakeRepository,
) -> None:
    response = client.post("/v1/intakes", json=request_body(), headers=headers())

    assert response.status_code == 202
    assert response.json()["status"] == "PROCESSING"
    assert response.json()["message_id"]
    assert response.json()["case_id"]
    assert response.headers["Location"] == "/v1/intakes/status"
    assert repository.counts == (1, 1, 1, 1)


def test_missing_or_invalid_bearer_token_returns_401(client: TestClient) -> None:
    missing = client.post(
        "/v1/intakes",
        json=request_body(),
        headers={"Idempotency-Key": str(uuid4())},
    )
    invalid = client.post(
        "/v1/intakes",
        json=request_body(),
        headers=headers(token="not-a-real-token"),
    )

    assert missing.status_code == 401
    assert invalid.status_code == 401
    assert missing.headers["WWW-Authenticate"] == "Bearer"


def test_non_booker_and_missing_reservation_share_privacy_safe_response(
    client: TestClient,
    repository: InMemoryIntakeRepository,
) -> None:
    non_booker = client.post(
        "/v1/intakes",
        json=request_body(),
        headers=headers(token="token-other"),
    )
    missing = client.post(
        "/v1/intakes",
        json=request_body(reservation_reference="reservation-missing"),
        headers=headers(),
    )

    assert non_booker.status_code == 404
    assert missing.status_code == 404
    assert non_booker.json() == missing.json() == {"detail": AUTHORIZATION_FAILURE_DETAIL}
    assert repository.counts == (0, 0, 0, 0)


def test_reservation_outage_returns_503_without_persisting_message(
    client: TestClient,
    repository: InMemoryIntakeRepository,
    authorizer: InMemoryReservationAuthorizer,
) -> None:
    authorizer.unavailable = True

    response = client.post("/v1/intakes", json=request_body(), headers=headers())

    assert response.status_code == 503
    assert repository.counts == (0, 0, 0, 0)


def test_guest_id_is_forbidden_in_request_body(client: TestClient) -> None:
    response = client.post(
        "/v1/intakes",
        json=request_body(guest_id="guest-999"),
        headers=headers(),
    )

    assert response.status_code == 422


def test_changed_payload_with_same_key_returns_409(client: TestClient) -> None:
    key = str(uuid4())
    first = client.post("/v1/intakes", json=request_body(), headers=headers(key=key))
    conflict = client.post(
        "/v1/intakes",
        json=request_body(original_message="Please transfer my reservation."),
        headers=headers(key=key),
    )

    assert first.status_code == 202
    assert conflict.status_code == 409


def test_status_lookup_is_scoped_to_authenticated_guest(client: TestClient) -> None:
    key = str(uuid4())
    created = client.post("/v1/intakes", json=request_body(), headers=headers(key=key))

    owner_lookup = client.get("/v1/intakes/status", headers=headers(key=key))
    other_lookup = client.get(
        "/v1/intakes/status",
        headers=headers(token="token-other", key=key),
    )

    assert owner_lookup.status_code == 200
    assert owner_lookup.json()["case_id"] == created.json()["case_id"]
    assert other_lookup.status_code == 404


def test_malformed_idempotency_key_returns_400(client: TestClient) -> None:
    response = client.post(
        "/v1/intakes",
        json=request_body(),
        headers=headers(key="not-a-uuid"),
    )

    assert response.status_code == 400
