"""Partition models."""
from pydantic import BaseModel, Field
from typing import List, Optional
from enum import Enum


class PartitionStatus(str, Enum):
    """
    Partition status enum.

    Based on API responses from XAPK analysis.
    """
    ARMED = "ARMED"  # Armed away (total)
    ARMED_STAY = "ARMED_STAY"  # Armed home (stay)
    DISARMED = "DISARMED"  # Disarmed


class ArmMode(str, Enum):
    """Arm mode for arming operations."""
    AWAY = "away"  # Arm total (all zones)
    HOME = "home"  # Arm stay (perimeter only)


class Partition(BaseModel):
    """
    Alarm partition.

    A partition is a logical grouping of zones that can be armed/disarmed independently.
    """

    id: int = Field(..., description="Partition unique identifier")
    name: str = Field(..., description="Partition name")
    status: PartitionStatus = Field(..., description="Current arming status")
    is_in_alarm: bool = Field(default=False, description="Whether partition is currently triggered")

    # Zones (sectors) in this partition
    sectors: List[dict] = Field(default_factory=list, description="List of zones/sectors")

    # Additional metadata
    description: Optional[str] = Field(None, description="Partition description")
    can_arm: bool = Field(default=True, description="Whether partition can be armed")
    can_disarm: bool = Field(default=True, description="Whether partition can be disarmed")

    class Config:
        """Pydantic config."""
        json_schema_extra = {
            "example": {
                "id": 1,
                "name": "Partição Principal",
                "status": "DISARMED",
                "is_in_alarm": False,
                "sectors": [
                    {
                        "id": 10,
                        "name": "Zona 1",
                        "friendly_name": "Porta Principal",
                        "status": "ACTIVE"
                    }
                ]
            }
        }


class ArmRequest(BaseModel):
    """Request to arm a partition."""

    partition_id: int = Field(..., description="Partition ID to arm")
    mode: ArmMode = Field(..., description="Arm mode (away or home)")


class DisarmRequest(BaseModel):
    """Request to disarm a partition."""

    partition_id: int = Field(..., description="Partition ID to disarm")


class PartitionStatusResponse(BaseModel):
    """Response after arm/disarm operation."""

    success: bool = Field(..., description="Whether operation succeeded")
    partition_id: int = Field(..., description="Partition ID")
    new_status: PartitionStatus = Field(..., description="New partition status")
    message: Optional[str] = Field(None, description="Status message")
