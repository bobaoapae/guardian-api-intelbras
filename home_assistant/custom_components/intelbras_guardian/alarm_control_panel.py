"""Alarm control panel for Intelbras Guardian."""
import asyncio
import logging
from typing import Any, List, Optional

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

from .const import (
    CONF_AWAY_PARTITIONS,
    CONF_HOME_PARTITIONS,
    CONF_UNIFIED_ALARM,
    DOMAIN,
    STATE_MAPPING,
)
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
        # Get unified alarm config from options
        unified_config = entry.options.get(CONF_UNIFIED_ALARM, {})
        _LOGGER.debug(f"Entry options: {entry.options}")
        _LOGGER.debug(f"Unified config: {unified_config}")

        # Track which devices have unified alarm enabled
        unified_devices = set()

        # Create unified alarm entities for configured devices
        for device_id_str, device_config in unified_config.items():
            _LOGGER.debug(f"Processing unified config for device {device_id_str}: {device_config}")
            if device_config.get("enabled", True):
                device_id = int(device_id_str)
                device = coordinator.get_device(device_id)
                _LOGGER.debug(f"Device {device_id} found: {device is not None}")
                if device:
                    unified_devices.add(device_id)
                    # Get partitions for this device
                    device_partitions = [
                        p for p in coordinator.data.get("partitions", [])
                        if p.get("device_id") == device_id
                    ]
                    _LOGGER.debug(f"Device {device_id} has {len(device_partitions)} partitions")
                    if len(device_partitions) > 1:
                        entities.append(
                            GuardianUnifiedAlarmControlPanel(
                                coordinator,
                                device_id,
                                device_config.get("mac", device.get("mac", "")),
                                device_config.get(CONF_HOME_PARTITIONS, [0]),
                                device_config.get(CONF_AWAY_PARTITIONS, list(range(len(device_partitions)))),
                                device_partitions,
                            )
                        )
                        _LOGGER.info(
                            f"Created unified alarm for device {device_id} "
                            f"(home={device_config.get(CONF_HOME_PARTITIONS)}, "
                            f"away={device_config.get(CONF_AWAY_PARTITIONS)})"
                        )
                    else:
                        _LOGGER.warning(f"Device {device_id} has only {len(device_partitions)} partitions, skipping unified alarm")

        # Create individual partition entities
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
    # Individual partitions only support ARM_AWAY (simple arm/disarm)
    # Use the unified alarm entity for HOME/AWAY modes
    _attr_supported_features = AlarmControlPanelEntityFeature.ARM_AWAY

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
            # Connection status (for detecting AMT legacy app blocking)
            attrs["connection_unavailable"] = device.get("connection_unavailable", False)
            attrs["last_updated"] = device.get("last_updated")

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
                    error_msg = self._format_disarm_error(result)
                    # Don't revert for "No response" - command likely worked
                    if "No response" not in str(result.get("error", "")):
                        _LOGGER.error(f"Failed to disarm partition: {error_msg}")
                        # Revert optimistic state on error
                        self._optimistic_state = None
                        self.async_write_ha_state()
                        # Show error to user via persistent notification
                        self.hass.components.persistent_notification.async_create(
                            error_msg,
                            title="Erro ao Desarmar Alarme",
                            notification_id=f"alarm_error_{self._device_id}_{self._partition_id}"
                        )
                    else:
                        _LOGGER.warning(f"Disarm command sent but no response received")
            except Exception as e:
                _LOGGER.error(f"Error disarming partition: {e}")
                # Revert optimistic state on exception
                self._optimistic_state = None
                self.async_write_ha_state()
                # Show error notification
                self.hass.components.persistent_notification.async_create(
                    f"Erro ao desarmar: {str(e)}",
                    title="Erro ao Desarmar Alarme",
                    notification_id=f"alarm_error_{self._device_id}_{self._partition_id}"
                )
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
                # Show error notification
                self.hass.components.persistent_notification.async_create(
                    f"Erro ao armar (home): {str(e)}",
                    title="Erro ao Armar Alarme",
                    notification_id=f"alarm_error_{self._device_id}_{self._partition_id}"
                )
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
                # Show error notification
                self.hass.components.persistent_notification.async_create(
                    f"Erro ao armar (away): {str(e)}",
                    title="Erro ao Armar Alarme",
                    notification_id=f"alarm_error_{self._device_id}_{self._partition_id}"
                )
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

        # Check for connection unavailable error
        if "ConnectionUnavailable" in error or "indisponivel" in error.lower():
            return (
                "Conexao com a central indisponivel.\n\n"
                "Verifique se o aplicativo AMT nao esta aberto.\n"
                "A central so permite uma conexao por vez."
            )

        if open_zones:
            zone_names = []
            for zone in open_zones:
                if isinstance(zone, dict):
                    name = zone.get("friendly_name") or zone.get("name") or f"Zona {zone.get('index', '?') + 1}"
                else:
                    name = str(zone)
                zone_names.append(name)
            zones_list = "\n".join(f"  - {z}" for z in zone_names)
            return f"Nao foi possivel armar: zonas abertas\n\n{zones_list}"

        return f"Falha ao armar: {error}"

    def _format_disarm_error(self, result: dict) -> str:
        """Format error message for disarming failure."""
        error = result.get("error", "Falha ao desarmar")

        # Check for connection unavailable error
        if "ConnectionUnavailable" in error or "indisponivel" in error.lower():
            return (
                "Conexao com a central indisponivel.\n\n"
                "Verifique se o aplicativo AMT nao esta aberto.\n"
                "A central so permite uma conexao por vez."
            )

        return f"Falha ao desarmar: {error}"


