"""Button entities for Intelbras Guardian."""
import logging
from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, ELETRIFICADOR_MODELS, AMT8000_MODELS
from .coordinator import GuardianCoordinator

_LOGGER = logging.getLogger(__name__)


def _is_amt8000_family(model: str) -> bool:
    """Check if model belongs to AMT 8000 family (supports fire/medical panic)."""
    model_upper = model.upper()
    return any(m in model_upper for m in AMT8000_MODELS)


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
            model = (device.get("model") or "").upper()
            is_eletrificador = any(m in model for m in ELETRIFICADOR_MODELS)
            has_password = device.get("has_saved_password")

            if not has_password:
                continue

            # Siren off button - alarm centrals only (not eletrificadores)
            if not is_eletrificador:
                entities.append(
                    GuardianSirenOffButton(coordinator, device_id, device)
                )

            # Panic buttons - all models with saved password
            entities.append(
                GuardianPanicButton(coordinator, device_id, device, 1, "Pânico Audível", "mdi:alarm-light")
            )
            entities.append(
                GuardianPanicButton(coordinator, device_id, device, 0, "Pânico Silencioso", "mdi:alarm-light-off")
            )

            # Fire and medical - AMT 8000 family only
            if _is_amt8000_family(model):
                entities.append(
                    GuardianPanicButton(coordinator, device_id, device, 2, "Pânico Incêndio", "mdi:fire")
                )
                entities.append(
                    GuardianPanicButton(coordinator, device_id, device, 3, "Emergência Médica", "mdi:hospital-box")
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
        """Handle button press — silence siren or stop a panic.

        The ISECNet `SIREN_OFF` (0x4F) command only stops siren output for
        zone-driven alarms; a panic alarm keeps the central in a triggered
        state until the panel receives `DEACTIVATE_CENTRAL` (0x44 alone, no
        partition byte). The official Intelbras app handles this implicitly
        in its disarm button (`armOrDisarm2018or4010`): if `isSirenTriggered`
        is true, it just sends `getComandoDesativarCentral()` and returns.

        Mirror that behaviour: when the device is currently triggered we
        route to /alarm/{id}/disarm with `partition_id=None`, which the API
        translates into `_build_isecv1_disarm_cmd(password, None)` — the
        same `[0x44]` packet the official app sends. Otherwise we keep the
        plain SIREN_OFF behaviour for non-triggered cases (e.g. lingering
        siren after a zone alarm cleared on its own).
        """
        device = self.coordinator.get_device(self._device_id)
        is_triggered = bool(device and device.get("is_triggered"))

        if is_triggered:
            _LOGGER.info(
                "Device %d is triggered — sending DEACTIVATE_CENTRAL ([0x44]) to silence siren and clear panic",
                self._device_id,
            )
            result = await self.coordinator.client.disarm_partition(self._device_id, None)
            failure_label = "Falha ao desativar central"
        else:
            _LOGGER.info("Sending SIREN_OFF for device %d", self._device_id)
            result = await self.coordinator.client.turn_off_siren(self._device_id)
            failure_label = "Falha ao desligar sirene"

        if not result.get("success", False):
            error = result.get("error", "Erro desconhecido")
            _LOGGER.error("%s: %s", failure_label, error)
            self.hass.components.persistent_notification.async_create(
                f"{failure_label}: {error}",
                title="Intelbras Guardian",
                notification_id=f"guardian_siren_error_{self._device_id}",
            )


class GuardianPanicButton(CoordinatorEntity, ButtonEntity):
    """Button to trigger a panic alarm."""

    def __init__(
        self,
        coordinator: GuardianCoordinator,
        device_id: int,
        device: dict,
        panic_type: int,
        name: str,
        icon: str,
    ) -> None:
        """Initialize the panic button."""
        super().__init__(coordinator)
        self._device_id = device_id
        self._panic_type = panic_type
        panic_slugs = {0: "silent", 1: "audible", 2: "fire", 3: "medical"}
        slug = panic_slugs.get(panic_type, f"panic_{panic_type}")
        self._attr_unique_id = f"guardian_{device_id}_panic_{slug}"
        self._attr_name = name
        self._attr_icon = icon
        self._attr_entity_registry_enabled_default = True

        device_mac = device.get("mac", "")
        self._attr_device_info = {
            "identifiers": {(DOMAIN, device_mac)},
            "name": device.get("description", f"Intelbras Alarm {device_id}"),
            "manufacturer": "Intelbras",
            "model": device.get("model", "Guardian Alarm"),
        }

    async def async_press(self) -> None:
        """Handle button press - trigger panic alarm."""
        panic_names = {0: "silencioso", 1: "audível", 2: "incêndio", 3: "médico"}
        panic_name = panic_names.get(self._panic_type, str(self._panic_type))
        _LOGGER.info("Triggering %s panic for device %d", panic_name, self._device_id)

        result = await self.coordinator.client.trigger_panic(self._device_id, self._panic_type)

        if not result.get("success", False):
            error = result.get("error", "Erro desconhecido")
            _LOGGER.error("Failed to trigger %s panic: %s", panic_name, error)
            self.hass.components.persistent_notification.async_create(
                f"Falha ao disparar pânico {panic_name}: {error}",
                title="Intelbras Guardian",
                notification_id=f"guardian_panic_error_{self._device_id}",
            )
