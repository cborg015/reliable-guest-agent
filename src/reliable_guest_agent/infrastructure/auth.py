from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AuthenticatedGuest:
    guest_id: str


class SyntheticBearerAuthenticator:
    """Demo-only replacement point for a production identity provider."""

    def __init__(self, token_guests: dict[str, str] | None = None) -> None:
        self._token_guests = dict(token_guests or {})

    def authenticate(self, token: str) -> AuthenticatedGuest | None:
        guest_id = self._token_guests.get(token)
        return AuthenticatedGuest(guest_id) if guest_id is not None else None
