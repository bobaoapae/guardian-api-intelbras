"""Event endpoints."""
import asyncio
import uuid
from fastapi import APIRouter, HTTPException, Header, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from typing import List, Optional, Any, AsyncGenerator
from datetime import datetime
import json

from app.services.auth_service import auth_service
from app.services.guardian_client import guardian_client
from app.services.event_stream import event_stream
from app.core.exceptions import (
    InvalidSessionError,
    APIConnectionError
)

router = APIRouter(prefix="/events", tags=["Events"])


class ZoneInfo(BaseModel):
    """Zone info in event."""
    id: Optional[int] = None
    name: Optional[str] = None
    friendly_name: Optional[str] = None


class NotificationInfo(BaseModel):
    """Notification info in event."""
    code: Optional[int] = None
    title: Optional[str] = None
    message: Optional[str] = None


class EventResponse(BaseModel):
    """Event response model."""
    id: int = Field(..., description="Event ID")
    timestamp: Optional[str] = Field(None, description="Event timestamp")
    event_type: Optional[str] = Field(None, description="Event type")
    zone: Optional[ZoneInfo] = Field(None, description="Zone info")
    partition_id: Optional[int] = Field(None, description="Partition ID")
    notification: Optional[NotificationInfo] = Field(None, description="Notification details")
    device_id: Optional[int] = Field(None, description="Device ID")
    is_read: bool = Field(default=False, description="Whether event was read")
    raw_data: Optional[dict] = Field(None, description="Raw event data for debugging")


class EventListResponse(BaseModel):
    """Event list response model."""
    events: List[EventResponse] = Field(..., description="List of events")
    total: int = Field(..., description="Total events returned")
    offset: int = Field(..., description="Current offset")
    limit: int = Field(..., description="Current limit")


def _parse_event(raw: dict) -> EventResponse:
    """Parse raw event data into response model."""
    # Parse zone info
    raw_zone = raw.get("zone") or {}
    zone = None
    if raw_zone:
        zone = ZoneInfo(
            id=raw_zone.get("id"),
            name=raw_zone.get("name"),
            friendly_name=raw_zone.get("friendly_name")
        )

    # Parse notification from event info (real API structure)
    # Real API has: event: {name: "...", id: 20, event_id: "1407"}
    raw_event = raw.get("event") or {}
    raw_notification = raw.get("notification") or {}
    raw_alarm_central = raw.get("alarm_central") or {}

    # Build notification from available data
    event_name = raw_event.get("name") or raw_notification.get("title")
    zone_name = raw_zone.get("name") if raw_zone else None
    alarm_user = raw.get("alarm_user")
    central_desc = raw_alarm_central.get("description")

    # Build message from zone and user info
    message_parts = []
    if zone_name:
        message_parts.append(zone_name)
    if alarm_user:
        message_parts.append(f"por {alarm_user}")
    if central_desc:
        message_parts.append(f"em {central_desc}")
    message = " - ".join(message_parts) if message_parts else None

    notification = NotificationInfo(
        code=raw_event.get("id") or raw_notification.get("code"),
        title=event_name,
        message=message or raw_notification.get("message")
    )

    # Parse timestamp - API uses "created" field
    timestamp = raw.get("created") or raw.get("timestamp")
    if timestamp and isinstance(timestamp, datetime):
        timestamp = timestamp.isoformat()

    return EventResponse(
        id=raw.get("id", 0),
        timestamp=timestamp,
        event_type=event_name or raw.get("event_type") or raw.get("type"),
        zone=zone,
        partition_id=raw.get("alarm_partition", raw.get("partition_id")),
        notification=notification,
        device_id=raw.get("alarm_central_id") or raw.get("device_id") or raw.get("central_id"),
        is_read=raw.get("is_read", False),
        raw_data=raw  # Include raw data for debugging API responses
    )


@router.get("", response_model=EventListResponse)
async def list_events(
    x_session_id: str = Header(..., alias="X-Session-ID"),
    offset: int = Query(default=0, ge=0, description="Starting offset"),
    limit: int = Query(default=50, ge=1, le=100, description="Maximum events to return"),
    since: Optional[str] = Query(default=None, description="ISO datetime to filter events after")
):
    """
    Get alarm events (paginated).

    Returns recent alarm events including triggers, arm/disarm actions, etc.

    Requires X-Session-ID header from login.
    """
    try:
        # Get valid token
        access_token = await auth_service.get_valid_token(x_session_id)

        # Fetch events
        raw_events = await guardian_client.get_events(
            access_token=access_token,
            offset=offset,
            limit=limit
        )

        # Filter by since if provided
        if since:
            try:
                since_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
                filtered_events = []
                for event in raw_events:
                    # API uses "created" field for timestamp
                    event_ts = event.get("created") or event.get("timestamp")
                    if event_ts:
                        if isinstance(event_ts, str):
                            event_dt = datetime.fromisoformat(event_ts.replace("Z", "+00:00"))
                        else:
                            event_dt = event_ts
                        if event_dt >= since_dt:
                            filtered_events.append(event)
                raw_events = filtered_events
            except ValueError:
                pass  # Invalid date format, ignore filter

        # Parse events
        events = [_parse_event(e) for e in raw_events]

        return EventListResponse(
            events=events,
            total=len(events),
            offset=offset,
            limit=limit
        )

    except InvalidSessionError as e:
        raise HTTPException(status_code=401, detail=str(e.message))
    except APIConnectionError as e:
        raise HTTPException(status_code=503, detail=str(e.message))


