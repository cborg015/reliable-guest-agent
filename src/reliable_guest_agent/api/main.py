from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field

from reliable_guest_agent.application.intake import (
    CheckIntakeStatus,
    IdempotencyConflictError,
    IntakeCommand,
    IntakeGuestMessage,
    IntakeRepository,
    ReservationAccessDeniedError,
    ReservationAuthorizer,
    ReservationServiceUnavailableError,
)
from reliable_guest_agent.domain.enums import RequestType
from reliable_guest_agent.domain.errors import DomainInvariantError
from reliable_guest_agent.infrastructure.auth import (
    AuthenticatedGuest,
    SyntheticBearerAuthenticator,
)
from reliable_guest_agent.infrastructure.memory import (
    InMemoryIntakeRepository,
    InMemoryReservationAuthorizer,
)

AUTHORIZATION_FAILURE_DETAIL = (
    "We couldn't verify that you can submit a request for this reservation."
)
NOT_FOUND_DETAIL = "Submission not found."
bearer_scheme = HTTPBearer(auto_error=False, scheme_name="SyntheticBearer")


class HealthResponse(BaseModel):
    status: str


class IntakeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reservation_reference: str = Field(min_length=1)
    original_message: str = Field(min_length=1)
    selected_request_types: tuple[RequestType, ...] = Field(min_length=1)


class IntakeResponse(BaseModel):
    message_id: UUID
    case_id: UUID
    status: Literal["PROCESSING"] = "PROCESSING"


def _parse_idempotency_key(value: str) -> str:
    try:
        UUID(value)
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Idempotency-Key must be a valid UUID.",
        ) from error
    return value


def _authenticate_guest(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> AuthenticatedGuest:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Valid bearer authentication is required.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    guest = request.app.state.authenticator.authenticate(credentials.credentials)
    if guest is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Valid bearer authentication is required.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return guest


def create_app(
    *,
    intake_repository: IntakeRepository | None = None,
    reservation_authorizer: ReservationAuthorizer | None = None,
    authenticator: SyntheticBearerAuthenticator | None = None,
) -> FastAPI:
    application = FastAPI(
        title="Reliable Guest Agent",
        version="0.2.0",
        description="Guest request triage with explicit human approval boundaries.",
    )
    repository = intake_repository or InMemoryIntakeRepository()
    authorizer = reservation_authorizer or InMemoryReservationAuthorizer(
        {"reservation-456": "guest-123"}
    )
    application.state.intake_repository = repository
    application.state.reservation_authorizer = authorizer
    application.state.authenticator = authenticator or SyntheticBearerAuthenticator(
        {
            "demo-token-guest-123": "guest-123",
            "demo-token-guest-999": "guest-999",
        }
    )

    @application.get("/health", response_model=HealthResponse, tags=["operations"])
    def health() -> HealthResponse:
        return HealthResponse(status="ok")

    @application.post(
        "/v1/intakes",
        response_model=IntakeResponse,
        status_code=status.HTTP_202_ACCEPTED,
        tags=["intakes"],
    )
    def create_intake(
        body: IntakeRequest,
        response: Response,
        request: Request,
        guest: Annotated[AuthenticatedGuest, Depends(_authenticate_guest)],
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
    ) -> IntakeResponse:
        key = _parse_idempotency_key(idempotency_key)
        use_case = IntakeGuestMessage(
            request.app.state.intake_repository,
            request.app.state.reservation_authorizer,
        )
        try:
            result = use_case.execute(
                IntakeCommand(
                    guest_id=guest.guest_id,
                    reservation_reference=body.reservation_reference,
                    original_message=body.original_message,
                    selected_request_types=body.selected_request_types,
                    idempotency_key=key,
                )
            )
        except ReservationAccessDeniedError as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=AUTHORIZATION_FAILURE_DETAIL,
            ) from error
        except ReservationServiceUnavailableError as error:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Reservation verification is temporarily unavailable.",
            ) from error
        except IdempotencyConflictError as error:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Idempotency key was already used with a different payload.",
            ) from error
        except DomainInvariantError as error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=str(error),
            ) from error

        response.headers["Location"] = "/v1/intakes/status"
        return IntakeResponse(message_id=result.message_id, case_id=result.case_id)

    @application.get(
        "/v1/intakes/status",
        response_model=IntakeResponse,
        tags=["intakes"],
    )
    def check_intake_status(
        request: Request,
        guest: Annotated[AuthenticatedGuest, Depends(_authenticate_guest)],
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
    ) -> IntakeResponse:
        key = _parse_idempotency_key(idempotency_key)
        result = CheckIntakeStatus(request.app.state.intake_repository).execute(
            guest_id=guest.guest_id,
            idempotency_key=key,
        )
        if result is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND_DETAIL)
        return IntakeResponse(message_id=result.message_id, case_id=result.case_id)

    return application


app = create_app()
