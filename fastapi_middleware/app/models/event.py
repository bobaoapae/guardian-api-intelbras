"""Alarm event models."""
from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime
from enum import Enum


class EventType(str, Enum):
    """Event type classification."""
    ALARM = "alarm"  # Zone triggered
    ARM = "arm"  # Partition armed
    DISARM = "disarm"  # Partition disarmed
    BYPASS = "bypass"  # Zone bypassed
    FAULT = "fault"  # System fault
    TAMPER = "tamper"  # Tamper detected
    LOW_BATTERY = "low_battery"  # Low battery
    AC_LOSS = "ac_loss"  # AC power loss
    SYSTEM = "system"  # System event


class NotificationInfo(BaseModel):
    """
    Notification details within an event.

    Based on API response structure from XAPK analysis.
    """

    code: int = Field(..., description="Event code")
    title: str = Field(..., description="Event title")
    message: str = Field(..., description="Event message")


class ZoneInfo(BaseModel):
    """Zone information within an event."""

    id: int = Field(..., description="Zone ID")
    name: str = Field(..., description="Zone name")
    friendly_name: Optional[str] = Field(None, description="User-friendly zone name")


class AlarmEvent(BaseModel):
    """
    Alarm event from Intelbras API.

    Based on AlarmEventModel structure from XAPK analysis.
    """

    # Core fields
    id: int = Field(..., description="Event unique identifier")
    timestamp: datetime = Field(..., description="Event occurrence time")

    # Event classification
    event_type: EventType = Field(default=EventType.SYSTEM, description="Event type")

    # Related entities
    zone: Optional[ZoneInfo] = Field(None, description="Zone that triggered the event")
    alarm_partition: Optional[int] = Field(None, description="Partition ID")

    # Notification data
    notification: NotificationInfo = Field(..., description="Event notification details")

    # Additional metadata
    device_id: Optional[int] = Field(None, description="Device/central ID")
    user_id: Optional[int] = Field(None, description="User who triggered the event (for arm/disarm)")
    is_read: bool = Field(default=False, description="Whether event has been acknowledged")

    class Config:
        """Pydantic config."""
        json_schema_extra = {
            "example": {
                "id": 123,
                "timestamp": "2024-01-24T10:30:00Z",
                "event_type": "alarm",
                "zone": {
                    "id": 10,
                    "name": "Zona 1",
                    "friendly_name": "Porta Principal"
                },
                "alarm_partition": 1,
                "notification": {
                    "code": 1000,
                    "title": "Disparo de Alarme",
                    "message": "Zona 1 foi disparada"
                },
                "device_id": 1,
                "is_read": False
            }
        }


class EventsResponse(BaseModel):
    """Response for event list endpoint."""

    events: list[AlarmEvent] = Field(..., description="List of events")
    total: int = Field(..., description="Total number of events")
    offset: int = Field(..., description="Current offset")
    limit: int = Field(..., description="Current limit")