class GuardianUnifiedAlarmControlPanel(CoordinatorEntity, AlarmControlPanelEntity):
    """Unified alarm control panel that controls multiple partitions."""

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
        device_mac: str,
        home_partitions: list,
        away_partitions: list,
        partitions: list[dict],
    ):
        """Initialize the unified alarm control panel."""
        super().__init__(coordinator)
        self._device_id = device_id
        self._device_mac = device_mac
        # Ensure partition indices are integers (may come as strings from config)
        self._home_partitions = [int(p) for p in home_partitions]
        self._away_partitions = [int(p) for p in away_partitions]
        self._partitions = partitions  # List of partition dicts

        _LOGGER.info(
            f"Unified alarm initialized: home_partitions={self._home_partitions}, "
            f"away_partitions={self._away_partitions}"
        )

        # Optimistic state management
        self._optimistic_state: Optional[AlarmControlPanelState] = None

        # Entity attributes
        self._attr_unique_id = f"{device_mac}_unified_alarm"

    @property
    def name(self) -> str:
        """Return the name of the alarm."""
        device = self.coordinator.get_device(self._device_id)
        if device:
            return device.get("description", "Alarme")
        return "Alarme"

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

    def _get_partition_states(self) -> dict[int, str]:
        """Get current state of each partition by index."""
        states = {}
        partitions = self.coordinator.data.get("partitions", []) if self.coordinator.data else []

        device_partitions = [p for p in partitions if p.get("device_id") == self._device_id]
        for idx, partition in enumerate(device_partitions):
            status = partition.get("status", "disarmed")
            states[idx] = status
            _LOGGER.debug(f"Partition {idx} status: {status}")

        return states

    @property
    def state(self) -> str:
        """Return the state of the unified alarm."""
        # Use optimistic state if set
        if self._optimistic_state is not None:
            _LOGGER.debug(f"Unified alarm using optimistic state: {self._optimistic_state}")
            return self._optimistic_state

        device = self.coordinator.get_device(self._device_id)

        # Check if triggered
        if device and device.get("is_triggered"):
            return AlarmControlPanelState.TRIGGERED

        # Get partition states
        partition_states = self._get_partition_states()
        _LOGGER.debug(f"Unified alarm partition_states: {partition_states}")

        if not partition_states:
            _LOGGER.debug("Unified alarm: no partition states, returning DISARMED")
            return AlarmControlPanelState.DISARMED

        # Check which partitions are armed
        # Note: Can't use "armed" in status because "disarmed" contains "armed"!
        armed_partitions = set()
        armed_states = {"armed_away", "armed_stay", "armed_home", "armed"}
        for idx, status in partition_states.items():
            status_lower = str(status).lower() if status else ""
            if status_lower in armed_states:
                armed_partitions.add(idx)

        _LOGGER.debug(f"Unified alarm armed_partitions: {armed_partitions}")

        # No partitions armed = DISARMED
        if not armed_partitions:
            _LOGGER.debug("Unified alarm: no armed partitions, returning DISARMED")
            return AlarmControlPanelState.DISARMED

        # Determine unified state based on configuration
        away_set = set(self._away_partitions)
        home_set = set(self._home_partitions)
        away_only = away_set - home_set

        _LOGGER.debug(
            f"Unified alarm config: home_set={home_set}, away_set={away_set}, away_only={away_only}"
        )

        # ARMED_AWAY: All away partitions are armed
        if away_set and away_set.issubset(armed_partitions):
            _LOGGER.debug("Unified alarm: all away partitions armed, returning ARMED_AWAY")
            return AlarmControlPanelState.ARMED_AWAY

        # ARMED_HOME: Home partitions armed, but away-only partitions NOT armed
        if home_set and home_set.issubset(armed_partitions):
            if not (away_only & armed_partitions):
                _LOGGER.debug("Unified alarm: home partitions armed (away-only not armed), returning ARMED_HOME")
                return AlarmControlPanelState.ARMED_HOME

        # Some partitions armed but doesn't match patterns
        _LOGGER.debug("Unified alarm: partial arm state, returning ARMED_HOME")
        return AlarmControlPanelState.ARMED_HOME

    @property
    def extra_state_attributes(self):
        """Return extra state attributes."""
        device = self.coordinator.get_device(self._device_id)
        partition_states = self._get_partition_states()

        # Build partition status list
        partition_status = []
        for idx, state in partition_states.items():
            partition = next(
                (p for p in self._partitions if self._partitions.index(p) == idx),
                None
            )
            name = partition.get("name", f"Particao {idx + 1}") if partition else f"Particao {idx + 1}"
            partition_status.append({
                "index": idx,
                "name": name,
                "status": state,
                "in_home_mode": idx in self._home_partitions,
                "in_away_mode": idx in self._away_partitions,
            })

        attrs = {
            "device_id": self._device_id,
            "unified_mode": True,
            "home_partitions": self._home_partitions,
            "away_partitions": self._away_partitions,
            "partition_status": partition_status,
        }

        if device:
            attrs["connection_unavailable"] = device.get("connection_unavailable", False)
            attrs["last_updated"] = device.get("last_updated")

        return attrs

    async def async_alarm_disarm(self, code: str = None) -> None:
        """Disarm all partitions that are currently armed."""
        _LOGGER.info(f"Unified alarm: Disarming armed partitions for device {self._device_id}")

        self._optimistic_state = AlarmControlPanelState.DISARMED
        self.async_write_ha_state()

        async def _execute_disarm():
            all_success = True
            errors = []

            # Get current partition states to only disarm armed partitions
            # This avoids sending DISARM to already-disarmed partitions which can
            # cause unexpected behavior on some panels (e.g., AMT_2018_E_SMART)
            partition_states = self._get_partition_states()
            armed_states = {"armed_away", "armed_stay", "armed_home", "armed"}

            # Only disarm partitions that are actually armed
            all_partitions = set(self._away_partitions) | set(self._home_partitions)
            partitions_to_disarm = []
            for idx in all_partitions:
                status = partition_states.get(idx, "")
                status_lower = str(status).lower() if status else ""
                if status_lower in armed_states:
                    partitions_to_disarm.append(idx)
                else:
                    _LOGGER.debug(f"Partition {idx} already disarmed (status={status}), skipping")

            _LOGGER.info(f"Partitions to disarm: {partitions_to_disarm} (armed from {all_partitions})")

            for idx in partitions_to_disarm:
                if idx < len(self._partitions):
                    partition_id = self._partitions[idx].get("id")
                    try:
                        result = await self.coordinator.client.disarm_partition(
                            self._device_id,
                            partition_id
                        )
                        if not result.get("success"):
                            if "No response" not in str(result.get("error", "")):
                                all_success = False
                                errors.append(f"Particao {idx + 1}: {result.get('error')}")
                    except Exception as e:
                        all_success = False
                        errors.append(f"Particao {idx + 1}: {str(e)}")

            if not all_success:
                self._optimistic_state = None
                self.async_write_ha_state()
                error_msg = "Falha ao desarmar:\n" + "\n".join(errors)
                self.hass.components.persistent_notification.async_create(
                    error_msg,
                    title="Erro ao Desarmar Alarme",
                    notification_id=f"alarm_error_{self._device_id}_unified"
                )

            await self.coordinator.async_request_refresh()
            self._optimistic_state = None
            self.async_write_ha_state()

        self.hass.async_create_task(_execute_disarm())

    async def async_alarm_arm_home(self, code: str = None) -> None:
        """Arm home partitions only."""
        _LOGGER.info(
            f"Unified alarm: Arming HOME partitions {self._home_partitions} for device {self._device_id}"
        )

        self._optimistic_state = AlarmControlPanelState.ARMING
        self.async_write_ha_state()

        async def _execute_arm_home():
            all_success = True
            errors = []
            open_zones_all = []

            # First disarm partitions not in home mode (if they're armed)
            partition_states = self._get_partition_states()
            armed_states = {"armed_away", "armed_stay", "armed_home", "armed"}
            for idx, status in partition_states.items():
                status_lower = str(status).lower() if status else ""
                # Only disarm if partition is not in home mode AND is actually armed
                # Note: Can't use "armed" in status because "disarmed" contains "armed"!
                if idx not in self._home_partitions and status_lower in armed_states:
                    if idx < len(self._partitions):
                        partition_id = self._partitions[idx].get("id")
                        _LOGGER.info(f"Disarming partition {idx} (not in home mode, status={status})")
                        try:
                            await self.coordinator.client.disarm_partition(
                                self._device_id, partition_id
                            )
                        except Exception:
                            pass  # Best effort

            # Arm home partitions
            for idx in self._home_partitions:
                if idx < len(self._partitions):
                    partition_id = self._partitions[idx].get("id")
                    try:
                        result = await self.coordinator.client.arm_partition(
                            self._device_id,
                            partition_id,
                            mode="away"  # Use "away" mode for the partition itself
                        )
                        if not result.get("success"):
                            if "No response" not in str(result.get("error", "")):
                                all_success = False
                                errors.append(f"Particao {idx + 1}: {result.get('error')}")
                                if result.get("open_zones"):
                                    open_zones_all.extend(result.get("open_zones"))
                    except Exception as e:
                        all_success = False
                        errors.append(f"Particao {idx + 1}: {str(e)}")

            if all_success:
                self._optimistic_state = AlarmControlPanelState.ARMED_HOME
                self.async_write_ha_state()
            else:
                self._optimistic_state = None
                self.async_write_ha_state()

                if open_zones_all:
                    zone_names = []
                    for zone in open_zones_all:
                        if isinstance(zone, dict):
                            name = zone.get("friendly_name") or zone.get("name") or f"Zona {zone.get('index', '?') + 1}"
                        else:
                            name = str(zone)
                        if name not in zone_names:
                            zone_names.append(name)
                    zones_list = "\n".join(f"  - {z}" for z in zone_names)
                    error_msg = f"Nao foi possivel armar: zonas abertas\n\n{zones_list}"
                else:
                    error_msg = "Falha ao armar (Em Casa):\n" + "\n".join(errors)

                self.hass.components.persistent_notification.async_create(
                    error_msg,
                    title="Erro ao Armar Alarme",
                    notification_id=f"alarm_error_{self._device_id}_unified"
                )

            await self.coordinator.async_request_refresh()
            self._optimistic_state = None
            self.async_write_ha_state()

        self.hass.async_create_task(_execute_arm_home())

    async def async_alarm_arm_away(self, code: str = None) -> None:
        """Arm all away partitions."""
        _LOGGER.info(
            f"Unified alarm: Arming AWAY partitions {self._away_partitions} for device {self._device_id}"
        )

        self._optimistic_state = AlarmControlPanelState.ARMING
        self.async_write_ha_state()

        async def _execute_arm_away():
            all_success = True
            errors = []
            open_zones_all = []

            # Arm away partitions
            for idx in self._away_partitions:
                if idx < len(self._partitions):
                    partition_id = self._partitions[idx].get("id")
                    try:
                        result = await self.coordinator.client.arm_partition(
                            self._device_id,
                            partition_id,
                            mode="away"
                        )
                        if not result.get("success"):
                            if "No response" not in str(result.get("error", "")):
                                all_success = False
                                errors.append(f"Particao {idx + 1}: {result.get('error')}")
                                if result.get("open_zones"):
                                    open_zones_all.extend(result.get("open_zones"))
                    except Exception as e:
                        all_success = False
                        errors.append(f"Particao {idx + 1}: {str(e)}")

            if all_success:
                self._optimistic_state = AlarmControlPanelState.ARMED_AWAY
                self.async_write_ha_state()
            else:
                self._optimistic_state = None
                self.async_write_ha_state()

                if open_zones_all:
                    zone_names = []
                    for zone in open_zones_all:
                        if isinstance(zone, dict):
                            name = zone.get("friendly_name") or zone.get("name") or f"Zona {zone.get('index', '?') + 1}"
                        else:
                            name = str(zone)
                        if name not in zone_names:
                            zone_names.append(name)
                    zones_list = "\n".join(f"  - {z}" for z in zone_names)
                    error_msg = f"Nao foi possivel armar: zonas abertas\n\n{zones_list}"
                else:
                    error_msg = "Falha ao armar (Ausente):\n" + "\n".join(errors)

                self.hass.components.persistent_notification.async_create(
                    error_msg,
                    title="Erro ao Armar Alarme",
                    notification_id=f"alarm_error_{self._device_id}_unified"
                )

            await self.coordinator.async_request_refresh()
            self._optimistic_state = None
            self.async_write_ha_state()

        self.hass.async_create_task(_execute_arm_away())
