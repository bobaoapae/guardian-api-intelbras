"""API client for FastAPI middleware."""
import logging
from typing import Any, Dict, List, Optional
import aiohttp
import async_timeout

_LOGGER = logging.getLogger(__name__)


class GuardianApiClient:
    """Client for communicating with FastAPI middleware."""

    def __init__(
        self,
        host: str,
        port: int,
        session: aiohttp.ClientSession,
        timeout: int = 30
    ):
        """Initialize the API client."""
        self._host = host
        self._port = port
        self._session = session
        self._timeout = timeout
        self._session_id: Optional[str] = None
        self._base_url = f"http://{host}:{port}"

    @property
    def session_id(self) -> Optional[str]:
        """Get current session ID."""
        return self._session_id

    async def authenticate(self, username: str, password: str) -> bool:
        """Authenticate with the API."""
        try:
            async with async_timeout.timeout(self._timeout):
                response = await self._session.post(
                    f"{self._base_url}/api/v1/auth/login",
                    json={"username": username, "password": password}
                )

                if response.status == 200:
                    data = await response.json()
                    self._session_id = data.get("session_id")
                    _LOGGER.info("Authentication successful")
                    return True
                else:
                    error = await response.text()
                    _LOGGER.error(f"Authentication failed: {error}")
                    return False

        except aiohttp.ClientError as e:
            _LOGGER.error(f"Connection error during authentication: {e}")
            return False
        except Exception as e:
            _LOGGER.error(f"Unexpected error during authentication: {e}")
            return False

    async def _request(
        self,
        method: str,
        endpoint: str,
        data: Optional[Dict[str, Any]] = None
    ) -> Optional[Dict[str, Any]]:
        """Make an API request."""
        if not self._session_id:
            _LOGGER.error("Not authenticated")
            return None

        headers = {"X-Session-ID": self._session_id}

        try:
            async with async_timeout.timeout(self._timeout):
                if method == "GET":
                    response = await self._session.get(
                        f"{self._base_url}{endpoint}",
                        headers=headers
                    )
                elif method == "POST":
                    response = await self._session.post(
                        f"{self._base_url}{endpoint}",
                        headers=headers,
                        json=data or {}
                    )
                elif method == "PUT":
                    response = await self._session.put(
                        f"{self._base_url}{endpoint}",
                        headers=headers,
                        json=data or {}
                    )
                elif method == "DELETE":
                    response = await self._session.delete(
                        f"{self._base_url}{endpoint}",
                        headers=headers
                    )
                else:
                    return None

                if response.status == 200:
                    return await response.json()
                elif response.status == 401:
                    _LOGGER.warning("Session expired")
                    self._session_id = None
                    return None
                else:
                    error = await response.text()
                    _LOGGER.error(f"API error {response.status}: {error}")
                    return None

        except aiohttp.ClientError as e:
            _LOGGER.error(f"Connection error: {e}")
            return None
        except Exception as e:
            _LOGGER.error(f"Request error: {e}")
            return None

    async def get_devices(self) -> List[Dict[str, Any]]:
        """Get list of devices."""
        result = await self._request("GET", "/api/v1/devices")
        if result:
            return result.get("devices", [])
        return []

    async def get_device(self, device_id: int) -> Optional[Dict[str, Any]]:
        """Get a specific device."""
        return await self._request("GET", f"/api/v1/devices/{device_id}")

    async def save_device_password(self, device_id: int, password: str) -> bool:
        """Save password for a device."""
        result = await self._request(
            "POST",
            f"/api/v1/devices/{device_id}/password",
            {"password": password}
        )
        return result is not None and result.get("success", False)

    async def delete_device_password(self, device_id: int) -> bool:
        """Delete saved password for a device."""
        result = await self._request(
            "DELETE",
            f"/api/v1/devices/{device_id}/password"
        )
        return result is not None and result.get("success", False)

    async def get_alarm_status_auto(self, device_id: int) -> Optional[Dict[str, Any]]:
        """Get alarm status using saved password (auto-sync)."""
        return await self._request("GET", f"/api/v1/alarm/{device_id}/status/auto")

    async def get_alarm_status(self, device_id: int, password: str) -> Optional[Dict[str, Any]]:
        """Get alarm status with explicit password."""
        return await self._request(
            "POST",
            f"/api/v1/alarm/{device_id}/status",
            {"password": password}
        )

    async def arm_partition(
        self,
        device_id: int,
        partition_id: int,
        mode: str = "away"
    ) -> bool:
        """Arm a partition."""
        result = await self._request(
            "POST",
            f"/api/v1/alarm/{device_id}/arm",
            {"partition_id": partition_id, "mode": mode}
        )
        return result is not None and result.get("success", False)

    async def disarm_partition(
        self,
        device_id: int,
        partition_id: int
    ) -> bool:
        """Disarm a partition."""
        result = await self._request(
            "POST",
            f"/api/v1/alarm/{device_id}/disarm",
            {"partition_id": partition_id}
        )
        return result is not None and result.get("success", False)

    async def get_events(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Get recent events."""
        result = await self._request("GET", f"/api/v1/events?limit={limit}")
        if result:
            return result.get("events", [])
        return []

    # Zone management
    async def get_zones(self, device_id: int) -> Optional[Dict[str, Any]]:
        """Get zones for a device with friendly names and status."""
        return await self._request("GET", f"/api/v1/zones/{device_id}")

    async def update_zone_friendly_name(
        self,
        device_id: int,
        zone_index: int,
        friendly_name: str
    ) -> bool:
        """Update friendly name for a zone."""
        result = await self._request(
            "PUT",
            f"/api/v1/zones/{device_id}/{zone_index}/friendly_name",
            {"friendly_name": friendly_name}
        )
        return result is not None and result.get("success", False)

    async def delete_zone_friendly_name(self, device_id: int, zone_index: int) -> bool:
        """Delete friendly name for a zone."""
        result = await self._request(
            "DELETE",
            f"/api/v1/zones/{device_id}/{zone_index}/friendly_name"
        )
        return result is not None and result.get("success", False)

    # Eletrificador (electric fence) controls
    async def eletrificador_shock_on(self, device_id: int) -> bool:
        """Turn on electric fence shock."""
        result = await self._request(
            "POST",
            f"/api/v1/alarm/{device_id}/eletrificador/shock/on"
        )
        return result is not None and result.get("success", False)

    async def eletrificador_shock_off(self, device_id: int) -> bool:
        """Turn off electric fence shock."""
        result = await self._request(
            "POST",
            f"/api/v1/alarm/{device_id}/eletrificador/shock/off"
        )
        return result is not None and result.get("success", False)

    async def eletrificador_alarm_activate(self, device_id: int) -> bool:
        """Activate electric fence alarm."""
        result = await self._request(
            "POST",
            f"/api/v1/alarm/{device_id}/eletrificador/activate"
        )
        return result is not None and result.get("success", False)

    async def eletrificador_alarm_deactivate(self, device_id: int) -> bool:
        """Deactivate electric fence alarm."""
        result = await self._request(
            "POST",
            f"/api/v1/alarm/{device_id}/eletrificador/deactivate"
        )
        return result is not None and result.get("success", False)

    async def check_connection(self) -> bool:
        """Check if connection to API is working."""
        try:
            async with async_timeout.timeout(10):
                response = await self._session.get(
                    f"{self._base_url}/api/v1/health"
                )
                return response.status == 200
        except Exception:
            return False

    async def check_session(self) -> bool:
        """Check if current session is still valid."""
        if not self._session_id:
            return False
        try:
            async with async_timeout.timeout(10):
                response = await self._session.get(
                    f"{self._base_url}/api/v1/auth/session",
                    headers={"X-Session-ID": self._session_id}
                )
                return response.status == 200
        except Exception:
            return False

    def set_session_id(self, session_id: str) -> None:
        """Set the session ID (for restoring from config)."""
        self._session_id = session_id
