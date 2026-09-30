from pydantic_settings import BaseSettings, SettingsConfigDict


class ApplicationSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="RGA_",
        extra="ignore",
    )

    database_url: str | None = None
    migration_database_url: str | None = None
