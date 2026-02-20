"""The Intelbras Guardian integration."""
import asyncio
import logging
import time

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, Event
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.exceptions import ConfigEntryAuthFailed

from .api_client import GuardianApiClient
from .const import CONF_FASTAPI_HOST, CONF_FASTAPI_PORT, CONF_SESSION_ID, DOMAIN, PLATFORMS
from .coordinator import GuardianCoordinator

_LOGGER = logging.getLogger(__name__)

# Staleness threshold for pending bypass context (seconds)
_BYPASS_STALE_TIMEOUT = 300  # 5 minutes


def _get_mobile_notify_services(hass: HomeAssistant) -> list[str]:
    """Discover mobile_app notify services."""
    services = hass.services.async_services().get("notify", {})
    return [name for name in services if name.startswith("mobile_app_")]


async def _send_bypass_notification(
    hass: HomeAssistant,
    device_id: int,
    arm_type: str,
    open_zones: list,
) -> None:
    """Send actionable notification to all mobile_app devices."""
    zone_names = []
    for zone in open_zones:
        if isinstance(zone, dict):
            name = zone.get("friendly_name") or zone.get("name") or f"Zona {zone.get('index', '?') + 1}"
        else:
            name = str(zone)
        if name not in zone_names:
            zone_names.append(name)

    zones_list = "\n".join(f"  - {z}" for z in zone_names)
    mode_label = "Ausente" if arm_type == "away" else "Em Casa"
    body = f"Nao foi possivel armar ({mode_label}). Zonas abertas:\n{zones_list}"
    action_tag = f"IG_BYPASS_ARM_{device_id}_{arm_type}"
    notification_tag = f"ig_arm_fail_{device_id}"

    mobile_services = _get_mobile_notify_services(hass)
    if not mobile_services:
        _LOGGER.warning("No mobile_app notify services found, bypass notification not sent")
        return

    for service_name in mobile_services:
        try:
            await hass.services.async_call(
                "notify",
                service_name,
                {
                    "message": body,
                    "title": "Alarme: Zonas Abertas",
                    "data": {
                        "tag": notification_tag,
                        "actions": [
                            {
                                "action": action_tag,
                                "title": "Ignorar Zonas e Armar",
                            }
                        ],
                    },
                },
            )
        except Exception as e:
            _LOGGER.warning(f"Failed to send bypass notification to {service_name}: {e}")


async def _dismiss_bypass_notification(hass: HomeAssistant, device_id: int) -> None:
    """Dismiss the bypass notification on all mobile devices."""
    notification_tag = f"ig_arm_fail_{device_id}"
    mobile_services = _get_mobile_notify_services(hass)

    for service_name in mobile_services:
        try:
            await hass.services.async_call(
                "notify",
                service_name,
                {
                    "message": "clear_notification",
                    "data": {"tag": notification_tag},
                },
            )
        except Exception:
            pass


