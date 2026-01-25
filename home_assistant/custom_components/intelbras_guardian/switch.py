"""Switch platform for Intelbras Guardian eletrificadores."""
import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, ELETRIFICADOR_MODELS
from .coordinator import GuardianCoordinator

_LOGGER = logging.getLogger(__name__)


def is_eletrificador(device: dict) -> bool:
    """Check if device is an electric fence (eletrificador)."""
    model = device.get("model", "")
    if not model:
        return False
    model_upper = model.upper()
    return any(elc in model_upper for elc in ELETRIFICADOR_MODELS)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up switch entities for eletrificadores."""
    coordinator: GuardianCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities = []
    if coordinator.data:
        for device_id, device in coordinator.data.get("devices", {}).items():
            if is_eletrificador(device):
                # Shock control switch
                entities.append(
                    GuardianEletrificadorShockSwitch(
                        coordinator,
                        device_id,
                        device.get("mac", ""),
                    )
                )
                # Alarm control switch
                entities.append(
                    GuardianEletrificadorAlarmSwitch(
                        coordinator,
                        device_id,
                        device.get("mac", ""),
                    )
                )

    async_add_entities(entities)


class GuardianEletrificadorShockSwitch(CoordinatorEntity, SwitchEntity):
    """Representation of an Intelbras electric fence SHOCK switch."""

    _attr_has_entity_name = True
    _attr_device_class = SwitchDeviceClass.SWITCH

    def __init__(
        self,
        coordinator: GuardianCoordinator,
        device_id: int,
        device_mac: str,
    ):
        """Initialize the switch."""
        super().__init__(coordinator)
        self._device_id = device_id
        self._device_mac = device_mac

        # Entity attributes
        self._attr_unique_id = f"{device_mac}_shock"
        self._attr_name = "Choque"
        self._attr_icon = "mdi:flash"

    @property
    def device_info(self):
        """Return device info."""
        device = self.coordinator.get_device(self._device_id)
        if device:
            return {
                "identifiers": {(DOMAIN, self._device_mac)},
                "name": device.get("description", f"Eletrificador {self._device_id}"),
                "manufacturer": "Intelbras",
                "model": device.get("model", "Eletrificador"),
            }
        return None

    @property
    def is_on(self) -> bool:
        """Return true if shock is enabled."""
        device = self.coordinator.get_device(self._device_id)
        if not device:
            return False
        return device.get("shock_enabled", False)

    @property
    def extra_state_attributes(self):
        """Return extra state attributes."""
        device = self.coordinator.get_device(self._device_id)
        if device:
            return {
                "device_id": self._device_id,
                "shock_triggered": device.get("shock_triggered", False),
            }
        return {}

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on shock."""
        _LOGGER.info(f"Turning on shock for eletrificador {self._device_id}")
        success = await self.coordinator.client.eletrificador_shock_on(self._device_id)
        if success:
            await self.coordinator.async_request_refresh()
        else:
            _LOGGER.error("Failed to turn on shock")

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off shock."""
        _LOGGER.info(f"Turning off shock for eletrificador {self._device_id}")
        success = await self.coordinator.client.eletrificador_shock_off(self._device_id)
        if success:
            await self.coordinator.async_request_refresh()
        else:
            _LOGGER.error("Failed to turn off shock")


class GuardianEletrificadorAlarmSwitch(CoordinatorEntity, SwitchEntity):
    """Representation of an Intelbras electric fence ALARM switch."""

    _attr_has_entity_name = True
    _attr_device_class = SwitchDeviceClass.SWITCH

    def __init__(
        self,
        coordinator: GuardianCoordinator,
        device_id: int,
        device_mac: str,
    ):
        """Initialize the switch."""
        super().__init__(coordinator)
        self._device_id = device_id
        self._device_mac = device_mac

        # Entity attributes
        self._attr_unique_id = f"{device_mac}_alarm"
        self._attr_name = "Alarme"
        self._attr_icon = "mdi:shield"

    @property
    def device_info(self):
        """Return device info."""
        device = self.coordinator.get_device(self._device_id)
        if device:
            return {
                "identifiers": {(DOMAIN, self._device_mac)},
                "name": device.get("description", f"Eletrificador {self._device_id}"),
                "manufacturer": "Intelbras",
                "model": device.get("model", "Eletrificador"),
            }
        return None

    @property
    def is_on(self) -> bool:
        """Return true if alarm is armed."""
        device = self.coordinator.get_device(self._device_id)
        if not device:
            return False
        return device.get("alarm_enabled", False)

    @property
    def extra_state_attributes(self):
        """Return extra state attributes."""
        device = self.coordinator.get_device(self._device_id)
        if device:
            return {
                "device_id": self._device_id,
                "alarm_triggered": device.get("alarm_triggered", False),
            }
        return {}

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Arm the alarm."""
        _LOGGER.info(f"Arming alarm for eletrificador {self._device_id}")
        success = await self.coordinator.client.eletrificador_alarm_activate(self._device_id)
        if success:
            await self.coordinator.async_request_refresh()
        else:
            _LOGGER.error("Failed to arm alarm")

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disarm the alarm."""
        _LOGGER.info(f"Disarming alarm for eletrificador {self._device_id}")
        success = await self.coordinator.client.eletrificador_alarm_deactivate(self._device_id)
        if success:
            await self.coordinator.async_request_refresh()
        else:
            _LOGGER.error("Failed to disarm alarm")
