"""Config flow for Intelbras Guardian integration."""
import logging
from typing import Any, Dict, Optional

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
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


class CannotConnect(Exception):
    """Error to indicate we cannot connect."""


class InvalidAuth(Exception):
    """Error to indicate there is invalid auth."""
