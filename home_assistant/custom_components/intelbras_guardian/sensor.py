"""Sensor platform for Intelbras Guardian events."""
import logging
from datetime import datetime
from typing import Any, Optional

from homeassistant.components.sensor import SensorEntity, SensorStateClass
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
    # Track which wireless signal sensors have been created (by unique_id)
    created_signal_sensors: set[str] = set()

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

        # Add wireless signal sensor for each wireless zone already known
        for zone in coordinator.data.get("zones", []):
            if zone.get("is_wireless"):
                uid = f"{zone.get('device_mac', '')}_zone_{zone.get('index', 0)}_signal"
                created_signal_sensors.add(uid)
                entities.append(
                    GuardianWirelessSignalSensor(
                        coordinator,
                        zone["device_id"],
                        zone.get("index", 0),
                        zone.get("device_mac", ""),
                    )
                )

    async_add_entities(entities)

    # Listen for coordinator updates to dynamically add wireless signal sensors
    # (wireless data may not be available on the first poll)
    def _check_new_wireless_zones() -> None:
        if not coordinator.data:
            return
        new_entities = []
        for zone in coordinator.data.get("zones", []):
            if zone.get("is_wireless"):
                uid = f"{zone.get('device_mac', '')}_zone_{zone.get('index', 0)}_signal"
                if uid not in created_signal_sensors:
                    created_signal_sensors.add(uid)
                    new_entities.append(
                        GuardianWirelessSignalSensor(
                            coordinator,
                            zone["device_id"],
                            zone.get("index", 0),
                            zone.get("device_mac", ""),
                        )
                    )
        if new_entities:
            _LOGGER.info("Adding %d new wireless signal sensors", len(new_entities))
            async_add_entities(new_entities)

    entry.async_on_unload(
        coordinator.async_add_listener(_check_new_wireless_zones)
    )


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
    def available(self) -> bool:
        """Return True if entity is available (connection to panel is working)."""
        if not self.coordinator.last_update_success:
            return False
        device = self.coordinator.get_device(self._device_id)
        if device and device.get("connection_unavailable", False):
            return False
        return True

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


class GuardianWirelessSignalSensor(CoordinatorEntity, SensorEntity):
    """Sensor showing wireless zone signal strength (0-10)."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:signal"
    _attr_native_unit_of_measurement = "/10"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: GuardianCoordinator,
        device_id: int,
        zone_index: int,
        device_mac: str,
    ):
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._device_id = device_id
        self._zone_index = zone_index
        self._device_mac = device_mac

        self._attr_unique_id = f"{device_mac}_zone_{zone_index}_signal"

    @property
    def available(self) -> bool:
        """Return True if entity is available (connection to panel is working)."""
        if not self.coordinator.last_update_success:
            return False
        device = self.coordinator.get_device(self._device_id)
        if device and device.get("connection_unavailable", False):
            return False
        return True

    @property
    def name(self) -> str:
        """Return the name of the sensor."""
        zone = self.coordinator.get_zone(self._device_id, self._zone_index)
        zone_name = f"Zona {self._zone_index + 1:02d}"
        if zone:
            zone_name = zone.get("name", zone_name)
        return f"{zone_name} Signal"

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
    def native_value(self) -> Optional[int]:
        """Return the signal strength value (0-10)."""
        zone = self.coordinator.get_zone(self._device_id, self._zone_index)
        if zone:
            return zone.get("signal_strength")
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        zone = self.coordinator.get_zone(self._device_id, self._zone_index)
        if zone:
            return {
                "zone_index": self._zone_index,
                "is_wireless": zone.get("is_wireless", False),
                "battery_low": zone.get("battery_low", False),
                "tamper": zone.get("tamper", False),
            }
        return {}
