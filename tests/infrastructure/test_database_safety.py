import pytest

from reliable_guest_agent.infrastructure.database import (
    UnsafeTestDatabaseError,
    require_safe_test_database,
)


def test_schema_reset_accepts_only_expected_test_database() -> None:
    require_safe_test_database(
        "postgresql+psycopg://user:password@localhost/reliable_guest_agent_test",
        "reliable_guest_agent_test",
    )


@pytest.mark.parametrize(
    ("configured_database", "actual_database"),
    [
        ("reliable_guest_agent_dev", "reliable_guest_agent_dev"),
        ("reliable_guest_agent_test", "reliable_guest_agent_dev"),
        ("reliable_guest_agent_dev", "reliable_guest_agent_test"),
    ],
)
def test_schema_reset_refuses_unexpected_database(
    configured_database: str,
    actual_database: str,
) -> None:
    with pytest.raises(UnsafeTestDatabaseError):
        require_safe_test_database(
            f"postgresql+psycopg://user:password@localhost/{configured_database}",
            actual_database,
        )
