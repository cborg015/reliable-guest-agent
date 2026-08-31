class DomainInvariantError(ValueError):
    """Raised when a domain object would enter an invalid state."""


class InvalidTransitionError(DomainInvariantError):
    """Raised when a requested state transition is not legal."""

