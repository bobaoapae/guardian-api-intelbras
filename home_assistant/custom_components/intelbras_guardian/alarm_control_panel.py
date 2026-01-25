"""Alarm control panel for Intelbras Guardian."""
import asyncio
import logging
from typing import Any, Optional

from homeassistant.components.alarm_control_panel import (
    AlarmControlPanelEntity,
    AlarmControlPanelEntityFeature,
    AlarmControlPanelState,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
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
    _attr_code_arm_required = False
    _attr_code_format = None
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

        # Optimistic state management
        self._optimistic_state: Optional[AlarmControlPanelState] = None
        self._pending_action: Optional[asyncio.Task] = None

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
        # Use optimistic state if set (for immediate UI feedback)
        if self._optimistic_state is not None:
            return self._optimistic_state

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

    def _clear_optimistic_state(self) -> None:
        """Clear optimistic state after sync."""
        self._optimistic_state = None
        self.async_write_ha_state()

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
        """Send disarm command with optimistic update."""
        _LOGGER.info(f"Disarming partition {self._partition_id}")

        # Optimistic update - UI responds immediately
        self._optimistic_state = AlarmControlPanelState.DISARMED
        self.async_write_ha_state()

        # Execute command in background
        async def _execute_disarm():
            try:
                result = await self.coordinator.client.disarm_partition(
                    self._device_id,
                    self._partition_id
                )

                if not result.get("success"):
                    error_msg = result.get("error", "Falha ao desarmar")
                    # Don't revert for "No response" - command likely worked
                    if "No response" not in error_msg:
                        _LOGGER.error(f"Failed to disarm partition: {error_msg}")
                        # Revert optimistic state on error
                        self._optimistic_state = None
                        self.async_write_ha_state()
                    else:
                        _LOGGER.warning(f"Disarm command sent but no response received")
            except Exception as e:
                _LOGGER.error(f"Error disarming partition: {e}")
                # Revert optimistic state on exception
                self._optimistic_state = None
                self.async_write_ha_state()
            finally:
                # Refresh to sync real state (will clear optimistic state if matches)
                await self.coordinator.async_request_refresh()
                # Clear optimistic state after refresh
                self._optimistic_state = None

        # Run in background
        self.hass.async_create_task(_execute_disarm())

    async def async_alarm_arm_home(self, code: str = None) -> None:
        """Send arm home (stay/partial) command with optimistic update."""
        _LOGGER.info(f"Arming partition {self._partition_id} in home mode")

        # Optimistic update - UI responds immediately
        self._optimistic_state = AlarmControlPanelState.ARMING
        self.async_write_ha_state()

        # Execute command in background
        async def _execute_arm_home():
            try:
                result = await self.coordinator.client.arm_partition(
                    self._device_id,
                    self._partition_id,
                    mode="home"
                )

                if result.get("success"):
                    # Update to armed state
                    self._optimistic_state = AlarmControlPanelState.ARMED_HOME
                    self.async_write_ha_state()
                else:
                    error_msg = self._format_arm_error(result)
                    # Don't revert for "No response" - command likely worked
                    if "No response" not in str(result.get("error", "")):
                        _LOGGER.error(f"Failed to arm partition in home mode: {error_msg}")
                        # Revert optimistic state on error
                        self._optimistic_state = None
                        self.async_write_ha_state()
                        # Show error to user via persistent notification
                        self.hass.components.persistent_notification.async_create(
                            error_msg,
                            title="Erro ao Armar Alarme",
                            notification_id=f"alarm_error_{self._device_id}_{self._partition_id}"
                        )
                    else:
                        _LOGGER.warning(f"Arm home command sent but no response received")
                        self._optimistic_state = AlarmControlPanelState.ARMED_HOME
                        self.async_write_ha_state()
            except Exception as e:
                _LOGGER.error(f"Error arming partition in home mode: {e}")
                # Revert optimistic state on exception
                self._optimistic_state = None
                self.async_write_ha_state()
            finally:
                # Refresh to sync real state
                await self.coordinator.async_request_refresh()
                # Clear optimistic state after refresh
                self._optimistic_state = None

        # Run in background
        self.hass.async_create_task(_execute_arm_home())

    async def async_alarm_arm_away(self, code: str = None) -> None:
        """Send arm away (total) command with optimistic update."""
        _LOGGER.info(f"Arming partition {self._partition_id} in away mode")

        # Optimistic update - UI responds immediately
        self._optimistic_state = AlarmControlPanelState.ARMING
        self.async_write_ha_state()

        # Execute command in background
        async def _execute_arm_away():
            try:
                result = await self.coordinator.client.arm_partition(
                    self._device_id,
                    self._partition_id,
                    mode="away"
                )

                if result.get("success"):
                    # Update to armed state
                    self._optimistic_state = AlarmControlPanelState.ARMED_AWAY
                    self.async_write_ha_state()
                else:
                    error_msg = self._format_arm_error(result)
                    # Don't revert for "No response" - command likely worked
                    if "No response" not in str(result.get("error", "")):
                        _LOGGER.error(f"Failed to arm partition in away mode: {error_msg}")
                        # Revert optimistic state on error
                        self._optimistic_state = None
                        self.async_write_ha_state()
                        # Show error to user via persistent notification
                        self.hass.components.persistent_notification.async_create(
                            error_msg,
                            title="Erro ao Armar Alarme",
                            notification_id=f"alarm_error_{self._device_id}_{self._partition_id}"
                        )
                    else:
                        _LOGGER.warning(f"Arm away command sent but no response received")
                        self._optimistic_state = AlarmControlPanelState.ARMED_AWAY
                        self.async_write_ha_state()
            except Exception as e:
                _LOGGER.error(f"Error arming partition in away mode: {e}")
                # Revert optimistic state on exception
                self._optimistic_state = None
                self.async_write_ha_state()
            finally:
                # Refresh to sync real state
                await self.coordinator.async_request_refresh()
                # Clear optimistic state after refresh
                self._optimistic_state = None

        # Run in background
        self.hass.async_create_task(_execute_arm_away())

    def _format_arm_error(self, result: dict) -> str:
        """Format error message for arming failure."""
        error = result.get("error", "Falha ao armar")
        open_zones = result.get("open_zones", [])

        if open_zones:
            zone_names = []
            for zone in open_zones:
                if isinstance(zone, dict):
                    name = zone.get("friendly_name") or zone.get("name") or f"Zona {zone.get('index', '?')}"
                else:
                    name = str(zone)
                zone_names.append(name)
            return f"Não foi possível armar: zonas abertas - {', '.join(zone_names)}"

        return f"Falha ao armar: {error}"
