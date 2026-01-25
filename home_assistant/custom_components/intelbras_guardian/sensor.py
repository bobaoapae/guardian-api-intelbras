"""Sensor platform for Intelbras Guardian events."""
import logging
from datetime import datetime
from typing import Any, Optional

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import GuardianCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensor entities."""
    coordinator: GuardianCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities = []

    # Add a last event sensor for each device
    if coordinator.data:
        devices = coordinator.data.get("devices", {})
        for device_id, device in devices.items():
            entities.append(
                GuardianLastEventSensor(
                    coordinator,
                    device_id,
                    device.get("mac", ""),
                )
            )

    async_add_entities(entities)


class GuardianLastEventSensor(CoordinatorEntity, SensorEntity):
    """Sensor showing the last alarm event."""

    _attr_has_entity_name = True
    _attr_name = "Last Event"
    _attr_icon = "mdi:bell-ring"

    def __init__(
        self,
        coordinator: GuardianCoordinator,
        device_id: int,
        device_mac: str,
    ):
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._device_id = device_id
        self._device_mac = device_mac

        # Entity attributes
        self._attr_unique_id = f"{device_mac}_last_event"

    @property
    def device_info(self):
        """Return device info."""
        device = self.coordinator.get_device(self._device_id)
        if device:
            return {
                "identifiers": {(DOMAIN, self._device_mac)},
                "name": device.get("description", f"Intelbras Alarm {self._device_id}"),
                "manufacturer": "Intelbras",
                "model": device.get("model", "Guardian Alarm"),
            }
        return None

    @property
    def native_value(self) -> Optional[str]:
        """Return the state of the sensor."""
        if self.coordinator.data:
            last_event = self.coordinator.data.get("last_event")
            if last_event:
                notification = last_event.get("notification", {})
                return notification.get("title", last_event.get("event_type", "Unknown"))
        return "No events"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        if self.coordinator.data:
            last_event = self.coordinator.data.get("last_event")
            if last_event:
                notification = last_event.get("notification", {})
                zone = last_event.get("zone", {})

                # Parse timestamp
                timestamp = last_event.get("timestamp")
                if timestamp and isinstance(timestamp, str):
                    try:
                        timestamp = datetime.fromisoformat(
                            timestamp.replace("Z", "+00:00")
                        ).isoformat()
                    except ValueError:
                        pass

                return {
                    "event_id": last_event.get("id"),
                    "timestamp": timestamp,
                    "event_type": last_event.get("event_type"),
                    "title": notification.get("title"),
                    "message": notification.get("message"),
                    "zone_id": zone.get("id") if zone else None,
                    "zone_name": zone.get("name") if zone else None,
                    "partition_id": last_event.get("partition_id"),
                    "device_id": last_event.get("device_id"),
                }
        return {}
