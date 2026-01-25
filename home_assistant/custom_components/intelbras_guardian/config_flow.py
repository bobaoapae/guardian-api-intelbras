"""Config flow for Intelbras Guardian integration."""
import logging
from typing import Any, Dict, Optional

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api_client import GuardianApiClient
from .const import (
    CONF_FASTAPI_HOST,
    CONF_FASTAPI_PORT,
    CONF_SESSION_ID,
    DEFAULT_FASTAPI_PORT,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

# Step 1: API connection
STEP_API_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_FASTAPI_HOST): str,
        vol.Required(CONF_FASTAPI_PORT, default=DEFAULT_FASTAPI_PORT): int,
    }
)

# Step 2: OAuth callback URL
STEP_OAUTH_SCHEMA = vol.Schema(
    {
        vol.Required("callback_url"): str,
    }
)


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Intelbras Guardian."""

    VERSION = 1

    def __init__(self):
        """Initialize the config flow."""
        self._host: Optional[str] = None
        self._port: Optional[int] = None
        self._auth_url: Optional[str] = None
        self._client: Optional[GuardianApiClient] = None

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
        """Handle the initial step - API connection."""
        errors: Dict[str, str] = {}

        if user_input is not None:
            self._host = user_input[CONF_FASTAPI_HOST]
            self._port = user_input[CONF_FASTAPI_PORT]

            session = async_get_clientsession(self.hass)
            self._client = GuardianApiClient(
                host=self._host,
                port=self._port,
                session=session,
            )

            # Check if API is reachable
            if not await self._client.check_connection():
                errors["base"] = "cannot_connect"
            else:
                # Start OAuth flow
                oauth_data = await self._client.start_oauth()
                if oauth_data:
                    self._auth_url = oauth_data.get("auth_url")
                    return await self.async_step_oauth()
                else:
                    errors["base"] = "oauth_start_failed"

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_API_SCHEMA,
            errors=errors,
        )

    async def async_step_oauth(
        self,
        user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Handle OAuth step - show URL and receive callback."""
        errors: Dict[str, str] = {}

        if user_input is not None:
            callback_url = user_input.get("callback_url", "").strip()

            if callback_url:
                # Complete OAuth flow
                if await self._client.complete_oauth(callback_url):
                    # Get devices to verify everything works
                    devices = await self._client.get_devices()
                    device_count = len(devices)

                    if device_count == 0:
                        _LOGGER.warning("No devices found, but authentication succeeded")

                    # Create unique ID from session
                    await self.async_set_unique_id(f"guardian_{self._host}_{self._port}")
                    self._abort_if_unique_id_configured()

                    return self.async_create_entry(
                        title=f"Intelbras Guardian ({self._host})",
                        data={
                            CONF_FASTAPI_HOST: self._host,
                            CONF_FASTAPI_PORT: self._port,
                            CONF_SESSION_ID: self._client.session_id,
                        },
                    )
                else:
                    errors["base"] = "oauth_callback_failed"
            else:
                errors["base"] = "callback_url_required"

        return self.async_show_form(
            step_id="oauth",
            data_schema=STEP_OAUTH_SCHEMA,
            errors=errors,
            description_placeholders={
                "auth_url": self._auth_url or "",
                "api_url": f"http://{self._host}:{self._port}",
            },
        )


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options flow for Intelbras Guardian."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        # Note: self.config_entry is available from parent class in newer HA versions
        self._config_entry = config_entry
        self._devices: list = []
        self._selected_device_id: Optional[int] = None

    @property
    def _entry(self) -> config_entries.ConfigEntry:
        """Get config entry (compatible with all HA versions)."""
        # Try parent class property first (newer HA), fallback to our stored reference
        try:
            return super().config_entry
        except AttributeError:
            return self._config_entry

    async def async_step_init(
        self,
        user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Manage the options - show menu."""
        return self.async_show_menu(
            step_id="init",
            menu_options=["configure_device_password", "manage_zones", "reauth"],
        )

    async def async_step_reauth(
        self,
        user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Handle re-authentication via OAuth."""
        errors: Dict[str, str] = {}

        # Get coordinator from hass.data
        coordinator = self.hass.data.get(DOMAIN, {}).get(self._entry.entry_id)

        if user_input is not None:
            callback_url = user_input.get("callback_url", "").strip()

            if callback_url and coordinator:
                # Complete OAuth flow
                if await coordinator.client.complete_oauth(callback_url):
                    # Update config entry with new session_id
                    self.hass.config_entries.async_update_entry(
                        self._entry,
                        data={
                            **self._entry.data,
                            CONF_SESSION_ID: coordinator.client.session_id,
                        },
                    )
                    await coordinator.async_request_refresh()
                    return self.async_create_entry(title="", data={})
                else:
                    errors["base"] = "oauth_callback_failed"
            else:
                errors["base"] = "callback_url_required"

        # Start OAuth flow
        auth_url = ""
        if coordinator:
            oauth_data = await coordinator.client.start_oauth()
            if oauth_data:
                auth_url = oauth_data.get("auth_url", "")

        host = self._entry.data.get(CONF_FASTAPI_HOST, "")
        port = self._entry.data.get(CONF_FASTAPI_PORT, DEFAULT_FASTAPI_PORT)

        return self.async_show_form(
            step_id="reauth",
            data_schema=STEP_OAUTH_SCHEMA,
            errors=errors,
            description_placeholders={
                "auth_url": auth_url,
                "api_url": f"http://{host}:{port}",
            },
        )

    async def async_step_configure_device_password(
        self,
        user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Handle device password configuration."""
        errors: Dict[str, str] = {}

        # Get coordinator from hass.data
        coordinator = self.hass.data.get(DOMAIN, {}).get(self._entry.entry_id)
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

        coordinator = self.hass.data.get(DOMAIN, {}).get(self._entry.entry_id)
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
                "webui_url": f"http://{self._entry.data[CONF_FASTAPI_HOST]}:{self._entry.data[CONF_FASTAPI_PORT]}"
            },
        )


class CannotConnect(Exception):
    """Error to indicate we cannot connect."""


class InvalidAuth(Exception):
    """Error to indicate there is invalid auth."""
