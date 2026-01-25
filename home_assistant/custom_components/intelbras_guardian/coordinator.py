"""Data update coordinator for Intelbras Guardian."""
import logging
from datetime import timedelta
from typing import Any, Dict, List, Optional

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)

from .api_client import GuardianApiClient
from .const import DEFAULT_SCAN_INTERVAL, DOMAIN

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

    async def _async_update_data(self) -> Dict[str, Any]:
        """Fetch data from API."""
        try:
            # Get devices
            devices = await self.client.get_devices()
            if not devices:
                raise UpdateFailed("Failed to fetch devices")

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

                            # Update partition statuses from real-time data
                            if status.get("partitions"):
                                for rt_partition in status.get("partitions", []):
                                    for partition in device.get("partitions", []):
                                        if partition.get("id") == rt_partition.get("index"):
                                            partition["status"] = rt_partition.get("state")
                    except Exception as e:
                        _LOGGER.debug(f"Could not get real-time status for device {device_id}: {e}")

                # Extract partitions (only for non-eletrificadores)
                if not is_eletrificador:
                    for partition in device.get("partitions", []):
                        partition["device_id"] = device_id
                        partition["device_mac"] = device.get("mac", "")
                        partition["device_model"] = device.get("model", "")
                        all_partitions.append(partition)

                # Get zones with friendly names from zones API
                try:
                    if device.get("has_saved_password"):
                        zones_data = await self.client.get_zones(device_id)
                        if zones_data and zones_data.get("zones"):
                            for zone in zones_data.get("zones", []):
                                zone["device_id"] = device_id
                                zone["device_mac"] = device.get("mac", "")
                                all_zones.append(zone)
                        continue
                except Exception as e:
                    _LOGGER.debug(f"Could not get zones for device {device_id}: {e}")

                # Fallback: use zones from device data
                for zone in device.get("zones", []):
                    zone["device_id"] = device_id
                    zone["device_mac"] = device.get("mac", "")
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
