from reliable_guest_agent.infrastructure.memory import InMemoryIntakeRepository
from tests.repository_contract import assert_intake_repository_contract


def test_in_memory_repository_satisfies_intake_contract() -> None:
    assert_intake_repository_contract(InMemoryIntakeRepository())
