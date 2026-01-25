"""Config flow for Intelbras Guardian integration."""
import logging
from typing import Any, Dict, Optional

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api_client import GuardianApiClient
from .const import (
    CONF_FASTAPI_HOST,
    CONF_FASTAPI_PORT,
    DEFAULT_FASTAPI_PORT,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
        vol.Required(CONF_FASTAPI_HOST): str,
        vol.Required(CONF_FASTAPI_PORT, default=DEFAULT_FASTAPI_PORT): int,
    }
)


async def validate_input(
    hass: HomeAssistant,
    data: Dict[str, Any]
) -> Dict[str, Any]:
    """Validate the user input allows us to connect."""
    session = async_get_clientsession(hass)

    client = GuardianApiClient(
        host=data[CONF_FASTAPI_HOST],
        port=data[CONF_FASTAPI_PORT],
        session=session,
    )

    # First check if API is reachable
    if not await client.check_connection():
        raise CannotConnect("Cannot connect to FastAPI middleware")

    # Then try to authenticate
    if not await client.authenticate(data[CONF_USERNAME], data[CONF_PASSWORD]):
        raise InvalidAuth("Invalid credentials")

    # Get devices to verify everything works
    devices = await client.get_devices()
    if not devices:
        _LOGGER.warning("No devices found, but authentication succeeded")

    # Return info to be stored in the config entry
    return {
        "title": f"Intelbras Guardian ({data[CONF_USERNAME]})",
        "session_id": client.session_id,
        "device_count": len(devices),
    }


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Intelbras Guardian."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Create the options flow."""
        return OptionsFlowHandler(config_entry)

    async def async_step_user(
        self,
        user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors: Dict[str, str] = {}

        if user_input is not None:
            try:
                info = await validate_input(self.hass, user_input)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except Exception:
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                # Check if already configured
                await self.async_set_unique_id(user_input[CONF_USERNAME])
                self._abort_if_unique_id_configured()

                return self.async_create_entry(
                    title=info["title"],
                    data=user_input,
                )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options flow for Intelbras Guardian."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self.config_entry = config_entry
        self._devices: list = []
        self._selected_device_id: Optional[int] = None

    async def async_step_init(
        self,
        user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Manage the options - show menu."""
        return self.async_show_menu(
            step_id="init",
            menu_options=["configure_device_password", "manage_zones"],
        )

    async def async_step_configure_device_password(
        self,
        user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Handle device password configuration."""
        errors: Dict[str, str] = {}

        # Get coordinator from hass.data
        coordinator = self.hass.data.get(DOMAIN, {}).get(self.config_entry.entry_id)
        if not coordinator:
            return self.async_abort(reason="not_loaded")

        # Build device list for selection
        if coordinator.data:
            self._devices = []
            for device_id, device in coordinator.data.get("devices", {}).items():
                has_password = device.get("has_saved_password", False)
                status = " [Senha Salva]" if has_password else ""
                self._devices.append({
                    "id": device_id,
                    "name": f"{device.get('description', f'Dispositivo {device_id}')}{status}",
                    "has_password": has_password,
                })

        if not self._devices:
            return self.async_abort(reason="no_devices")

        if user_input is not None:
            self._selected_device_id = int(user_input["device"])
            return await self.async_step_enter_password()

        # Build device selection schema
        device_options = {str(d["id"]): d["name"] for d in self._devices}

        return self.async_show_form(
            step_id="configure_device_password",
            data_schema=vol.Schema(
                {
                    vol.Required("device"): vol.In(device_options),
                }
            ),
            errors=errors,
        )

    async def async_step_enter_password(
        self,
        user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Enter or manage device password."""
        errors: Dict[str, str] = {}

        coordinator = self.hass.data.get(DOMAIN, {}).get(self.config_entry.entry_id)
        if not coordinator:
            return self.async_abort(reason="not_loaded")

        # Find selected device
        device = coordinator.get_device(self._selected_device_id)
        device_name = device.get("description", f"Dispositivo {self._selected_device_id}") if device else f"Dispositivo {self._selected_device_id}"
        has_password = device.get("has_saved_password", False) if device else False

        if user_input is not None:
            action = user_input.get("action", "save")

            if action == "delete":
                # Delete password
                success = await coordinator.client.delete_device_password(self._selected_device_id)
                if success:
                    await coordinator.async_request_refresh()
                    return self.async_create_entry(title="", data={})
                else:
                    errors["base"] = "delete_failed"
            else:
                # Save password
                password = user_input.get("device_password", "")
                if password:
                    success = await coordinator.client.save_device_password(
                        self._selected_device_id,
                        password
                    )
                    if success:
                        await coordinator.async_request_refresh()
                        return self.async_create_entry(title="", data={})
                    else:
                        errors["base"] = "save_failed"
                else:
                    errors["base"] = "password_required"

        # Build schema based on whether password exists
        if has_password:
            schema = vol.Schema(
                {
                    vol.Required("action", default="save"): vol.In({
                        "save": "Atualizar Senha",
                        "delete": "Remover Senha",
                    }),
                    vol.Optional("device_password"): str,
                }
            )
        else:
            schema = vol.Schema(
                {
                    vol.Required("device_password"): str,
                }
            )

        return self.async_show_form(
            step_id="enter_password",
            data_schema=schema,
            description_placeholders={"device_name": device_name},
            errors=errors,
        )

    async def async_step_manage_zones(
        self,
        user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Manage zone friendly names - redirect to Web UI."""
        return self.async_show_form(
            step_id="manage_zones",
            description_placeholders={
                "webui_url": f"http://{self.config_entry.data[CONF_FASTAPI_HOST]}:{self.config_entry.data[CONF_FASTAPI_PORT]}"
            },
        )


class CannotConnect(Exception):
    """Error to indicate we cannot connect."""


class InvalidAuth(Exception):
    """Error to indicate there is invalid auth."""
