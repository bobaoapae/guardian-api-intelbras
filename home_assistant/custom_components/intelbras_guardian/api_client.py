"""API client for FastAPI middleware."""
import asyncio
import json
import logging
from typing import Any, AsyncGenerator, Callable, Dict, List, Optional
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

    @property
    def base_url(self) -> str:
        """Get base URL."""
        return self._base_url

    async def start_oauth(self) -> Optional[Dict[str, Any]]:
        """Start OAuth flow and get authorization URL.

        Returns dict with:
        - auth_url: URL to open in browser
        - state: State parameter for callback
        - redirect_uri: Redirect URI used
        - instructions: Instructions for user
        """
        try:
            async with async_timeout.timeout(self._timeout):
                response = await self._session.post(
                    f"{self._base_url}/api/v1/auth/start"
                )

                if response.status == 200:
                    data = await response.json()
                    _LOGGER.info("OAuth flow started successfully")
                    return data
                else:
                    error = await response.text()
                    _LOGGER.error(f"Failed to start OAuth: {error}")
                    return None

        except aiohttp.ClientError as e:
            _LOGGER.error(f"Connection error starting OAuth: {e}")
            return None
        except Exception as e:
            _LOGGER.error(f"Unexpected error starting OAuth: {e}")
            return None

    async def complete_oauth(self, callback_url: str) -> bool:
        """Complete OAuth flow with callback URL.

        Args:
            callback_url: The full callback URL with code and state parameters

        Returns:
            True if authentication successful
        """
        try:
            async with async_timeout.timeout(self._timeout):
                response = await self._session.post(
                    f"{self._base_url}/api/v1/auth/callback-url",
                    json={"callback_url": callback_url}
                )

                if response.status == 200:
                    data = await response.json()
                    self._session_id = data.get("session_id")
                    _LOGGER.info("OAuth authentication successful")
                    return True
                else:
                    error = await response.text()
                    _LOGGER.error(f"OAuth callback failed: {error}")
                    return False

        except aiohttp.ClientError as e:
            _LOGGER.error(f"Connection error during OAuth callback: {e}")
            return False
        except Exception as e:
            _LOGGER.error(f"Unexpected error during OAuth callback: {e}")
            return False

    async def authenticate(self, username: str, password: str) -> bool:
        """Authenticate with the API (legacy - password grant).

        Note: This method may not work if the API only supports OAuth.
        Use start_oauth() and complete_oauth() instead.
        """
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
    ) -> Dict[str, Any]:
        """Arm a partition.

        Returns:
            Dict with 'success' (bool) and optionally 'error' (str) or 'open_zones' (list)
        """
        result = await self._request_with_error(
            "POST",
            f"/api/v1/alarm/{device_id}/arm",
            {"partition_id": partition_id, "mode": mode}
        )
        return result

    async def disarm_partition(
        self,
        device_id: int,
        partition_id: int
    ) -> Dict[str, Any]:
        """Disarm a partition.

        Returns:
            Dict with 'success' (bool) and optionally 'error' (str)
        """
        result = await self._request_with_error(
            "POST",
            f"/api/v1/alarm/{device_id}/disarm",
            {"partition_id": partition_id}
        )
        return result

    async def _request_with_error(
        self,
        method: str,
        endpoint: str,
        data: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Make an API request and return result with error details."""
        if not self._session_id:
            _LOGGER.error("Not authenticated")
            return {"success": False, "error": "Não autenticado"}

        headers = {"X-Session-ID": self._session_id}

        try:
            async with async_timeout.timeout(self._timeout):
                if method == "POST":
                    response = await self._session.post(
                        f"{self._base_url}{endpoint}",
                        headers=headers,
                        json=data or {}
                    )
                else:
                    return {"success": False, "error": "Método não suportado"}

                response_data = await response.json()

                if response.status == 200:
                    return response_data
                else:
                    # Handle error response - detail can be string or dict
                    detail = response_data.get("detail", {})
                    if isinstance(detail, dict):
                        error_msg = detail.get("message", detail.get("error", "Erro desconhecido"))
                        open_zones = detail.get("open_zones", [])
                    else:
                        error_msg = str(detail) if detail else "Erro desconhecido"
                        open_zones = []

                    _LOGGER.error(f"API error {response.status}: {error_msg}")
                    return {
                        "success": False,
                        "error": error_msg,
                        "open_zones": open_zones
                    }

        except aiohttp.ClientError as e:
            _LOGGER.error(f"Connection error: {e}")
            return {"success": False, "error": f"Erro de conexão: {e}"}
        except Exception as e:
            _LOGGER.error(f"Request error: {e}")
            return {"success": False, "error": f"Erro: {e}"}

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

    async def listen_sse_events(
        self,
        on_event: Callable[[Dict[str, Any]], None],
        stop_event: asyncio.Event,
    ) -> None:
        """Listen to Server-Sent Events (SSE) stream for real-time updates.

        Args:
            on_event: Callback function called for each event received
            stop_event: Event to signal when to stop listening

        The SSE endpoint sends events in the format:
            data: {"event_type": "alarm_event", "data": {...}}
        """
        if not self._session_id:
            _LOGGER.warning("Cannot connect to SSE: not authenticated")
            return

        url = f"{self._base_url}/api/v1/events/stream"
        headers = {"X-Session-ID": self._session_id}

        reconnect_delay = 1
        max_reconnect_delay = 60

        while not stop_event.is_set():
            try:
                _LOGGER.debug("Connecting to SSE stream at %s", url)

                async with self._session.get(
                    url,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=None, sock_read=90),
                ) as response:
                    if response.status == 401:
                        _LOGGER.warning("SSE connection unauthorized - session expired")
                        break

                    if response.status != 200:
                        _LOGGER.error("SSE connection failed with status %d", response.status)
                        await asyncio.sleep(reconnect_delay)
                        reconnect_delay = min(reconnect_delay * 2, max_reconnect_delay)
                        continue

                    _LOGGER.info("SSE stream connected successfully")
                    reconnect_delay = 1  # Reset on successful connection

                    # Read SSE stream
                    async for line in response.content:
                        if stop_event.is_set():
                            break

                        line = line.decode("utf-8").strip()

                        # Skip empty lines and comments
                        if not line or line.startswith(":"):
                            continue

                        # Parse SSE data line
                        if line.startswith("data:"):
                            data_str = line[5:].strip()
                            if data_str:
                                try:
                                    event_data = json.loads(data_str)
                                    event_type = event_data.get("event_type", "")

                                    # Only process alarm events
                                    if event_type == "alarm_event":
                                        _LOGGER.debug("SSE alarm event received: %s", event_data)
                                        on_event(event_data.get("data", {}))

                                except json.JSONDecodeError as e:
                                    _LOGGER.debug("SSE non-JSON data: %s", data_str[:100])

            except asyncio.CancelledError:
                _LOGGER.debug("SSE listener cancelled")
                break

            except aiohttp.ClientError as e:
                _LOGGER.warning("SSE connection error: %s", e)
                if not stop_event.is_set():
                    await asyncio.sleep(reconnect_delay)
                    reconnect_delay = min(reconnect_delay * 2, max_reconnect_delay)

            except Exception as e:
                _LOGGER.error("SSE unexpected error: %s", e)
                if not stop_event.is_set():
                    await asyncio.sleep(reconnect_delay)
                    reconnect_delay = min(reconnect_delay * 2, max_reconnect_delay)

        _LOGGER.info("SSE listener stopped")