@router.get("/recent")
async def get_recent_events(
    x_session_id: str = Header(..., alias="X-Session-ID"),
    count: int = Query(default=10, ge=1, le=50, description="Number of recent events")
):
    """
    Get most recent alarm events.

    Convenience endpoint for getting the latest events.

    Requires X-Session-ID header from login.
    """
    try:
        # Get valid token
        access_token = await auth_service.get_valid_token(x_session_id)

        # Fetch events
        raw_events = await guardian_client.get_events(
            access_token=access_token,
            offset=0,
            limit=count
        )

        # Parse events
        events = [_parse_event(e) for e in raw_events]

        return {
            "events": events,
            "count": len(events)
        }

    except InvalidSessionError as e:
        raise HTTPException(status_code=401, detail=str(e.message))
    except APIConnectionError as e:
        raise HTTPException(status_code=503, detail=str(e.message))


async def event_generator(client_id: str, session_id: str) -> AsyncGenerator[str, None]:
    """
    Generate SSE events for a client.

    Yields SSE-formatted events as they occur.
    """
    client = await event_stream.add_client(client_id, session_id)

    try:
        # Send initial connection message
        yield f"event: connected\ndata: {json.dumps({'client_id': client_id, 'message': 'Conectado ao stream de eventos'})}\n\n"

        while True:
            try:
                # Wait for events with timeout (for keepalive)
                event = await asyncio.wait_for(client.queue.get(), timeout=30.0)

                event_type = event.get("type", "event")
                event_data = json.dumps(event.get("data", {}), default=str)

                yield f"event: {event_type}\ndata: {event_data}\n\n"

            except asyncio.TimeoutError:
                # Send keepalive ping
                yield f"event: ping\ndata: {json.dumps({'timestamp': datetime.utcnow().isoformat()})}\n\n"

    except asyncio.CancelledError:
        pass
    finally:
        await event_stream.remove_client(client_id)


@router.get("/stream")
async def stream_events(
    request: Request,
    x_session_id: str = Header(None, alias="X-Session-ID"),
    session_id: str = Query(None, description="Session ID (alternative to header)")
):
    """
    Stream events in real-time using Server-Sent Events (SSE).

    This endpoint maintains an open connection and pushes new events
    as they are detected from the Intelbras API.

    Events are polled every 5 seconds from Intelbras and pushed immediately
    to connected clients.

    Event types:
    - connected: Initial connection confirmation
    - alarm_event: New alarm event detected
    - ping: Keepalive (every 30s)

    Requires X-Session-ID header or session_id query parameter.

    Example usage with JavaScript EventSource:
    ```javascript
    const eventSource = new EventSource('/api/v1/events/stream?session_id=xxx');

    eventSource.addEventListener('alarm_event', (e) => {
        const event = JSON.parse(e.data);
        console.log('New alarm event:', event);
    });
    ```
    """
    # Accept session from header or query param (EventSource doesn't support headers)
    effective_session_id = x_session_id or session_id

    if not effective_session_id:
        raise HTTPException(
            status_code=401,
            detail="Session ID required (X-Session-ID header or session_id query param)"
        )

    try:
        # Validate session
        await auth_service.get_valid_token(effective_session_id)

        # Generate unique client ID
        client_id = str(uuid.uuid4())

        return StreamingResponse(
            event_generator(client_id, effective_session_id),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",  # Disable nginx buffering
            }
        )

    except InvalidSessionError as e:
        raise HTTPException(status_code=401, detail=str(e.message))


@router.get("/stream/stats")
async def get_stream_stats(
    x_session_id: str = Header(..., alias="X-Session-ID")
):
    """
    Get event stream statistics.

    Returns information about connected clients and polling status.

    Requires X-Session-ID header from login.
    """
    try:
        await auth_service.get_valid_token(x_session_id)
        return event_stream.get_stats()
    except InvalidSessionError as e:
        raise HTTPException(status_code=401, detail=str(e.message))
