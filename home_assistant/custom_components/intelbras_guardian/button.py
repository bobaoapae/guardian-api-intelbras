"""Button entities for Intelbras Guardian."""
import logging
from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, ELETRIFICADOR_MODELS
from .coordinator import GuardianCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up button entities."""
    coordinator: GuardianCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities = []
    if coordinator.data:
        for device_id, device in coordinator.data.get("devices", {}).items():
            # Only create siren off button for alarm centrals (not eletrificadores)
            model = (device.get("model") or "").upper()
            is_eletrificador = any(m in model for m in ELETRIFICADOR_MODELS)

            if not is_eletrificador and device.get("has_saved_password"):
                entities.append(
                    GuardianSirenOffButton(coordinator, device_id, device)
                )

    async_add_entities(entities)


class GuardianSirenOffButton(CoordinatorEntity, ButtonEntity):
    """Button to turn off the alarm siren."""

    def __init__(
        self,
        coordinator: GuardianCoordinator,
        device_id: int,
        device: dict,
    ) -> None:
        """Initialize the button."""
        super().__init__(coordinator)
        self._device_id = device_id
        self._attr_unique_id = f"guardian_{device_id}_siren_off"
        self._attr_name = "Desligar Sirene"
        self._attr_icon = "mdi:volume-off"

        # Device info - must match identifiers used by alarm_control_panel
        # (keyed by MAC address, not device_id)
        device_mac = device.get("mac", "")
        self._attr_device_info = {
            "identifiers": {(DOMAIN, device_mac)},
            "name": device.get("description", f"Intelbras Alarm {device_id}"),
            "manufacturer": "Intelbras",
            "model": device.get("model", "Guardian Alarm"),
        }

    async def async_press(self) -> None:
        """Handle button press - turn off siren."""
        _LOGGER.info("Turning off siren for device %d", self._device_id)

        result = await self.coordinator.client.turn_off_siren(self._device_id)

        if not result.get("success", False):
            error = result.get("error", "Erro desconhecido")
            _LOGGER.error("Failed to turn off siren: %s", error)
            self.hass.components.persistent_notification.async_create(
                f"Falha ao desligar sirene: {error}",
                title="Intelbras Guardian",
                notification_id=f"guardian_siren_error_{self._device_id}",
            )
