"""Device and alarm central models."""
from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime


class DeviceInfo(BaseModel):
    """Basic device information."""

    id: int = Field(..., description="Device unique identifier")
    description: str = Field(..., description="Device description/name")
    mac: str = Field(..., description="Device MAC address")
    model: Optional[str] = Field(None, description="Device model")
    firmware_version: Optional[str] = Field(None, description="Firmware version")
    is_online: bool = Field(default=True, description="Device online status")
    last_seen: Optional[datetime] = Field(None, description="Last communication timestamp")


class AlarmCentral(BaseModel):
    """
    Alarm central (device) with partitions and zones.

    Based on API response structure from XAPK analysis.
    """

    # Basic info
    id: int = Field(..., description="Central unique identifier")
    description: str = Field(..., description="Central name/description")
    mac: str = Field(..., description="Central MAC address")
    model: Optional[str] = Field(None, description="Central model (e.g., AMT 8000)")

    # Status
    is_online: bool = Field(default=True, description="Online status")
    firmware_version: Optional[str] = Field(None, description="Firmware version")

    # Structure (imported to avoid circular dependencies)
    partitions: List[dict] = Field(default_factory=list, description="List of partitions")

    # Metadata
    created_at: Optional[datetime] = Field(None, description="Registration timestamp")
    updated_at: Optional[datetime] = Field(None, description="Last update timestamp")

    class Config:
        """Pydantic config."""
        json_schema_extra = {
            "example": {
                "id": 1,
                "description": "Central Principal",
                "mac": "00:11:22:33:44:55",
                "model": "AMT 8000",
                "is_online": True,
                "firmware_version": "1.2.3",
                "partitions": [
                    {
                        "id": 1,
                        "name": "Partição Principal",
                        "status": "DISARMED"
                    }
                ]
            }
        }
