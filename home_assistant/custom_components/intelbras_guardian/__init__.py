"""The Intelbras Guardian integration."""
import logging

from homeassistant.config_entries import ConfigEntry
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

    # Try to restore session
    stored_session_id = entry.data.get(CONF_SESSION_ID)
    if stored_session_id:
        client.set_session_id(stored_session_id)
        if await client.check_session():
            _LOGGER.info("Restored existing session")
        else:
            _LOGGER.warning("Stored session expired or invalid")
            # Session is invalid - user needs to re-authenticate via OAuth
            # The integration will still load but with limited functionality
            # User can re-authenticate via Options -> Re-authenticate
            client.set_session_id(None)

    if not client.session_id:
        _LOGGER.warning(
            "No valid session. Please re-authenticate via integration options "
            "(Settings -> Devices & Services -> Intelbras Guardian -> Configure -> Re-authenticate)"
        )
        # We still set up the integration so user can re-authenticate
        # The coordinator will handle the missing session gracefully

    # Create coordinator
    coordinator = GuardianCoordinator(hass, client, entry)

    # Store coordinator first so options flow can access it
    hass.data[DOMAIN][entry.entry_id] = coordinator

    # Fetch initial data (will fail gracefully if not authenticated)
    try:
        await coordinator.async_config_entry_first_refresh()
    except ConfigEntryAuthFailed:
        _LOGGER.warning(
            "Authentication required. Please use Options -> Re-authenticate"
        )
        # Don't raise - let the integration load so user can re-auth

    # Set up platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok
