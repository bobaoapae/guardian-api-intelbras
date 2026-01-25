"""Server-Sent Events (SSE) manager for real-time event streaming."""
import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

from dataclasses import dataclass, field
import json

logger = logging.getLogger(__name__)


@dataclass
class SSEClient:
    """Represents a connected SSE client."""
    session_id: str
    queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    connected_at: datetime = field(default_factory=datetime.utcnow)
    last_event_id: Optional[int] = None


class EventStreamManager:
    """
    Manages Server-Sent Events for real-time event streaming.

    Features:
    - Multiple clients can subscribe to events
    - Background polling of Intelbras API
    - Automatic detection of new events
    - Push to all connected clients
    """

    def __init__(self):
        """Initialize the event stream manager."""
        self._clients: Dict[str, SSEClient] = {}  # client_id -> SSEClient
        self._polling_task: Optional[asyncio.Task] = None
        self._poll_interval = 5  # Poll every 5 seconds
        self._last_event_ids: Dict[str, int] = {}  # session_id -> last_event_id
        self._initialized_sessions: Set[str] = set()  # Sessions that completed first poll
        self._lock = asyncio.Lock()

    async def start_polling(self):
        """Start the background polling task."""
        if self._polling_task is None:
            self._polling_task = asyncio.create_task(self._poll_loop())
            logger.info("Event stream polling started")

    async def stop_polling(self):
        """Stop the background polling task."""
        if self._polling_task:
            self._polling_task.cancel()
            try:
                await self._polling_task
            except asyncio.CancelledError:
                pass
            self._polling_task = None
            logger.info("Event stream polling stopped")

    async def add_client(self, client_id: str, session_id: str) -> SSEClient:
        """
        Add a new SSE client.

        Args:
            client_id: Unique client identifier
            session_id: Session ID for API authentication

        Returns:
            SSEClient instance
        """
        async with self._lock:
            client = SSEClient(session_id=session_id)
            self._clients[client_id] = client
            logger.info(f"SSE client connected: {client_id[:8]}...")

            # Start polling if this is the first client
            if len(self._clients) == 1:
                await self.start_polling()

            return client

    async def remove_client(self, client_id: str):
        """Remove an SSE client."""
        async with self._lock:
            if client_id in self._clients:
                del self._clients[client_id]
                logger.info(f"SSE client disconnected: {client_id[:8]}...")

            # Stop polling if no clients
            if len(self._clients) == 0:
                await self.stop_polling()

    async def broadcast_event(self, event: Dict[str, Any], event_type: str = "event"):
        """
        Broadcast an event to all connected clients.

        Args:
            event: Event data to broadcast
            event_type: SSE event type (default: "event")
        """
        async with self._lock:
            for client_id, client in self._clients.items():
                try:
                    await client.queue.put({
                        "type": event_type,
                        "data": event
                    })
                except Exception as e:
                    logger.error(f"Error broadcasting to client {client_id[:8]}: {e}")

    async def send_to_client(self, client_id: str, event: Dict[str, Any], event_type: str = "event"):
        """Send an event to a specific client."""
        async with self._lock:
            client = self._clients.get(client_id)
            if client:
                try:
                    await client.queue.put({
                        "type": event_type,
                        "data": event
                    })
                except Exception as e:
                    logger.error(f"Error sending to client {client_id[:8]}: {e}")

    async def _poll_loop(self):
        """Background loop that polls for new events."""
        from app.services.auth_service import auth_service
        from app.services.guardian_client import guardian_client

        logger.info("Event polling loop started")

        while True:
            try:
                await asyncio.sleep(self._poll_interval)

                # Get unique session IDs from connected clients
                async with self._lock:
                    session_ids = set(client.session_id for client in self._clients.values())

                if not session_ids:
                    continue

                # Poll events for each session
                for session_id in session_ids:
                    try:
                        # Get valid token
                        access_token = await auth_service.get_valid_token(session_id)

                        # Fetch recent events
                        events = await guardian_client.get_events(
                            access_token=access_token,
                            offset=0,
                            limit=10  # Only get recent events
                        )

                        if not events:
                            continue

                        # Find max event ID
                        max_id = max(event.get("id", 0) for event in events)

                        # First poll for this session - just store the max ID, don't broadcast
                        if session_id not in self._initialized_sessions:
                            self._last_event_ids[session_id] = max_id
                            self._initialized_sessions.add(session_id)
                            logger.info(f"Session {session_id[:8]}... initialized with last_event_id={max_id}")
                            continue

                        # Check for new events (only after initialization)
                        last_id = self._last_event_ids.get(session_id, 0)
                        new_events = []

                        for event in events:
                            event_id = event.get("id", 0)
                            if event_id > last_id:
                                new_events.append(event)

                        # Update last event ID
                        if max_id > last_id:
                            self._last_event_ids[session_id] = max_id

                        # Broadcast only truly new events
                        for event in reversed(new_events):  # Oldest first
                            logger.info(f"New event detected: {event.get('id')} - {event.get('event', {}).get('name', 'Unknown')}")

                            # Parse event for broadcast
                            parsed_event = self._parse_event(event)
                            await self.broadcast_event(parsed_event, event_type="alarm_event")

                    except Exception as e:
                        logger.error(f"Error polling events for session {session_id[:8]}: {e}")

            except asyncio.CancelledError:
                logger.info("Event polling loop cancelled")
                break
            except Exception as e:
                logger.error(f"Error in event polling loop: {e}")

    def _parse_event(self, raw: dict) -> dict:
        """Parse raw event into a simplified format for SSE."""
        raw_event = raw.get("event") or {}
        raw_zone = raw.get("zone") or {}
        raw_alarm_central = raw.get("alarm_central") or {}

        # Determine event severity/type
        event_name = raw_event.get("name", "").lower()
        is_alarm = any(word in event_name for word in ["disparo", "alarme", "violacao", "panico"])
        is_arm = any(word in event_name for word in ["ativacao", "arme", "armado"])
        is_disarm = any(word in event_name for word in ["desativacao", "desarme", "desarmado"])

        if is_alarm:
            severity = "critical"
        elif is_arm or is_disarm:
            severity = "info"
        else:
            severity = "normal"

        return {
            "id": raw.get("id"),
            "timestamp": raw.get("created") or raw.get("timestamp"),
            "event_name": raw_event.get("name"),
            "event_code": raw_event.get("id"),
            "zone": {
                "id": raw_zone.get("id"),
                "name": raw_zone.get("name"),
                "friendly_name": raw_zone.get("friendly_name")
            } if raw_zone else None,
            "partition_id": raw.get("alarm_partition"),
            "device_id": raw.get("alarm_central_id"),
            "device_name": raw_alarm_central.get("description"),
            "user": raw.get("alarm_user"),
            "severity": severity,
            "is_alarm": is_alarm,
            "raw": raw
        }

    def get_stats(self) -> Dict[str, Any]:
        """Get event stream statistics."""
        return {
            "connected_clients": len(self._clients),
            "is_polling": self._polling_task is not None,
            "poll_interval_seconds": self._poll_interval,
            "tracked_sessions": len(self._last_event_ids)
        }


# Global instance
event_stream = EventStreamManager()
