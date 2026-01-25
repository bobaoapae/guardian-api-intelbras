"""Data update coordinator for Intelbras Guardian."""
import asyncio
import logging
from datetime import timedelta
from typing import Any, Dict, List, Optional

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)

from .api_client import GuardianApiClient
from .const import DEFAULT_SCAN_INTERVAL, DOMAIN, EVENT_ALARM

_LOGGER = logging.getLogger(__name__)


class GuardianCoordinator(DataUpdateCoordinator):
    """Coordinator for fetching data from Intelbras Guardian API."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: GuardianApiClient,
        entry: ConfigEntry,
    ):
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
        )
        self.client = client
        self.entry = entry
        self._last_event_id: Optional[int] = None

        # SSE listener
        self._sse_task: Optional[asyncio.Task] = None
        self._sse_stop_event: Optional[asyncio.Event] = None

    async def start_sse_listener(self) -> None:
        """Start SSE listener for real-time events."""
        if self._sse_task is not None:
            _LOGGER.debug("SSE listener already running")
            return

        if not self.client.session_id:
            _LOGGER.debug("Cannot start SSE: not authenticated")
            return

        self._sse_stop_event = asyncio.Event()
        self._sse_task = asyncio.create_task(
            self.client.listen_sse_events(
                on_event=self._on_sse_event,
                stop_event=self._sse_stop_event,
            )
        )
        _LOGGER.info("SSE listener started for real-time alarm events")

    async def stop_sse_listener(self) -> None:
        """Stop SSE listener."""
        if self._sse_stop_event:
            self._sse_stop_event.set()

        if self._sse_task:
            self._sse_task.cancel()
            try:
                await self._sse_task
            except asyncio.CancelledError:
                pass
            self._sse_task = None

        self._sse_stop_event = None
        _LOGGER.debug("SSE listener stopped")

    @callback
    def _on_sse_event(self, event_data: Dict[str, Any]) -> None:
        """Handle SSE alarm event - trigger immediate refresh."""
        _LOGGER.info("SSE alarm event received, triggering refresh: %s", event_data)

        # Fire a Home Assistant event for automations
        self.hass.bus.async_fire(EVENT_ALARM, event_data)

        # Trigger an immediate coordinator refresh
        self.hass.async_create_task(self.async_request_refresh())

    async def _async_update_data(self) -> Dict[str, Any]:
        """Fetch data from API."""
        try:
            # Check if we have a valid session
            if not self.client.session_id:
                _LOGGER.warning(
                    "No active session. Please re-authenticate via integration options."
                )
                # Stop SSE listener if session expired
                await self.stop_sse_listener()
                return {
                    "devices": {},
                    "partitions": [],
                    "zones": [],
                    "events": [],
                    "new_events": [],
                    "last_event": None,
                    "needs_reauth": True,
                }

            # Get devices
            devices = await self.client.get_devices()
            if not devices:
                _LOGGER.warning("No devices found or session may be invalid")
                # Stop SSE listener if session seems invalid
                await self.stop_sse_listener()
                return {
                    "devices": {},
                    "partitions": [],
                    "zones": [],
                    "events": [],
                    "new_events": [],
                    "last_event": None,
                    "needs_reauth": True,
                }

            # Start SSE listener if not already running (for real-time events)
            if self._sse_task is None:
                await self.start_sse_listener()

            # Get events
            events = await self.client.get_events(limit=20)

            # Process devices into a more usable format
            processed_devices = {}
            all_partitions = []
            all_zones = []

            for device in devices:
                device_id = device.get("id")
                processed_devices[device_id] = device

                # Check if device is eletrificador
                model = device.get("model", "").upper()
                is_eletrificador = "ELC" in model or "ELETRIFICADOR" in model

                # Try to get real-time status using auto-sync (uses saved password)
                # This now also returns zones, eliminating need for separate /zones call
                status_zones = []
                if device.get("has_saved_password"):
                    try:
                        status = await self.client.get_alarm_status_auto(device_id)
                        if status:
                            # Update device with real-time status
                            processed_devices[device_id]["real_time_status"] = status
                            processed_devices[device_id]["arm_mode"] = status.get("arm_mode")
                            processed_devices[device_id]["is_armed"] = status.get("is_armed")
                            processed_devices[device_id]["is_triggered"] = status.get("is_triggered")

                            # Eletrificador-specific fields
                            if is_eletrificador:
                                processed_devices[device_id]["shock_enabled"] = status.get("shock_enabled")
                                processed_devices[device_id]["alarm_enabled"] = status.get("alarm_enabled")
                                processed_devices[device_id]["shock_triggered"] = status.get("shock_triggered")
                                processed_devices[device_id]["alarm_triggered"] = status.get("alarm_triggered")

                            # Update partitions_enabled from real-time status
                            if "partitions_enabled" in status:
                                processed_devices[device_id]["partitions_enabled"] = status.get("partitions_enabled")

                            # Update partition statuses from real-time data
                            if status.get("partitions"):
                                for rt_partition in status.get("partitions", []):
                                    for partition in device.get("partitions", []):
                                        if partition.get("id") == rt_partition.get("index"):
                                            partition["status"] = rt_partition.get("state")

                            # Get zones from status (avoids separate ISECNet call)
                            if status.get("zones"):
                                status_zones = status.get("zones", [])
                    except Exception as e:
                        _LOGGER.debug(f"Could not get real-time status for device {device_id}: {e}")

                # Extract partitions (only for non-eletrificadores)
                # Only add multiple partitions if partitions_enabled is explicitly True
                # This avoids creating "ghost" entities that become unavailable later
                if not is_eletrificador:
                    partitions_enabled = processed_devices[device_id].get("partitions_enabled")
                    device_partitions = device.get("partitions", [])

                    if partitions_enabled is True and len(device_partitions) > 1:
                        # Partitions explicitly enabled - add all partitions
                        for partition in device_partitions:
                            partition_copy = partition.copy()
                            partition_copy["device_id"] = device_id
                            partition_copy["device_mac"] = device.get("mac", "")
                            partition_copy["device_model"] = device.get("model", "")
                            all_partitions.append(partition_copy)
                    else:
                        # Partitions disabled or unknown - only use first partition
                        # This is the safest default to avoid orphan entities
                        if device_partitions:
                            partition = device_partitions[0].copy()
                            partition["device_id"] = device_id
                            partition["device_mac"] = device.get("mac", "")
                            partition["device_model"] = device.get("model", "")
                            partition["name"] = device.get("description", "Alarme")
                            # Use device-level arm_mode
                            partition["status"] = processed_devices[device_id].get("arm_mode")
                            all_partitions.append(partition)
                        else:
                            # Create a virtual partition
                            all_partitions.append({
                                "id": 0,
                                "device_id": device_id,
                                "device_mac": device.get("mac", ""),
                                "device_model": device.get("model", ""),
                                "name": device.get("description", "Alarme"),
                                "status": processed_devices[device_id].get("arm_mode"),
                            })

                # Get zones - prefer from status (already fetched), avoids extra ISECNet call
                if status_zones:
                    # Use zones from real-time status
                    for zone in status_zones:
                        all_zones.append({
                            "device_id": device_id,
                            "device_mac": device.get("mac", ""),
                            "index": zone.get("index", 0),
                            "name": zone.get("name", f"Zona {zone.get('index', 0) + 1:02d}"),
                            "is_open": zone.get("is_open", False),
                            "is_bypassed": zone.get("is_bypassed", False),
                        })
                else:
                    # Fallback: use zones from device data (cloud API)
                    device_zones = device.get("zones", [])
                    if device_zones:
                        # Try to calculate index from zone IDs (they are usually sequential)
                        # e.g., IDs 21135370, 21135371, ... correspond to indices 0, 1, ...
                        zone_ids = [z.get("id", 0) for z in device_zones if z.get("id")]
                        min_zone_id = min(zone_ids) if zone_ids else 0

                        for zone in device_zones:
                            zone["device_id"] = device_id
                            zone["device_mac"] = device.get("mac", "")
                            # Calculate index from ID (ID - min_ID = index)
                            if "index" not in zone and zone.get("id"):
                                zone["index"] = zone.get("id") - min_zone_id
                            elif "index" not in zone:
                                zone["index"] = 0
                            all_zones.append(zone)

            # Check for new events
            new_events = []
            if events and self._last_event_id is not None:
                for event in events:
                    if event.get("id", 0) > self._last_event_id:
                        new_events.append(event)

            if events:
                self._last_event_id = max(
                    e.get("id", 0) for e in events
                ) if events else None

            return {
                "devices": processed_devices,
                "partitions": all_partitions,
                "zones": all_zones,
                "events": events,
                "new_events": new_events,
                "last_event": events[0] if events else None,
            }

        except Exception as err:
            _LOGGER.error(f"Error fetching data: {err}")
            raise UpdateFailed(f"Error communicating with API: {err}") from err

    def get_device(self, device_id: int) -> Optional[Dict[str, Any]]:
        """Get a specific device from cached data."""
        if self.data:
            return self.data.get("devices", {}).get(device_id)
        return None

    def get_partition(
        self,
        device_id: int,
        partition_id: int
    ) -> Optional[Dict[str, Any]]:
        """Get a specific partition from cached data."""
        if self.data:
            for partition in self.data.get("partitions", []):
                if (partition.get("device_id") == device_id and
                    partition.get("id") == partition_id):
                    return partition
        return None

    def get_zone(
        self,
        device_id: int,
        zone_index: int
    ) -> Optional[Dict[str, Any]]:
        """Get a specific zone from cached data by index."""
        if self.data:
            for zone in self.data.get("zones", []):
                if (zone.get("device_id") == device_id and
                    zone.get("index") == zone_index):
                    return zone
        return None

    def get_zone_by_id(
        self,
        device_id: int,
        zone_id: int
    ) -> Optional[Dict[str, Any]]:
        """Get a specific zone from cached data by ID."""
        if self.data:
            for zone in self.data.get("zones", []):
                if (zone.get("device_id") == device_id and
                    zone.get("id") == zone_id):
                    return zone
        return None