async def _execute_bypass_and_rearm(
    hass: HomeAssistant,
    entry: ConfigEntry,
    device_id: int,
    arm_type: str,
) -> None:
    """Bypass all pending open zones and re-arm the alarm."""
    domain_data = hass.data.get(DOMAIN, {})
    pending = domain_data.get("pending_bypass", {}).get(device_id)

    if not pending:
        _LOGGER.warning(f"No pending bypass context for device {device_id}")
        return

    # Check staleness
    elapsed = time.monotonic() - pending.get("timestamp", 0)
    zone_indices = pending.get("zone_indices", [])
    entity_type = pending.get("entity_type", "unified")

    # Find the alarm entity
    alarm_entities = domain_data.get("alarm_entities", {})
    if entity_type == "unified":
        entity = alarm_entities.get((device_id, "unified"))
    else:
        # Individual entities are keyed by (device_id, "individual", partition_id)
        partition_id = pending.get("partition_id")
        entity = alarm_entities.get((device_id, "individual", partition_id))

    if not entity:
        # Try the other entity type as fallback
        fallback_entity = alarm_entities.get((device_id, "unified"))
        if not fallback_entity:
            # Search for any individual entity of this device
            for key, ent in alarm_entities.items():
                if key[0] == device_id and len(key) >= 2 and key[1] == "individual":
                    fallback_entity = ent
                    break
        entity = fallback_entity

    if not entity:
        _LOGGER.error(f"Could not find alarm entity for device {device_id} (type={entity_type})")
        await hass.services.async_call(
            "persistent_notification", "create",
            {"message": f"Erro interno: entidade do alarme nao encontrada (device {device_id})",
             "title": "Erro ao Ignorar Zonas",
             "notification_id": f"ig_bypass_error_{device_id}"},
        )
        return

    coordinator: GuardianCoordinator = hass.data[DOMAIN].get(entry.entry_id)
    if not coordinator:
        _LOGGER.error("Coordinator not found")
        return

    if elapsed > _BYPASS_STALE_TIMEOUT:
        _LOGGER.info(f"Bypass context stale ({elapsed:.0f}s > {_BYPASS_STALE_TIMEOUT}s), attempting arm without bypass")
    else:
        # Bypass zones via API
        _LOGGER.info(f"Bypassing zones {zone_indices} on device {device_id}")
        result = await coordinator.client.bypass_zones(device_id, zone_indices, bypass=True)

        if not result.get("success"):
            error_msg = result.get("error", "Erro desconhecido")
            _LOGGER.error(f"Bypass failed for device {device_id}: {error_msg}")
            await hass.services.async_call(
                "persistent_notification", "create",
                {"message": f"Falha ao ignorar zonas: {error_msg}",
                 "title": "Erro ao Ignorar Zonas",
                 "notification_id": f"ig_bypass_error_{device_id}"},
            )
            domain_data.get("pending_bypass", {}).pop(device_id, None)
            return

        _LOGGER.info(f"Bypass successful, waiting before re-arm...")
        await asyncio.sleep(0.5)

    # Re-arm
    try:
        if arm_type == "away":
            await entity.async_alarm_arm_away()
        else:
            await entity.async_alarm_arm_home()
        _LOGGER.info(f"Re-arm ({arm_type}) command sent for device {device_id}")
    except Exception as e:
        _LOGGER.error(f"Re-arm failed after bypass: {e}")
        await hass.services.async_call(
            "persistent_notification", "create",
            {"message": f"Zonas ignoradas, mas falha ao armar: {e}",
             "title": "Erro ao Armar Alarme",
             "notification_id": f"ig_bypass_error_{device_id}"},
        )
        domain_data.get("pending_bypass", {}).pop(device_id, None)
        return

    # Dismiss the mobile notification
    await _dismiss_bypass_notification(hass, device_id)

    # Clean up pending context
    domain_data.get("pending_bypass", {}).pop(device_id, None)


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

    # Register listener for options updates (to reload entities when unified alarm config changes)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    # Initialize bypass data structures
    hass.data[DOMAIN].setdefault("pending_bypass", {})
    hass.data[DOMAIN].setdefault("alarm_entities", {})

    # Register mobile_app notification action listener
    async def _handle_notification_action(event: Event) -> None:
        """Handle mobile_app actionable notification responses."""
        action = event.data.get("action", "")
        if not action.startswith("IG_BYPASS_ARM_"):
            return

        # Parse action: IG_BYPASS_ARM_{device_id}_{arm_type}
        parts = action.split("_")
        if len(parts) < 5:
            _LOGGER.warning(f"Invalid bypass action format: {action}")
            return

        try:
            action_device_id = int(parts[3])
            arm_type = parts[4]
        except (ValueError, IndexError):
            _LOGGER.warning(f"Could not parse bypass action: {action}")
            return

        if arm_type not in ("away", "home"):
            _LOGGER.warning(f"Invalid arm_type in bypass action: {arm_type}")
            return

        _LOGGER.info(f"Bypass+rearm action received: device={action_device_id}, arm_type={arm_type}")
        hass.async_create_task(
            _execute_bypass_and_rearm(hass, entry, action_device_id, arm_type)
        )

    cancel_listener = hass.bus.async_listen(
        "mobile_app_notification_action", _handle_notification_action
    )
    entry.async_on_unload(cancel_listener)

    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update - reload the integration to recreate entities."""
    _LOGGER.info("Options updated, reloading integration to apply changes")
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    # Stop SSE listener before unloading
    coordinator = hass.data[DOMAIN].get(entry.entry_id)
    if coordinator:
        await coordinator.stop_sse_listener()

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok
