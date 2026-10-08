"""Application settings."""
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "sqlite:///./health_softwares.db"
    app_name: str = "HealthSoftwares"
    agency_name: str = "Sunrise Home Care"

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
