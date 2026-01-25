"""The Intelbras Guardian integration."""
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.exceptions import ConfigEntryAuthFailed

from .api_client import GuardianApiClient
from .const import CONF_FASTAPI_HOST, CONF_FASTAPI_PORT, CONF_SESSION_ID, DOMAIN, PLATFORMS
from .coordinator import GuardianCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Intelbras Guardian from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    # Create API client
    session = async_get_clientsession(hass)
    client = GuardianApiClient(
        host=entry.data[CONF_FASTAPI_HOST],
        port=entry.data[CONF_FASTAPI_PORT],
        session=session,
    )

    # Try to restore session first
    stored_session_id = entry.data.get(CONF_SESSION_ID)
    if stored_session_id:
        client.set_session_id(stored_session_id)
        if await client.check_session():
            _LOGGER.info("Restored existing session")
        else:
            _LOGGER.info("Stored session expired, re-authenticating")
            stored_session_id = None

    # Authenticate if no valid session
    if not stored_session_id:
        if not await client.authenticate(
            entry.data[CONF_USERNAME],
            entry.data[CONF_PASSWORD]
        ):
            _LOGGER.error("Failed to authenticate with Intelbras Guardian API")
            raise ConfigEntryAuthFailed("Authentication failed")

        # Store the new session ID
        new_data = {**entry.data, CONF_SESSION_ID: client.session_id}
        hass.config_entries.async_update_entry(entry, data=new_data)

    # Create coordinator
    coordinator = GuardianCoordinator(hass, client, entry)

    # Fetch initial data
    await coordinator.async_config_entry_first_refresh()

    # Store coordinator
    hass.data[DOMAIN][entry.entry_id] = coordinator

    # Set up platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok
