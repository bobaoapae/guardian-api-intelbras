"""Alarm control panel for Intelbras Guardian."""
import logging
from typing import Any

from homeassistant.components.alarm_control_panel import (
    AlarmControlPanelEntity,
    AlarmControlPanelEntityFeature,
    AlarmControlPanelState,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, STATE_MAPPING
from .coordinator import GuardianCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up alarm control panel entities."""
    coordinator: GuardianCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities = []
    if coordinator.data:
        for partition in coordinator.data.get("partitions", []):
            entities.append(
                GuardianAlarmControlPanel(
                    coordinator,
                    partition["device_id"],
                    partition["id"],
                    partition.get("device_mac", ""),
                )
            )

    async_add_entities(entities)


class GuardianAlarmControlPanel(CoordinatorEntity, AlarmControlPanelEntity):
    """Representation of an Intelbras Guardian alarm partition."""

    _attr_has_entity_name = True
    _attr_supported_features = (
        AlarmControlPanelEntityFeature.ARM_HOME |
        AlarmControlPanelEntityFeature.ARM_AWAY
    )

    def __init__(
        self,
        coordinator: GuardianCoordinator,
        device_id: int,
        partition_id: int,
        device_mac: str,
    ):
        """Initialize the alarm control panel."""
        super().__init__(coordinator)
        self._device_id = device_id
        self._partition_id = partition_id
        self._device_mac = device_mac

        # Entity attributes
        self._attr_unique_id = f"{device_mac}_partition_{partition_id}"

    @property
    def name(self) -> str:
        """Return the name of the alarm."""
        partition = self.coordinator.get_partition(self._device_id, self._partition_id)
        if partition:
            return partition.get("name", f"Partition {self._partition_id}")
        return f"Partition {self._partition_id}"

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
    def state(self) -> str:
        """Return the state of the alarm."""
        partition = self.coordinator.get_partition(self._device_id, self._partition_id)
        device = self.coordinator.get_device(self._device_id)

        if not partition:
            return None

        # Check if triggered first (from device real-time status)
        if device and device.get("is_triggered"):
            return AlarmControlPanelState.TRIGGERED

        # Check partition-level alarm state
        if partition.get("is_in_alarm", False):
            return AlarmControlPanelState.TRIGGERED

        # Get status from partition or device arm_mode
        status = partition.get("status")
        if not status and device:
            # Use device-level arm_mode from real-time status
            status = device.get("arm_mode")

        if not status:
            return AlarmControlPanelState.DISARMED

        # Map status to Home Assistant state
        ha_state = STATE_MAPPING.get(status)
        if ha_state:
            # Convert string to AlarmControlPanelState enum
            state_map = {
                "armed_away": AlarmControlPanelState.ARMED_AWAY,
                "armed_home": AlarmControlPanelState.ARMED_HOME,
                "disarmed": AlarmControlPanelState.DISARMED,
                "triggered": AlarmControlPanelState.TRIGGERED,
            }
            return state_map.get(ha_state, AlarmControlPanelState.DISARMED)

        # Fallback mapping
        status_upper = str(status).upper()
        if "AWAY" in status_upper or "ARMED" in status_upper:
            return AlarmControlPanelState.ARMED_AWAY
        if "STAY" in status_upper or "HOME" in status_upper:
            return AlarmControlPanelState.ARMED_HOME

        return AlarmControlPanelState.DISARMED

    @property
    def extra_state_attributes(self):
        """Return extra state attributes."""
        partition = self.coordinator.get_partition(self._device_id, self._partition_id)
        device = self.coordinator.get_device(self._device_id)

        attrs = {
            "device_id": self._device_id,
            "partition_id": self._partition_id,
        }

        if partition:
            attrs["partition_name"] = partition.get("name")
            attrs["is_in_alarm"] = partition.get("is_in_alarm", False)
            attrs["raw_status"] = partition.get("status")

        if device:
            attrs["arm_mode"] = device.get("arm_mode")
            attrs["is_triggered"] = device.get("is_triggered", False)
            attrs["has_saved_password"] = device.get("has_saved_password", False)
            attrs["partitions_enabled"] = device.get("partitions_enabled")

        return attrs

    async def async_alarm_disarm(self, code: str = None) -> None:
        """Send disarm command."""
        _LOGGER.info(f"Disarming partition {self._partition_id}")
        success = await self.coordinator.client.disarm_partition(
            self._device_id,
            self._partition_id
        )
        if success:
            await self.coordinator.async_request_refresh()
        else:
            _LOGGER.error("Failed to disarm partition")

    async def async_alarm_arm_home(self, code: str = None) -> None:
        """Send arm home (stay/partial) command."""
        _LOGGER.info(f"Arming partition {self._partition_id} in home mode")
        success = await self.coordinator.client.arm_partition(
            self._device_id,
            self._partition_id,
            mode="home"
        )
        if success:
            await self.coordinator.async_request_refresh()
        else:
            _LOGGER.error("Failed to arm partition in home mode")

    async def async_alarm_arm_away(self, code: str = None) -> None:
        """Send arm away (total) command."""
        _LOGGER.info(f"Arming partition {self._partition_id} in away mode")
        success = await self.coordinator.client.arm_partition(
            self._device_id,
            self._partition_id,
            mode="away"
        )
        if success:
            await self.coordinator.async_request_refresh()
        else:
            _LOGGER.error("Failed to arm partition in away mode")
