from reliable_guest_agent.infrastructure.settings import ApplicationSettings


def test_database_url_can_be_absent_for_in_memory_development(monkeypatch) -> None:
    monkeypatch.delenv("RGA_DATABASE_URL", raising=False)

    settings = ApplicationSettings(_env_file=None)

    assert settings.database_url is None


def test_database_url_is_loaded_from_environment(monkeypatch) -> None:
    database_url = "postgresql+psycopg://runtime:secret@localhost/example"
    monkeypatch.setenv("RGA_DATABASE_URL", database_url)

    settings = ApplicationSettings(_env_file=None)

    assert settings.database_url == database_url
