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

        # Triggered state safety timeout: only clears `is_triggered` when the API
        # has NOT confirmed it during the configured window — protects against
        # SSE-orphan triggers (set by SSE but never confirmed by polling).
        self._triggered_first_seen: Dict[int, float] = {}
        self._triggered_timeout = 120  # seconds (was 600; now only for SSE-orphans)

        # Snapshots taken when device first enters triggered state. Used to
        # preserve the armed mode/status the panel was in before the trigger,
        # because the AMT_2018_E_SMART zeroes the partition status byte during
        # an active alarm. Cleared on the True→False transition.
        self._pre_trigger_arm_mode: Dict[int, str] = {}
        self._pre_trigger_partition_status: Dict[int, Dict[int, str]] = {}

        # SSE-detected triggered state protection: prevents ISECNet polling
        # from clearing triggered state before HA automations can react.
        # Maps device_id -> expiry timestamp
        self._sse_triggered_until: Dict[int, float] = {}

        # "Phantom" / stuck-trigger detection: the AMT_2018_E_SMART central
        # latches `is_triggered=True` until an explicit disarm. If the
        # monitoring centre silences the siren remotely (without disarming),
        # the alarm physically stops but HA stays stuck on TRIGGERED. Track
        # how long the device has reported triggered while no zone is
        # actually in alarm any more, so the coordinator can release the
        # phantom trigger after a grace period.
        self._phantom_trigger_since: Dict[int, float] = {}
        self._phantom_trigger_grace = 90  # seconds with is_triggered=True and no zones_in_alarm

        # Previous zone open states for edge detection (zone events)
        self._prev_zone_open: Dict[tuple, bool] = {}

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
            # If this is an alarm trigger event, set triggered state immediately
            # so HA automations can react even if the alarm is disarmed quickly
            if event_data.get("is_alarm") and self.data:
                self._apply_alarm_trigger(event_data)

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

            # If a re-arm command lands while the device is still triggered,
            # update the pre-trigger memory so templates depending on the
            # `pre_trigger_arm_mode` attribute reflect the user's new intent.
            # Also refresh the partition snapshot so the per-partition restore
            # in the next poll cycle uses the new mode (otherwise the
            # preservation logic would overwrite it with the old armed value).
            if device.get("is_triggered") and isinstance(new_status, str) and new_status.startswith("armed"):
                self._pre_trigger_arm_mode[device_id] = new_status
                new_snapshot: Dict[int, str] = {}
                for idx, partition in enumerate(
                    p for p in self.data.get("partitions", []) if p.get("device_id") == device_id
                ):
                    s = partition.get("status")
                    if isinstance(s, str) and s.startswith("armed"):
                        new_snapshot[idx] = s
                if new_snapshot:
                    self._pre_trigger_partition_status[device_id] = new_snapshot

        # Notify HA that data changed (entities will read new state)
        self.async_set_updated_data(self.data)

    def _apply_alarm_trigger(self, event_data: Dict[str, Any]) -> None:
        """Apply alarm trigger from SSE event to set triggered state immediately.

        When a "Disparo de Setor" (or similar alarm event) arrives via SSE,
        set the device as triggered right away. This ensures HA automations
        watching for the 'triggered' state can react, even if the alarm is
        disarmed on the keypad within seconds (before the next ISECNet poll).
        """
        device_id = event_data.get("device_id")
        if not device_id:
            return

        # Ensure device_id is the right type for our dict lookup
        device = self.data.get("devices", {}).get(device_id)
        if not device:
            try:
                device_id = int(device_id)
                device = self.data.get("devices", {}).get(device_id)
            except (ValueError, TypeError):
                pass

        if not device:
            _LOGGER.warning("SSE alarm trigger for unknown device %s", device_id)
            return

        _LOGGER.info(
            "Alarm trigger detected via SSE for device %s: %s",
            device_id, event_data.get("event_name"),
        )

        # Snapshot pre-trigger state on transition False -> True so templates
        # and the unified panel can recover the armed mode the panel was in
        # before the alarm fired (the AMT_2018_E_SMART zeroes the partition
        # byte during an active alarm, which would otherwise be lost).
        if not device.get("is_triggered"):
            prev_arm_mode = device.get("arm_mode")
            if isinstance(prev_arm_mode, str) and prev_arm_mode.startswith("armed"):
                self._pre_trigger_arm_mode[device_id] = prev_arm_mode
            partition_snapshot: Dict[int, str] = {}
            for idx, partition in enumerate(
                p for p in self.data.get("partitions", []) if p.get("device_id") == device_id
            ):
                status = partition.get("status")
                if isinstance(status, str) and status.startswith("armed"):
                    partition_snapshot[idx] = status
            if partition_snapshot:
                self._pre_trigger_partition_status[device_id] = partition_snapshot

        # Set device as triggered immediately
        device["is_triggered"] = True
        self._triggered_first_seen.setdefault(device_id, time.time())

        # Protect triggered state from being cleared by ISECNet polling
        # for 30 seconds — enough for HA automations to detect the change
        self._sse_triggered_until[device_id] = time.time() + 30

        # Update last_event so sensor.casa_last_event also updates immediately
        zone = event_data.get("zone")
        self.data["last_event"] = {
            "id": event_data.get("id"),
            "timestamp": event_data.get("timestamp"),
            "event_type": event_data.get("event_name"),
            "notification": {
                "title": event_data.get("event_name"),
                "message": event_data.get("device_name"),
            },
            "zone": zone,
            "partition_id": event_data.get("partition_id"),
            "device_id": device_id,
        }

        # Notify HA that data changed — entities will see triggered state
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

                # Capture previous coordinator-tracked state BEFORE the API
                # response overwrites it. Used by the Bug 3 preservation logic
                # below (snapshot pre-trigger arm mode / partition status).
                prev_device_data = (self.data or {}).get("devices", {}).get(device_id, {})
                prev_is_triggered = bool(prev_device_data.get("is_triggered"))
                prev_arm_mode = prev_device_data.get("arm_mode")
                prev_partition_statuses: Dict[int, str] = {}
                for p_idx, prev_partition in enumerate(
                    p for p in (self.data or {}).get("partitions", [])
                    if p.get("device_id") == device_id
                ):
                    p_status = prev_partition.get("status")
                    if isinstance(p_status, str):
                        prev_partition_statuses[p_idx] = p_status

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

                            # Protect SSE-detected triggered state from being
                            # cleared by ISECNet polling before HA can react
                            if device_id in self._sse_triggered_until:
                                if time.time() < self._sse_triggered_until[device_id]:
                                    processed_devices[device_id]["is_triggered"] = True
                                else:
                                    del self._sse_triggered_until[device_id]

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

                            # Bug 3 preservation: AMT_2018_E_SMART zeros the
                            # partition byte during an active alarm (data[22]=0x00),
                            # so the parser reports arm_mode="disarmed" even with
                            # the siren sounding. Snapshot the pre-trigger mode
                            # on the False->True transition and restore it from
                            # memory while the device stays triggered.
                            if processed_devices[device_id].get("is_triggered"):
                                if not prev_is_triggered:
                                    if device_id not in self._pre_trigger_arm_mode:
                                        if isinstance(prev_arm_mode, str) and prev_arm_mode.startswith("armed"):
                                            self._pre_trigger_arm_mode[device_id] = prev_arm_mode
                                    if device_id not in self._pre_trigger_partition_status:
                                        armed_prev = {
                                            idx: s for idx, s in prev_partition_statuses.items()
                                            if isinstance(s, str) and s.startswith("armed")
                                        }
                                        if armed_prev:
                                            self._pre_trigger_partition_status[device_id] = armed_prev

                                pre_mode = self._pre_trigger_arm_mode.get(device_id)
                                if pre_mode and processed_devices[device_id].get("arm_mode") == "disarmed":
                                    processed_devices[device_id]["arm_mode"] = pre_mode
                                    processed_devices[device_id]["is_armed"] = True

                                snapshot = self._pre_trigger_partition_status.get(device_id, {})
                                if snapshot:
                                    partitions_to_restore = device.get("partitions", [])
                                    for idx, prev_status in snapshot.items():
                                        if idx < len(partitions_to_restore):
                                            current = partitions_to_restore[idx].get("status")
                                            if (current is None or current == "disarmed") and \
                                               isinstance(prev_status, str) and prev_status.startswith("armed"):
                                                partitions_to_restore[idx]["status"] = prev_status

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
                            # Wireless sensor data (only present for smart panels)
                            "is_wireless": zone.get("is_wireless", False),
                            "battery_low": zone.get("battery_low", False),
                            "signal_strength": zone.get("signal_strength"),
                            "tamper": zone.get("tamper", False),
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

            # Triggered safety timeout (Bug 2): only clears `is_triggered`
            # when the API has NOT confirmed it during the configured window.
            # The previous unconditional 10-minute timeout caused a flap
            # `triggered -> disarmed -> triggered` every 10 minutes whenever
            # the alarm stayed on for that long, because the API kept
            # reporting True while the coordinator forced it back to False.
            # The timeout still serves as a safety net for SSE-orphan triggers
            # (set by SSE but never confirmed by the polling path).
            #
            # Phantom-trigger release (Bug 5): the AMT_2018_E_SMART latches
            # `is_triggered=True` until an explicit disarm, even after the
            # siren is silenced and every zone exits the alarm state. If the
            # API confirms the trigger but no zone reports `is_in_alarm` for
            # `_phantom_trigger_grace` seconds, treat the trigger as cleared
            # — otherwise HA stays stuck on TRIGGERED for hours, generating
            # repeated automation runs and notifications.
            now = time.time()
            for device_id, device in processed_devices.items():
                if device.get("is_triggered"):
                    rt = device.get("real_time_status") or {}
                    api_confirmed = bool(rt.get("is_triggered"))
                    api_zones = rt.get("zones") or []
                    # Both signals are needed because we do not know which
                    # the AMT_2018_E_SMART latches:
                    #   - `is_in_alarm`: zone-level alarm flag from the
                    #     central (may latch until disarm on some firmwares)
                    #   - `is_open`: zone currently tripped (PIR zones go
                    #     back to False seconds after motion ends; magnetic
                    #     zones stay True until physically closed)
                    # If either still indicates activity we treat the
                    # trigger as genuine; only when both are False for the
                    # grace window do we release the latched trigger.
                    any_in_alarm = any(z.get("is_in_alarm") for z in api_zones)
                    any_open = any(
                        z.get("is_open") and not z.get("is_bypassed")
                        for z in api_zones
                    )
                    if api_confirmed and (any_in_alarm or any_open):
                        # Genuine ongoing alarm — refresh both timers.
                        self._triggered_first_seen[device_id] = now
                        self._phantom_trigger_since.pop(device_id, None)
                    elif api_confirmed:
                        # API still reports triggered but no zone is in
                        # alarm or open any more — start (or continue) the
                        # phantom-trigger countdown. Refresh first_seen so
                        # the SSE-orphan timeout does not also fire here.
                        self._triggered_first_seen[device_id] = now
                        first_phantom = self._phantom_trigger_since.setdefault(device_id, now)
                        if now - first_phantom > self._phantom_trigger_grace:
                            _LOGGER.info(
                                "Device %s phantom trigger released after %.0fs (API still reports triggered but no zone in alarm/open)",
                                device_id, now - first_phantom,
                            )
                            device["is_triggered"] = False
                            for partition in all_partitions:
                                if partition.get("device_id") == device_id:
                                    if partition.get("status") == "triggered":
                                        partition["status"] = device.get("arm_mode", "disarmed")
                            self._triggered_first_seen.pop(device_id, None)
                            self._phantom_trigger_since.pop(device_id, None)
                            self._pre_trigger_arm_mode.pop(device_id, None)
                            self._pre_trigger_partition_status.pop(device_id, None)
                    else:
                        # SSE-only trigger that API never confirmed.
                        self._phantom_trigger_since.pop(device_id, None)
                        first_seen = self._triggered_first_seen.setdefault(device_id, now)
                        if now - first_seen > self._triggered_timeout:
                            _LOGGER.info(
                                "Device %s SSE-orphan trigger (no API confirmation) timed out after %ds, clearing",
                                device_id, self._triggered_timeout,
                            )
                            device["is_triggered"] = False
                            for partition in all_partitions:
                                if partition.get("device_id") == device_id:
                                    if partition.get("status") == "triggered":
                                        partition["status"] = device.get("arm_mode", "disarmed")
                            self._triggered_first_seen.pop(device_id, None)
                            self._pre_trigger_arm_mode.pop(device_id, None)
                            self._pre_trigger_partition_status.pop(device_id, None)
                else:
                    # Cleanup snapshots once the device leaves the triggered state
                    self._triggered_first_seen.pop(device_id, None)
                    self._phantom_trigger_since.pop(device_id, None)
                    self._pre_trigger_arm_mode.pop(device_id, None)
                    self._pre_trigger_partition_status.pop(device_id, None)

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

            # Build lookup indexes for O(1) access by entities
            zone_index = {}
            for zone in all_zones:
                key = (zone.get("device_id"), zone.get("index"))
                zone_index[key] = zone
            partition_index = {}
            for partition in all_partitions:
                key = (partition.get("device_id"), partition.get("id"))
                partition_index[key] = partition

            # Detect zone open transitions (closed->open) for event entities
            zone_triggered: List[tuple] = []
            for zone in all_zones:
                key = (zone.get("device_id"), zone.get("index"))
                is_open = zone.get("is_open", False)
                was_open = self._prev_zone_open.get(key, False)
                if is_open and not was_open:
                    zone_triggered.append(key)
                self._prev_zone_open[key] = is_open

            return {
                "devices": processed_devices,
                "partitions": all_partitions,
                "zones": all_zones,
                "events": events,
                "new_events": new_events,
                "last_event": events[0] if events else None,
                "_zone_index": zone_index,
                "_partition_index": partition_index,
                "_zone_triggered": zone_triggered,
            }

        except Exception as err:
            _LOGGER.error(f"Error fetching data: {err}")
            raise UpdateFailed(f"Error communicating with API: {err}") from err

    async def async_refresh_device(self, device_id: int) -> None:
        """Refresh only a single device's status without touching other devices.

        This avoids the problem where a full coordinator refresh rebuilds ALL
        device data from scratch, potentially causing transient incorrect states
        on unrelated devices (e.g., eletrificador refresh corrupting alarm state).
        """
        if not self.data:
            return

        device = self.data.get("devices", {}).get(device_id)
        if not device or not device.get("has_saved_password"):
            return

        try:
            status = await self.client.get_alarm_status_auto(device_id)
            if not status:
                return

            # Update only this device's fields in-place
            device["arm_mode"] = status.get("arm_mode")
            device["is_armed"] = status.get("is_armed")
            device["is_triggered"] = status.get("is_triggered")
            device["connection_unavailable"] = status.get("connection_unavailable", False)
            device["last_updated"] = status.get("last_updated")

            # Eletrificador-specific fields
            model = device.get("model", "").upper()
            if "ELC" in model or "ELETRIFICADOR" in model:
                device["shock_enabled"] = status.get("shock_enabled")
                device["alarm_enabled"] = status.get("alarm_enabled")
                device["shock_triggered"] = status.get("shock_triggered")
                device["alarm_triggered"] = status.get("alarm_triggered")

            # Notify entities that data changed (without rebuilding everything)
            self.async_set_updated_data(self.data)

        except Exception as e:
            _LOGGER.debug(f"Could not refresh device {device_id}: {e}")

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
            index = self.data.get("_partition_index")
            if index:
                return index.get((device_id, partition_id))
        return None

    def get_zone(
        self,
        device_id: int,
        zone_index: int
    ) -> Optional[Dict[str, Any]]:
        """Get a specific zone from cached data by index."""
        if self.data:
            index = self.data.get("_zone_index")
            if index:
                return index.get((device_id, zone_index))
        return None

    def get_zone_by_id(
        self,
        device_id: int,
        zone_id: int
    ) -> Optional[Dict[str, Any]]:
        """Get a specific zone from cached data by ID."""
        if self.data:
            # zone_by_id is less common, fall back to linear scan
            for zone in self.data.get("zones", []):
                if (zone.get("device_id") == device_id and
                    zone.get("id") == zone_id):
                    return zone
        return None
