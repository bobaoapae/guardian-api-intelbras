"""Data update coordinator for Intelbras Guardian."""
import asyncio
import logging
import time
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

        # Stale triggered state timeout (10 minutes)
        self._triggered_timestamps: Dict[int, float] = {}
        self._triggered_timeout = 600  # seconds

        # Cloud API throttle: only fetch devices/events every N cycles
        self._cloud_api_interval = 30  # seconds
        self._cloud_api_counter = 0
        self._cached_devices: Optional[List] = None
        self._cached_events: Optional[List] = None

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
        """Handle SSE alarm event."""
        _LOGGER.info("SSE event received: %s", event_data)

        # Fire a Home Assistant event for automations
        self.hass.bus.async_fire(EVENT_ALARM, event_data)

        # If this is a state_changed event from our own command, apply immediately
        if event_data.get("event_type") == "state_changed":
            self._apply_state_change(event_data)
        else:
            # External event (cloud) - trigger full refresh
            self.hass.async_create_task(self.async_request_refresh())

    def _apply_state_change(self, event_data: Dict[str, Any]) -> None:
        """Apply a state change from command directly to cached data."""
        device_id = event_data.get("device_id")
        new_status = event_data.get("new_status")
        partition_id = event_data.get("partition_id")

        if not self.data or not new_status:
            return

        # Update partition status in cached data
        for partition in self.data.get("partitions", []):
            if partition.get("device_id") == device_id:
                if partition_id is None or partition.get("id") == partition_id:
                    partition["status"] = new_status

        # Update device-level arm_mode
        device = self.data.get("devices", {}).get(device_id)
        if device:
            device["arm_mode"] = new_status
            device["is_armed"] = new_status != "disarmed"

        # Notify HA that data changed (entities will read new state)
        self.async_set_updated_data(self.data)

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

            # Throttle cloud API calls (get_devices/get_events) to every
            # _cloud_api_interval seconds, while ISECNet status polls every cycle
            self._cloud_api_counter += 1
            cloud_cycles = max(1, int(self._cloud_api_interval / self.update_interval.total_seconds()))
            fetch_cloud = self._cached_devices is None or self._cloud_api_counter >= cloud_cycles

            if fetch_cloud:
                self._cloud_api_counter = 0
                devices = await self.client.get_devices()
                if not devices:
                    _LOGGER.warning("No devices found or session may be invalid")
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
                self._cached_devices = devices
                self._cached_events = await self.client.get_events(limit=20)
            else:
                devices = self._cached_devices

            events = self._cached_events or []

            # Start SSE listener if not already running (for real-time events)
            if self._sse_task is None:
                await self.start_sse_listener()

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

                            # Track connection unavailability (e.g., AMT legacy app blocking)
                            connection_unavailable = status.get("connection_unavailable", False)
                            processed_devices[device_id]["connection_unavailable"] = connection_unavailable
                            processed_devices[device_id]["last_updated"] = status.get("last_updated")

                            if connection_unavailable:
                                _LOGGER.warning(
                                    f"Device {device_id} connection unavailable - using last known state. "
                                    f"Message: {status.get('message')}"
                                )

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
                            # Note: ISECNet returns partitions with 'index' (0, 1, 2...)
                            # while cloud API returns partitions with large 'id' values
                            # We match by position in the list (index)
                            if status.get("partitions"):
                                device_partitions_list = device.get("partitions", [])
                                for rt_partition in status.get("partitions", []):
                                    rt_index = rt_partition.get("index", 0)
                                    if rt_index < len(device_partitions_list):
                                        device_partitions_list[rt_index]["status"] = rt_partition.get("state")
                                        _LOGGER.debug(
                                            f"Updated partition {rt_index} status to {rt_partition.get('state')}"
                                        )

                            # Get zones from status (avoids separate ISECNet call)
                            if status.get("zones"):
                                status_zones = status.get("zones", [])
                    except Exception as e:
                        _LOGGER.debug(f"Could not get real-time status for device {device_id}: {e}")

                # Extract partitions (only for non-eletrificadores)
                # Trust the partitions returned by the API, unless partitions_enabled is explicitly False
                if not is_eletrificador:
                    partitions_enabled = processed_devices[device_id].get("partitions_enabled")
                    device_partitions = device.get("partitions", [])

                    # Show multiple partitions if:
                    # - partitions_enabled is True (confirmed by ISECNet), OR
                    # - partitions_enabled is None (unknown) AND API returned multiple partitions
                    # Only collapse to single partition if partitions_enabled is explicitly False
                    if partitions_enabled is not False and len(device_partitions) > 1:
                        # Multiple partitions - add all
                        for partition in device_partitions:
                            partition_copy = partition.copy()
                            partition_copy["device_id"] = device_id
                            partition_copy["device_mac"] = device.get("mac", "")
                            partition_copy["device_model"] = device.get("model", "")
                            all_partitions.append(partition_copy)
                    elif device_partitions:
                        # Single partition or partitions explicitly disabled
                        partition = device_partitions[0].copy()
                        partition["device_id"] = device_id
                        partition["device_mac"] = device.get("mac", "")
                        partition["device_model"] = device.get("model", "")
                        if partitions_enabled is False or len(device_partitions) == 1:
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

            # Check for stale triggered state (10-minute timeout)
            now = time.time()
            for device_id, device in processed_devices.items():
                is_triggered = device.get("is_triggered", False)
                if is_triggered:
                    if device_id not in self._triggered_timestamps:
                        self._triggered_timestamps[device_id] = now
                    elif now - self._triggered_timestamps[device_id] > self._triggered_timeout:
                        _LOGGER.info(
                            "Device %d triggered state timed out after %d seconds, clearing",
                            device_id, self._triggered_timeout
                        )
                        device["is_triggered"] = False
                        # Also clear partition-level triggered if any
                        for partition in all_partitions:
                            if partition.get("device_id") == device_id:
                                if partition.get("status") == "triggered":
                                    partition["status"] = device.get("arm_mode", "disarmed")
                        del self._triggered_timestamps[device_id]
                else:
                    self._triggered_timestamps.pop(device_id, None)

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
