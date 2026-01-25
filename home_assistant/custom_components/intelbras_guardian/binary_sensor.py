"""Binary sensor platform for Intelbras Guardian zones."""
import logging
from typing import Optional

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, ZONE_TYPE_DEVICE_CLASS
from .coordinator import GuardianCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up binary sensor entities for zones."""
    coordinator: GuardianCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities = []
    if coordinator.data:
        for zone in coordinator.data.get("zones", []):
            entities.append(
                GuardianZoneSensor(
                    coordinator,
                    zone["device_id"],
                    zone.get("index", zone.get("id", 0)),
                    zone.get("device_mac", ""),
                )
            )

    async_add_entities(entities)


class GuardianZoneSensor(CoordinatorEntity, BinarySensorEntity):
    """Representation of an Intelbras Guardian zone sensor."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: GuardianCoordinator,
        device_id: int,
        zone_index: int,
        device_mac: str,
    ):
        """Initialize the zone sensor."""
        super().__init__(coordinator)
        self._device_id = device_id
        self._zone_index = zone_index
        self._device_mac = device_mac

        # Entity attributes
        self._attr_unique_id = f"{device_mac}_zone_{zone_index}"

    @property
    def name(self) -> str:
        """Return the name of the sensor."""
        zone = self.coordinator.get_zone(self._device_id, self._zone_index)
        if zone:
            # Prefer friendly_name if set
            friendly_name = zone.get("friendly_name")
            if friendly_name:
                return friendly_name
            return zone.get("name", f"Zona {self._zone_index + 1:02d}")
        return f"Zona {self._zone_index + 1:02d}"

    @property
    def device_class(self) -> Optional[BinarySensorDeviceClass]:
        """Return the device class of the sensor."""
        zone = self.coordinator.get_zone(self._device_id, self._zone_index)
        if zone:
            zone_type = zone.get("zone_type", "generic")
            device_class_str = ZONE_TYPE_DEVICE_CLASS.get(zone_type, "opening")
            try:
                return BinarySensorDeviceClass(device_class_str)
            except ValueError:
                return BinarySensorDeviceClass.OPENING
        return BinarySensorDeviceClass.OPENING

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
    def is_on(self) -> bool:
        """Return true if the zone is open/triggered."""
        zone = self.coordinator.get_zone(self._device_id, self._zone_index)
        if zone:
            # Use is_open from zones API (ISECNet real-time status)
            if "is_open" in zone:
                return zone.get("is_open", False)
            # Fallback to status field
            status = zone.get("status", "INACTIVE")
            return status in ("ACTIVE", "open", "triggered")
        return False

    @property
    def extra_state_attributes(self):
        """Return extra state attributes."""
        zone = self.coordinator.get_zone(self._device_id, self._zone_index)
        if zone:
            return {
                "device_id": self._device_id,
                "zone_index": self._zone_index,
                "zone_name": zone.get("name"),
                "friendly_name": zone.get("friendly_name"),
                "is_bypassed": zone.get("is_bypassed", False),
                "is_open": zone.get("is_open", False),
            }
        return {}
