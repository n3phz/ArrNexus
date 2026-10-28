from pathlib import Path
from typing import Any, Optional, List
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def parse_cors_origins(v: Any) -> List[str]:
    """Parse CORS origins from comma-separated string or list."""
    if isinstance(v, str):
        return [origin.strip() for origin in v.split(",") if origin.strip()]
    if isinstance(v, list):
        return v
    return []


class Settings(BaseSettings):
    # Application
    app_name: str = Field(default="arrnexus")
    app_version: str = Field(default="0.1.0")
    environment: str = Field(default="development")
    debug: bool = Field(default=False)

    # CORS Configuration
    cors_origins: List[str] = Field(
        default_factory=lambda: ["https://arrnexus.neph.ovh", "http://localhost:5173", "http://localhost:3000"],
        validation_alias="CORS_ORIGINS"
    )

    # Data directory
    data_dir: Path = Field(default=Path("/tmp/arrnexus"))
    
    # Database
    database_url: str = Field(default="sqlite:////tmp/arrnexus/arrnexus.db")

    # Polling
    poll_interval_seconds: int = Field(default=30)
    poll_timeout_seconds: int = Field(default=10)

    # Sonarr Configuration
    sonarr_url: str = Field(default="http://localhost:8989")
    sonarr_api_key: str = Field(default="")
    sonarr_timeout_seconds: int = Field(default=10)
    sonarr_verify_tls: bool = Field(default=True)

    # Radarr Configuration
    radarr_url: str = Field(default="http://localhost:7878")
    radarr_api_key: str = Field(default="")
    radarr_timeout_seconds: int = Field(default=10)
    radarr_verify_tls: bool = Field(default=True)

    # qBittorrent Configuration - no insecure defaults
    qbittorrent_url: str = Field(default="http://localhost:8080")
    qbittorrent_username: str = Field(default="")
    qbittorrent_password: str = Field(default="")
    qbittorrent_timeout_seconds: int = Field(default=10)
    qbittorrent_verify_tls: bool = Field(default=True)

    # Prowlarr Configuration
    prowlarr_url: str = Field(default="http://localhost:9696")
    prowlarr_api_key: str = Field(default="")
    prowlarr_timeout_seconds: int = Field(default=10)
    prowlarr_verify_tls: bool = Field(default=True)

    # Guardarr Configuration
    guardarr_url: str = Field(default="http://localhost:8000")
    guardarr_api_key: str = Field(default="")
    guardarr_timeout_seconds: int = Field(default=10)
    guardarr_verify_tls: bool = Field(default=True)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    @field_validator("cors_origins", mode="before")
    @classmethod
    def validate_cors_origins(cls, v: Any) -> List[str]:
        return parse_cors_origins(v)

    @field_validator("data_dir", mode="before")
    @classmethod
    def ensure_absolute_path(cls, v: Any) -> Path:
        if isinstance(v, str):
            return Path(v).resolve()
        return v


_settings: Optional[Settings] = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings