"""Event platform for Intelbras Guardian zone activations."""
import logging
from typing import Optional

from homeassistant.components.event import EventEntity, EventDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import GuardianCoordinator

_LOGGER = logging.getLogger(__name__)

EVENT_TYPE_TRIGGERED = "triggered"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up event entities for zones."""
    coordinator: GuardianCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities = []

    if coordinator.data:
        for zone in coordinator.data.get("zones", []):
            entities.append(
                GuardianZoneEvent(
                    coordinator,
                    zone["device_id"],
                    zone.get("index", zone.get("id", 0)),
                    zone.get("device_mac", ""),
                )
            )

    async_add_entities(entities)


class GuardianZoneEvent(CoordinatorEntity, EventEntity):
    """Event entity that fires when a zone is triggered (opened)."""

    _attr_has_entity_name = True
    _attr_event_types = [EVENT_TYPE_TRIGGERED]
    _attr_device_class = EventDeviceClass.DOORBELL
    _attr_translation_key = "zone_triggered"

    def __init__(
        self,
        coordinator: GuardianCoordinator,
        device_id: int,
        zone_index: int,
        device_mac: str,
    ):
        """Initialize the zone event entity."""
        super().__init__(coordinator)
        self._device_id = device_id
        self._zone_index = zone_index
        self._device_mac = device_mac

        self._attr_unique_id = f"{device_mac}_zone_{zone_index}_event"

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        if not self.coordinator.last_update_success:
            return False
        device = self.coordinator.get_device(self._device_id)
        if device and device.get("connection_unavailable", False):
            return False
        return True

    @property
    def name(self) -> str:
        """Return the name of the event entity."""
        zone = self.coordinator.get_zone(self._device_id, self._zone_index)
        if zone:
            friendly_name = zone.get("friendly_name")
            if friendly_name:
                return f"{friendly_name} Evento"
            zone_name = zone.get("name", f"Zona {self._zone_index + 1:02d}")
            return f"{zone_name} Evento"
        return f"Zona {self._zone_index + 1:02d} Evento"

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

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        if not self.coordinator.data:
            return

        triggered = self.coordinator.data.get("_zone_triggered", [])
        key = (self._device_id, self._zone_index)

        if key in triggered:
            self._trigger_event(EVENT_TYPE_TRIGGERED, {
                "zone_index": self._zone_index,
                "zone_name": self.name,
            })

        self.async_write_ha_state()
