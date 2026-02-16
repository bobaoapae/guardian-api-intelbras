"""Binary sensor platform for Intelbras Guardian zones."""
import logging
from typing import Any, Optional

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
    # Track which battery sensors have been created (by unique_id)
    created_battery_sensors: set[str] = set()

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

            # Add battery sensor for wireless zones already known
            if zone.get("is_wireless"):
                zone_index = zone.get("index", zone.get("id", 0))
                uid = f"{zone.get('device_mac', '')}_zone_{zone_index}_battery"
                created_battery_sensors.add(uid)
                entities.append(
                    GuardianZoneBatterySensor(
                        coordinator,
                        zone["device_id"],
                        zone_index,
                        zone.get("device_mac", ""),
                    )
                )

    async_add_entities(entities)

    # Listen for coordinator updates to dynamically add battery sensors
    # (wireless data may not be available on the first poll)
    def _check_new_wireless_zones() -> None:
        if not coordinator.data:
            return
        new_entities = []
        for zone in coordinator.data.get("zones", []):
            if zone.get("is_wireless"):
                zone_index = zone.get("index", zone.get("id", 0))
                uid = f"{zone.get('device_mac', '')}_zone_{zone_index}_battery"
                if uid not in created_battery_sensors:
                    created_battery_sensors.add(uid)
                    new_entities.append(
                        GuardianZoneBatterySensor(
                            coordinator,
                            zone["device_id"],
                            zone_index,
                            zone.get("device_mac", ""),
                        )
                    )
        if new_entities:
            _LOGGER.info("Adding %d new wireless battery sensors", len(new_entities))
            async_add_entities(new_entities)

    entry.async_on_unload(
        coordinator.async_add_listener(_check_new_wireless_zones)
    )


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
            attrs = {
                "device_id": self._device_id,
                "zone_index": self._zone_index,
                "zone_name": zone.get("name"),
                "friendly_name": zone.get("friendly_name"),
                "is_bypassed": zone.get("is_bypassed", False),
                "is_open": zone.get("is_open", False),
            }
            # Include wireless data if available
            if zone.get("is_wireless"):
                attrs["is_wireless"] = True
                attrs["battery_low"] = zone.get("battery_low", False)
                attrs["signal_strength"] = zone.get("signal_strength")
                attrs["tamper"] = zone.get("tamper", False)
            return attrs
        return {}


class GuardianZoneBatterySensor(CoordinatorEntity, BinarySensorEntity):
    """Binary sensor for wireless zone battery status (low battery = on)."""

    _attr_has_entity_name = True
    _attr_device_class = BinarySensorDeviceClass.BATTERY

    def __init__(
        self,
        coordinator: GuardianCoordinator,
        device_id: int,
        zone_index: int,
        device_mac: str,
    ):
        """Initialize the battery sensor."""
        super().__init__(coordinator)
        self._device_id = device_id
        self._zone_index = zone_index
        self._device_mac = device_mac

        self._attr_unique_id = f"{device_mac}_zone_{zone_index}_battery"

    @property
    def name(self) -> str:
        """Return the name of the sensor."""
        zone = self.coordinator.get_zone(self._device_id, self._zone_index)
        zone_name = f"Zona {self._zone_index + 1:02d}"
        if zone:
            zone_name = zone.get("name", zone_name)
        return f"{zone_name} Battery"

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
        """Return true if the zone has low battery."""
        zone = self.coordinator.get_zone(self._device_id, self._zone_index)
        if zone:
            return zone.get("battery_low", False)
        return False

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        zone = self.coordinator.get_zone(self._device_id, self._zone_index)
        if zone:
            return {
                "zone_index": self._zone_index,
                "signal_strength": zone.get("signal_strength"),
                "is_wireless": zone.get("is_wireless", False),
            }
        return {}
