"""Authentication models."""
from pydantic import BaseModel, Field, EmailStr
from typing import Optional
from datetime import datetime


class LoginRequest(BaseModel):
    """Request model for user login."""

    username: EmailStr = Field(..., description="User email address")
    password: str = Field(..., min_length=6, description="User password")


class TokenResponse(BaseModel):
    """OAuth 2.0 token response from Intelbras API."""

    access_token: str = Field(..., description="JWT access token")
    refresh_token: str = Field(..., description="JWT refresh token")
    token_type: str = Field(default="Bearer", description="Token type")
    expires_in: int = Field(..., description="Token expiration time in seconds")
    scope: Optional[str] = Field(None, description="Token scope")


class SessionInfo(BaseModel):
    """Session information returned to Home Assistant."""

    session_id: str = Field(..., description="Unique session identifier")
    expires_at: datetime = Field(..., description="Session expiration timestamp")
    created_at: datetime = Field(default_factory=datetime.utcnow, description="Session creation time")


class RefreshTokenRequest(BaseModel):
    """Request model for token refresh."""

    refresh_token: str = Field(..., description="Refresh token")


class LogoutRequest(BaseModel):
    """Request model for logout."""

    session_id: str = Field(..., description="Session ID to invalidate")
