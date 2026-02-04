"""State management for tokens and device cache."""
import logging
import json
from datetime import datetime, timedelta
from typing import Any, Dict, Optional
from pathlib import Path
import asyncio

from app.core.config import settings

logger = logging.getLogger(__name__)

# File path for persisting sessions
SESSIONS_FILE = Path(__file__).parent.parent.parent / "data" / "sessions.json"


class InMemoryStateManager:
    """
    In-memory state manager for tokens and device state.

    Provides:
    - Token storage with TTL
    - Device state caching
    - Device password storage (per session)
    - Device connection info caching (for performance)
    - Last known alarm state (persistent, no TTL) for connection failure fallback
    - Automatic cleanup of expired entries
    """

    def __init__(self):
        """Initialize the state manager."""
        self._tokens: Dict[str, Dict[str, Any]] = {}
        self._device_state: Dict[str, Dict[str, Any]] = {}
        self._device_passwords: Dict[str, Dict[str, str]] = {}  # session_id -> {device_id: password}
        self._device_conn_info: Dict[str, Dict[str, Any]] = {}  # device_id -> connection info cache
        self._device_partitions_enabled: Dict[str, bool] = {}  # device_id -> partitions_enabled (from status)
        self._zone_friendly_names: Dict[str, Dict[int, str]] = {}  # device_id -> {zone_index: friendly_name}
        self._last_known_status: Dict[str, Dict[str, Any]] = {}  # device_id -> last successful status (persistent)
        self._state_ttl = 30  # Device state TTL in seconds
        self._conn_info_ttl = 300  # Connection info TTL in seconds (5 minutes)
        self._cleanup_task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()
        # Load persisted sessions on startup
        self._load_sessions()

    def _load_sessions(self) -> None:
        """Load sessions from file on startup."""
        try:
            if SESSIONS_FILE.exists():
                with open(SESSIONS_FILE, "r") as f:
                    data = json.load(f)
                    self._tokens = data.get("tokens", {})
                    self._device_passwords = data.get("device_passwords", {})
                    # Load zone friendly names (convert string keys back to int)
                    raw_zone_names = data.get("zone_friendly_names", {})
                    self._zone_friendly_names = {}
                    for device_id, zones in raw_zone_names.items():
                        self._zone_friendly_names[device_id] = {int(k): v for k, v in zones.items()}
                    # Load last known status (persistent cache for connection failures)
                    self._last_known_status = data.get("last_known_status", {})
                    logger.info(f"Loaded {len(self._tokens)} sessions, {len(self._device_passwords)} password sets, {len(self._zone_friendly_names)} zone configs, {len(self._last_known_status)} last known statuses from file")
        except Exception as e:
            logger.warning(f"Could not load sessions from file: {e}")
            self._tokens = {}
            self._device_passwords = {}
            self._zone_friendly_names = {}
            self._last_known_status = {}

    def _save_sessions(self) -> None:
        """Save sessions to file using atomic write (temp + rename).

        This prevents data corruption if the process crashes during write.
        """
        try:
            # Ensure data directory exists
            SESSIONS_FILE.parent.mkdir(parents=True, exist_ok=True)

            # Convert zone friendly names int keys to string for JSON
            zone_names_serializable = {}
            for device_id, zones in self._zone_friendly_names.items():
                zone_names_serializable[device_id] = {str(k): v for k, v in zones.items()}

            data = {
                "tokens": self._tokens,
                "device_passwords": self._device_passwords,
                "zone_friendly_names": zone_names_serializable,
                "last_known_status": self._last_known_status
            }

            # Atomic write: write to temp file first, then rename
            temp_file = SESSIONS_FILE.with_suffix('.tmp')
            try:
                with open(temp_file, "w") as f:
                    json.dump(data, f, indent=2)
                    f.flush()
                    # Ensure data is written to disk
                    import os
                    os.fsync(f.fileno())

                # Atomic rename (on most systems, rename is atomic)
                temp_file.replace(SESSIONS_FILE)
                logger.debug(f"Saved {len(self._tokens)} sessions to file (atomic)")
            except Exception as e:
                # Clean up temp file on failure
                if temp_file.exists():
                    try:
                        temp_file.unlink()
                    except:
                        pass
                raise e
        except Exception as e:
            logger.error(f"Could not save sessions to file: {e}")

    async def start_cleanup_task(self):
        """Start background task to cleanup expired entries."""
        if self._cleanup_task is None:
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())
            logger.info("State manager cleanup task started")

    async def stop_cleanup_task(self):
        """Stop the cleanup background task."""
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            self._cleanup_task = None
            logger.info("State manager cleanup task stopped")

    async def _cleanup_loop(self):
        """Periodically cleanup expired entries."""
        while True:
            try:
                await asyncio.sleep(60)  # Run every minute
                await self._cleanup_expired()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in cleanup loop: {e}")

    async def _cleanup_expired(self):
        """Remove expired entries from storage."""
        async with self._lock:
            now = datetime.utcnow()

            # Cleanup expired tokens
            expired_tokens = []
            for session_id, data in self._tokens.items():
                expires_at_str = data.get("expires_at")
                if expires_at_str:
                    expires_at = datetime.fromisoformat(expires_at_str)
                    if expires_at <= now:
                        expired_tokens.append(session_id)

            for session_id in expired_tokens:
                del self._tokens[session_id]
                logger.debug(f"Cleaned up expired token: {session_id[:8]}...")

            # Save if tokens were removed
            if expired_tokens:
                self._save_sessions()

            # Cleanup expired device state
            expired_states = []
            for key, data in self._device_state.items():
                cached_at_str = data.get("_cached_at")
                if cached_at_str:
                    cached_at = datetime.fromisoformat(cached_at_str)
                    if (now - cached_at).total_seconds() > self._state_ttl:
                        expired_states.append(key)

            for key in expired_states:
                del self._device_state[key]
                logger.debug(f"Cleaned up expired state: {key}")

            if expired_tokens or expired_states:
                logger.info(f"Cleanup: removed {len(expired_tokens)} tokens, {len(expired_states)} states")

    # Token management

    async def set_token(self, session_id: str, token_data: Dict[str, Any]) -> None:
        """
        Store token data for a session.

        Args:
            session_id: Unique session identifier
            token_data: Token data including access_token, refresh_token, expires_at
        """
        async with self._lock:
            self._tokens[session_id] = token_data.copy()
            self._save_sessions()  # Persist to file
            logger.debug(f"Stored token for session: {session_id[:8]}...")

    async def get_token(self, session_id: str) -> Optional[Dict[str, Any]]:
        """
        Get token data for a session.

        Args:
            session_id: Session identifier

        Returns:
            Token data dict or None if not found/expired
        """
        async with self._lock:
            token_data = self._tokens.get(session_id)
            if not token_data:
                return None

            # Check if token is expired
            expires_at_str = token_data.get("expires_at")
            if expires_at_str:
                expires_at = datetime.fromisoformat(expires_at_str)
                if expires_at <= datetime.utcnow():
                    # Token expired, but don't delete yet (refresh might work)
                    pass

            return token_data.copy()

    async def delete_token(self, session_id: str) -> None:
        """
        Delete token for a session.

        Args:
            session_id: Session identifier to delete
        """
        async with self._lock:
            if session_id in self._tokens:
                del self._tokens[session_id]
                self._save_sessions()  # Persist to file
                logger.debug(f"Deleted token for session: {session_id[:8]}...")

    # Device state management

    async def set_device_state(self, device_id: int, state_data: Dict[str, Any]) -> None:
        """
        Cache device state.

        Args:
            device_id: Device identifier
            state_data: Device state data
        """
        async with self._lock:
            state_copy = state_data.copy()
            state_copy["_cached_at"] = datetime.utcnow().isoformat()
            self._device_state[str(device_id)] = state_copy
            logger.debug(f"Cached state for device: {device_id}")

    async def get_device_state(self, device_id: int) -> Optional[Dict[str, Any]]:
        """
        Get cached device state.

        Args:
            device_id: Device identifier

        Returns:
            Device state dict or None if not found/expired
        """
        async with self._lock:
            state_data = self._device_state.get(str(device_id))
            if not state_data:
                return None

            # Check if state is expired
            cached_at_str = state_data.get("_cached_at")
            if cached_at_str:
                cached_at = datetime.fromisoformat(cached_at_str)
                if (datetime.utcnow() - cached_at).total_seconds() > self._state_ttl:
                    # State expired
                    return None

            # Return copy without internal fields
            result = state_data.copy()
            result.pop("_cached_at", None)
            return result

    async def delete_device_state(self, device_id: int) -> None:
        """
        Delete cached device state.

        Args:
            device_id: Device identifier
        """
        async with self._lock:
            key = str(device_id)
            if key in self._device_state:
                del self._device_state[key]
                logger.debug(f"Deleted state for device: {device_id}")

    async def clear_all_device_state(self) -> None:
        """Clear all cached device state."""
        async with self._lock:
            self._device_state.clear()
            logger.info("Cleared all device state cache")

    # Utility methods

    async def get_stats(self) -> Dict[str, Any]:
        """Get state manager statistics."""
        async with self._lock:
            return {
                "active_sessions": len(self._tokens),
                "cached_devices": len(self._device_state),
                "saved_passwords": sum(len(p) for p in self._device_passwords.values()),
                "backend": "memory"
            }

    # Device password management

    async def set_device_password(self, session_id: str, device_id: str, password: str) -> None:
        """
        Store device password for a session.

        Args:
            session_id: Session identifier
            device_id: Device identifier
            password: Device password (6 digits)
        """
        async with self._lock:
            if session_id not in self._device_passwords:
                self._device_passwords[session_id] = {}
            self._device_passwords[session_id][str(device_id)] = password
            self._save_sessions()
            logger.debug(f"Stored password for device {device_id} in session {session_id[:8]}...")

    async def get_device_password(self, session_id: str, device_id: str) -> Optional[str]:
        """
        Get stored device password for a session.

        Args:
            session_id: Session identifier
            device_id: Device identifier

        Returns:
            Password string or None if not found
        """
        async with self._lock:
            session_passwords = self._device_passwords.get(session_id, {})
            return session_passwords.get(str(device_id))

    async def delete_device_password(self, session_id: str, device_id: str) -> None:
        """
        Delete stored device password.

        Args:
            session_id: Session identifier
            device_id: Device identifier
        """
        async with self._lock:
            if session_id in self._device_passwords:
                if str(device_id) in self._device_passwords[session_id]:
                    del self._device_passwords[session_id][str(device_id)]
                    self._save_sessions()
                    logger.debug(f"Deleted password for device {device_id} in session {session_id[:8]}...")

    async def get_all_device_passwords(self, session_id: str) -> Dict[str, str]:
        """
        Get all stored device passwords for a session.

        Args:
            session_id: Session identifier

        Returns:
            Dict mapping device_id to password
        """
        async with self._lock:
            return self._device_passwords.get(session_id, {}).copy()

    async def cleanup_session_passwords(self, session_id: str) -> None:
        """
        Remove all passwords for a session (called on logout).

        Args:
            session_id: Session identifier
        """
        async with self._lock:
            if session_id in self._device_passwords:
                del self._device_passwords[session_id]
                self._save_sessions()
                logger.debug(f"Cleaned up passwords for session {session_id[:8]}...")

    # Device connection info caching (for performance)

    async def set_device_conn_info(self, device_id: int, conn_info: Dict[str, Any]) -> None:
        """
        Cache device connection info for fast retrieval.

        Args:
            device_id: Device identifier
            conn_info: Connection info dict with mac, use_ip_receiver, etc.
        """
        async with self._lock:
            conn_copy = conn_info.copy()
            conn_copy["_cached_at"] = datetime.utcnow().isoformat()
            self._device_conn_info[str(device_id)] = conn_copy
            logger.debug(f"Cached connection info for device: {device_id}")

    async def get_device_conn_info(self, device_id: int) -> Optional[Dict[str, Any]]:
        """
        Get cached device connection info.

        Args:
            device_id: Device identifier

        Returns:
            Connection info dict or None if not found/expired
        """
        async with self._lock:
            conn_info = self._device_conn_info.get(str(device_id))
            if not conn_info:
                return None

            # Check if cache is expired
            cached_at_str = conn_info.get("_cached_at")
            if cached_at_str:
                cached_at = datetime.fromisoformat(cached_at_str)
                if (datetime.utcnow() - cached_at).total_seconds() > self._conn_info_ttl:
                    # Cache expired
                    logger.debug(f"Connection info cache expired for device: {device_id}")
                    return None

            # Return copy without internal fields
            result = conn_info.copy()
            result.pop("_cached_at", None)
            return result

    async def delete_device_conn_info(self, device_id: int) -> None:
        """
        Delete cached device connection info.

        Args:
            device_id: Device identifier
        """
        async with self._lock:
            key = str(device_id)
            if key in self._device_conn_info:
                del self._device_conn_info[key]
                logger.debug(f"Deleted connection info cache for device: {device_id}")

    # Device partitions_enabled caching (for arm/disarm commands)

    async def set_device_partitions_enabled(self, device_id: int, partitions_enabled: bool) -> None:
        """
        Cache device partitions_enabled status.

        This value comes from ISECNet status response (byte 21) and indicates
        whether the device has partitions enabled. Used to decide whether to
        include partition byte in arm/disarm commands.

        Args:
            device_id: Device identifier
            partitions_enabled: True if device has partitions enabled
        """
        async with self._lock:
            self._device_partitions_enabled[str(device_id)] = partitions_enabled
            logger.debug(f"Cached partitions_enabled={partitions_enabled} for device: {device_id}")

    async def get_device_partitions_enabled(self, device_id: int) -> Optional[bool]:
        """
        Get cached device partitions_enabled status.

        Args:
            device_id: Device identifier

        Returns:
            True/False if cached, None if not known
        """
        async with self._lock:
            return self._device_partitions_enabled.get(str(device_id))

    async def delete_device_partitions_enabled(self, device_id: int) -> None:
        """
        Delete cached device partitions_enabled status.

        Args:
            device_id: Device identifier
        """
        async with self._lock:
            key = str(device_id)
            if key in self._device_partitions_enabled:
                del self._device_partitions_enabled[key]
                logger.debug(f"Deleted partitions_enabled cache for device: {device_id}")

    # Zone friendly name management

    async def set_zone_friendly_name(self, device_id: int, zone_index: int, friendly_name: str) -> None:
        """
        Set friendly name for a zone.

        Args:
            device_id: Device identifier
            zone_index: Zone index (0-based)
            friendly_name: User-friendly name for the zone
        """
        async with self._lock:
            key = str(device_id)
            if key not in self._zone_friendly_names:
                self._zone_friendly_names[key] = {}
            self._zone_friendly_names[key][zone_index] = friendly_name
            self._save_sessions()
            logger.debug(f"Set zone {zone_index} friendly_name='{friendly_name}' for device {device_id}")

    async def get_zone_friendly_name(self, device_id: int, zone_index: int) -> Optional[str]:
        """
        Get friendly name for a zone.

        Args:
            device_id: Device identifier
            zone_index: Zone index (0-based)

        Returns:
            Friendly name or None if not set
        """
        async with self._lock:
            key = str(device_id)
            if key in self._zone_friendly_names:
                return self._zone_friendly_names[key].get(zone_index)
            return None

    async def get_all_zone_friendly_names(self, device_id: int) -> Dict[int, str]:
        """
        Get all zone friendly names for a device.

        Args:
            device_id: Device identifier

        Returns:
            Dict mapping zone_index to friendly_name
        """
        async with self._lock:
            key = str(device_id)
            return self._zone_friendly_names.get(key, {}).copy()

    async def delete_zone_friendly_name(self, device_id: int, zone_index: int) -> None:
        """
        Delete friendly name for a zone.

        Args:
            device_id: Device identifier
            zone_index: Zone index (0-based)
        """
        async with self._lock:
            key = str(device_id)
            if key in self._zone_friendly_names and zone_index in self._zone_friendly_names[key]:
                del self._zone_friendly_names[key][zone_index]
                self._save_sessions()
                logger.debug(f"Deleted zone {zone_index} friendly_name for device {device_id}")

    # Last known status management (persistent cache for connection failures)

    async def set_last_known_status(self, device_id: int, status_data: Dict[str, Any]) -> None:
        """
        Store last known alarm status for a device.

        This is persisted to disk and used as fallback when connection to
        the alarm panel fails (e.g., AMT legacy app is blocking the connection).

        Args:
            device_id: Device identifier
            status_data: Full status data from successful ISECNet status query
        """
        async with self._lock:
            key = str(device_id)
            status_copy = status_data.copy()
            status_copy["_last_updated"] = datetime.utcnow().isoformat()
            self._last_known_status[key] = status_copy
            self._save_sessions()
            logger.debug(f"Saved last known status for device {device_id}: arm_mode={status_data.get('arm_mode')}")

    async def get_last_known_status(self, device_id: int) -> Optional[Dict[str, Any]]:
        """
        Get last known alarm status for a device.

        Used as fallback when real-time connection fails.

        Args:
            device_id: Device identifier

        Returns:
            Last known status dict with '_last_updated' timestamp, or None if not available
        """
        async with self._lock:
            key = str(device_id)
            status = self._last_known_status.get(key)
            if status:
                return status.copy()
            return None

    async def delete_last_known_status(self, device_id: int) -> None:
        """
        Delete last known status for a device.

        Args:
            device_id: Device identifier
        """
        async with self._lock:
            key = str(device_id)
            if key in self._last_known_status:
                del self._last_known_status[key]
                self._save_sessions()
                logger.debug(f"Deleted last known status for device {device_id}")


# Global state manager instance
state_manager = InMemoryStateManager()
