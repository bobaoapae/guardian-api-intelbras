"""Zone (sector) models."""
from pydantic import BaseModel, Field
from typing import Optional
from enum import Enum


class ZoneStatus(str, Enum):
    """
    Zone status enum.

    Based on API responses from XAPK analysis.
    """
    ACTIVE = "ACTIVE"  # Zone is active/triggered
    INACTIVE = "INACTIVE"  # Zone is inactive/closed
    BYPASSED = "BYPASSED"  # Zone is bypassed
    FAULT = "FAULT"  # Zone has a fault


class ZoneType(str, Enum):
    """Zone type for Home Assistant device class mapping."""
    DOOR = "door"
    WINDOW = "window"
    MOTION = "motion"
    SMOKE = "smoke"
    GAS = "gas"
    GLASS_BREAK = "glass_break"
    PANIC = "panic"
    GENERIC = "generic"


class Zone(BaseModel):
    """
    Alarm zone (sensor).

    Called 'Sector' in Intelbras API, but we use 'Zone' for HA compatibility.
    """

    id: int = Field(..., description="Zone unique identifier")
    name: str = Field(..., description="Zone name")
    friendly_name: Optional[str] = Field(None, description="User-friendly name")

    # Status
    status: ZoneStatus = Field(..., description="Current zone status")
    stay_enabled: bool = Field(default=True, description="Whether zone is active in stay mode")
    bypassed: bool = Field(default=False, description="Whether zone is bypassed")

    # Classification
    zone_type: ZoneType = Field(default=ZoneType.GENERIC, description="Zone type for HA device class")
    partition_id: int = Field(..., description="Parent partition ID")

    # Additional metadata
    description: Optional[str] = Field(None, description="Zone description")
    can_bypass: bool = Field(default=True, description="Whether zone can be bypassed")

    class Config:
        """Pydantic config."""
        json_schema_extra = {
            "example": {
                "id": 10,
                "name": "Zona 1",
                "friendly_name": "Porta Principal",
                "status": "INACTIVE",
                "stay_enabled": True,
                "bypassed": False,
                "zone_type": "door",
                "partition_id": 1
            }
        }


class BypassZoneRequest(BaseModel):
    """Request to bypass a zone."""

    zone_id: int = Field(..., description="Zone ID to bypass")
    bypass: bool = Field(..., description="True to bypass, False to unbypass")
