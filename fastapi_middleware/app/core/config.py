"""Application configuration using Pydantic Settings."""
from typing import List, Union
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, field_validator


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=True,
        extra="ignore"
    )

    # API Intelbras (constants - should not change)
    INTELBRAS_API_URL: str = Field(
        default="https://api-guardian.intelbras.com.br:8443",
        description="Base URL for Intelbras Guardian API"
    )
    INTELBRAS_OAUTH_URL: str = Field(
        default="https://api.conta.intelbras.com/auth",
        description="OAuth 2.0 endpoint URL"
    )
    INTELBRAS_CLIENT_ID: str = Field(
        default="xHCEFEMoQnBcIHcw8ACqbU9aZaYa",
        description="OAuth client ID from XAPK analysis"
    )

    # Server configuration
    HOST: str = Field(default="0.0.0.0", description="Server host")
    PORT: int = Field(default=8000, description="Server port")
    DEBUG: bool = Field(default=False, description="Debug mode")
    LOG_LEVEL: str = Field(default="INFO", description="Logging level")

    # CORS configuration - accepts comma-separated string or list
    CORS_ORIGINS: Union[str, List[str]] = Field(
        default="http://localhost:8123,http://homeassistant.local:8123",
        description="Allowed CORS origins (comma-separated)"
    )

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def parse_cors_origins(cls, v):
        """Parse CORS_ORIGINS from comma-separated string."""
        if isinstance(v, str):
            return [origin.strip() for origin in v.split(",") if origin.strip()]
        return v

    # Cache configuration
    STATE_BACKEND: str = Field(
        default="memory",
        description="State backend: 'memory' or 'redis'"
    )
    REDIS_URL: str = Field(
        default="redis://localhost:6379/0",
        description="Redis connection URL (if STATE_BACKEND=redis)"
    )

    # Timeouts and intervals
    HTTP_TIMEOUT: int = Field(
        default=30,
        description="HTTP request timeout in seconds"
    )
    TOKEN_REFRESH_BUFFER: int = Field(
        default=300,
        description="Refresh token N seconds before expiration"
    )
    EVENT_POLL_INTERVAL: int = Field(
        default=30,
        description="Event polling interval in seconds"
    )


# Global settings instance
settings = Settings()
