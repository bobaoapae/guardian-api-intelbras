"""HTTP client for Intelbras Guardian API."""
import logging
from typing import Any, Dict, List, Optional
import aiohttp
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log
)

from app.core.config import settings
from app.core.exceptions import (
    APIConnectionError,
    AuthenticationError,
    DeviceNotFoundError,
    AlarmOperationError
)

logger = logging.getLogger(__name__)


class GuardianClient:
    """
    HTTP client for Intelbras Guardian API.

    Handles all communication with the Intelbras cloud API including:
    - Authentication (delegated to AuthService)
    - Device listing
    - Partition control (arm/disarm)
    - Event retrieval
    """

    def __init__(self):
        """Initialize the Guardian client."""
        self.base_url = settings.INTELBRAS_API_URL
        self.timeout = aiohttp.ClientTimeout(total=settings.HTTP_TIMEOUT)
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self.timeout)
        return self._session

    async def close(self):
        """Close the HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    def _get_headers(self, access_token: str) -> Dict[str, str]:
        """Build request headers with authorization.

        Based on APK analysis (RetrofitUtils.java getTokenInterceptor):
        The Authorization header uses the token directly, NOT "Bearer {token}".
        """
        return {
            "Authorization": access_token,  # Token directly, not "Bearer {token}"
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "IntelbrasGuardian/1.0 Android"
        }

    def _sanitize_log_data(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Remove sensitive data from logs."""
        sanitized = data.copy()
        sensitive_keys = ["password", "access_token", "refresh_token", "token"]
        for key in sensitive_keys:
            if key in sanitized:
                sanitized[key] = "***"
        return sanitized

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=2, max=8),
        retry=retry_if_exception_type((aiohttp.ClientError, TimeoutError)),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True
    )
    async def _request(
        self,
        method: str,
        endpoint: str,
        access_token: str,
        data: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Make an HTTP request to the Guardian API with retry logic.

        Args:
            method: HTTP method (GET, POST, PUT, DELETE)
            endpoint: API endpoint (e.g., "/api/v2/alarm-centrals")
            access_token: Bearer token for authentication
            data: Request body for POST/PUT
            params: Query parameters

        Returns:
            JSON response as dictionary

        Raises:
            AuthenticationError: If 401 response
            APIConnectionError: If connection fails
        """
        url = f"{self.base_url}{endpoint}"
        headers = self._get_headers(access_token)
        session = await self._get_session()

        log_data = self._sanitize_log_data(data or {})
        logger.debug(f"Request: {method} {url} data={log_data} params={params}")

        try:
            async with session.request(
                method=method,
                url=url,
                headers=headers,
                json=data,
                params=params
            ) as response:
                response_text = await response.text()
                logger.debug(f"Response: {response.status} {response_text[:500]}")

                if response.status == 401:
                    raise AuthenticationError(
                        "Authentication failed - token may be expired",
                        {"status": 401}
                    )

                if response.status == 404:
                    raise DeviceNotFoundError(
                        f"Resource not found: {endpoint}",
                        {"status": 404}
                    )

                if response.status >= 400:
                    raise APIConnectionError(
                        f"API error: {response.status}",
                        {"status": response.status, "body": response_text[:200]}
                    )

                # Try to parse JSON, return empty dict if empty response
                if response_text.strip():
                    return await response.json()
                return {}

        except aiohttp.ClientError as e:
            logger.error(f"Connection error: {e}")
            raise APIConnectionError(f"Failed to connect to API: {e}")
        except TimeoutError as e:
            logger.error(f"Request timeout: {e}")
            raise APIConnectionError(f"Request timed out: {e}")

    async def get_alarm_centrals(self, access_token: str) -> List[Dict[str, Any]]:
        """
        Get list of alarm centrals (devices) from the API.

        Args:
            access_token: Valid access token

        Returns:
            List of alarm central dictionaries
        """
        logger.info("Fetching alarm centrals")
        response = await self._request(
            method="GET",
            endpoint="/api/v2/alarm-centrals",
            access_token=access_token
        )

        # Handle both list and paginated response formats
        if isinstance(response, list):
            return response
        elif isinstance(response, dict):
            return response.get("results", response.get("data", [response]))
        return []

    async def get_partition_status(
        self,
        access_token: str,
        central_id: int,
        partition_id: int
    ) -> Dict[str, Any]:
        """
        Get status of a specific partition.

        Args:
            access_token: Valid access token
            central_id: Alarm central ID
            partition_id: Partition ID

        Returns:
            Partition status dictionary
        """
        logger.info(f"Fetching partition status: central={central_id} partition={partition_id}")
        response = await self._request(
            method="GET",
            endpoint=f"/api/v2/alarm-centrals/{central_id}/partitions/{partition_id}",
            access_token=access_token
        )
        return response

    async def get_central_status(
        self,
        access_token: str,
        central_id: int
    ) -> Dict[str, Any]:
        """
        Get full status of an alarm central including partitions.

        Args:
            access_token: Valid access token
            central_id: Alarm central ID

        Returns:
            Central status dictionary with partitions
        """
        logger.info(f"Fetching central status: central={central_id}")
        response = await self._request(
            method="GET",
            endpoint=f"/api/v2/alarm-centrals/{central_id}/status",
            access_token=access_token
        )
        return response

    async def get_central_detail(
        self,
        access_token: str,
        central_id: int
    ) -> Dict[str, Any]:
        """
        Get detailed info of an alarm central.

        Args:
            access_token: Valid access token
            central_id: Alarm central ID

        Returns:
            Central detail dictionary
        """
        logger.info(f"Fetching central detail: central={central_id}")
        response = await self._request(
            method="GET",
            endpoint=f"/api/v2/alarm-centrals/{central_id}",
            access_token=access_token
        )
        return response

    async def arm_partition(
        self,
        access_token: str,
        central_id: int,
        partition_id: int,
        mode: str = "away"
    ) -> Dict[str, Any]:
        """
        Arm a partition.

        Args:
            access_token: Valid access token
            central_id: Alarm central ID
            partition_id: Partition ID
            mode: Arm mode - "away" (total) or "home" (stay)

        Returns:
            Operation result dictionary
        """
        logger.info(f"Arming partition: central={central_id} partition={partition_id} mode={mode}")

        # Map mode to API values (may need adjustment based on real API)
        arm_type = "total" if mode == "away" else "stay"

        try:
            response = await self._request(
                method="POST",
                endpoint=f"/api/v2/alarm-centrals/{central_id}/partitions/{partition_id}/arm",
                access_token=access_token,
                data={"type": arm_type}
            )
            return {"success": True, "response": response}
        except APIConnectionError as e:
            raise AlarmOperationError(f"Failed to arm partition: {e.message}", e.details)

    async def disarm_partition(
        self,
        access_token: str,
        central_id: int,
        partition_id: int
    ) -> Dict[str, Any]:
        """
        Disarm a partition.

        Args:
            access_token: Valid access token
            central_id: Alarm central ID
            partition_id: Partition ID

        Returns:
            Operation result dictionary
        """
        logger.info(f"Disarming partition: central={central_id} partition={partition_id}")

        try:
            response = await self._request(
                method="POST",
                endpoint=f"/api/v2/alarm-centrals/{central_id}/partitions/{partition_id}/disarm",
                access_token=access_token
            )
            return {"success": True, "response": response}
        except APIConnectionError as e:
            raise AlarmOperationError(f"Failed to disarm partition: {e.message}", e.details)

    async def get_events(
        self,
        access_token: str,
        offset: int = 0,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """
        Get alarm events (paginated).

        Args:
            access_token: Valid access token
            offset: Starting offset for pagination
            limit: Maximum number of events to return

        Returns:
            List of event dictionaries
        """
        logger.info(f"Fetching events: offset={offset} limit={limit}")
        response = await self._request(
            method="GET",
            endpoint="/api/v2/events",
            access_token=access_token,
            params={"offset": offset, "limit": limit}
        )

        # Handle both list and paginated response formats
        if isinstance(response, list):
            return response
        elif isinstance(response, dict):
            return response.get("results", response.get("data", []))
        return []

    async def get_zones(
        self,
        access_token: str,
        central_id: int
    ) -> List[Dict[str, Any]]:
        """
        Get zones (sectors) for an alarm central.

        Args:
            access_token: Valid access token
            central_id: Alarm central ID

        Returns:
            List of zone dictionaries
        """
        logger.info(f"Fetching zones for central: {central_id}")
        response = await self._request(
            method="GET",
            endpoint=f"/api/v2/alarm-centrals/{central_id}/zones",
            access_token=access_token
        )

        if isinstance(response, list):
            return response
        elif isinstance(response, dict):
            return response.get("results", response.get("data", []))
        return []

    async def activate_eletrificador(
        self,
        access_token: str,
        central_id: int
    ) -> Dict[str, Any]:
        """
        Activate (turn on) an electric fence (eletrificador).

        Args:
            access_token: Valid access token
            central_id: Electric fence device ID

        Returns:
            Operation result dictionary
        """
        logger.info(f"Activating eletrificador: central={central_id}")

        try:
            response = await self._request(
                method="POST",
                endpoint=f"/api/v2/alarm-centrals/{central_id}/activate",
                access_token=access_token,
                data={"operation": "ACTIVATE_ELETRICFIER"}
            )
            return {"success": True, "response": response}
        except APIConnectionError as e:
            raise AlarmOperationError(f"Failed to activate eletrificador: {e.message}", e.details)

    async def deactivate_eletrificador(
        self,
        access_token: str,
        central_id: int
    ) -> Dict[str, Any]:
        """
        Deactivate (turn off) an electric fence (eletrificador).

        Args:
            access_token: Valid access token
            central_id: Electric fence device ID

        Returns:
            Operation result dictionary
        """
        logger.info(f"Deactivating eletrificador: central={central_id}")

        try:
            response = await self._request(
                method="POST",
                endpoint=f"/api/v2/alarm-centrals/{central_id}/deactivate",
                access_token=access_token,
                data={"operation": "DEACTIVATE_ELETRICFIER"}
            )
            return {"success": True, "response": response}
        except APIConnectionError as e:
            raise AlarmOperationError(f"Failed to deactivate eletrificador: {e.message}", e.details)


# Global client instance
guardian_client = GuardianClient()
