"""Device endpoints."""
import logging
from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel, Field
from typing import List, Optional, Any

from app.services.auth_service import auth_service

logger = logging.getLogger(__name__)
from app.services.guardian_client import guardian_client
from app.services.state_manager import state_manager
from app.core.exceptions import (
    InvalidSessionError,
    DeviceNotFoundError,
    APIConnectionError
)

router = APIRouter(prefix="/devices", tags=["Devices"])


class PartitionInfo(BaseModel):
    """Partition info model."""
    id: int
    name: Optional[str] = None
    status: Optional[str] = None
    is_in_alarm: bool = False


class ZoneInfo(BaseModel):
    """Zone info model."""
    id: int
    name: Optional[str] = None
    friendly_name: Optional[str] = None
    status: Optional[str] = None
    stay_enabled: bool = True
    bypassed: bool = False


class DeviceResponse(BaseModel):
    """Device response model."""
    id: int = Field(..., description="Device ID")
    description: str = Field(..., description="Device name/description")
    mac: Optional[str] = Field(None, description="MAC address")
    model: Optional[str] = Field(None, description="Device model")
    is_online: bool = Field(default=True, description="Online status")
    has_saved_password: bool = Field(default=False, description="Whether password is saved")
    partitions_enabled: Optional[bool] = Field(None, description="Whether partitions are enabled on device (from ISECNet status)")
    partitions: List[PartitionInfo] = Field(default_factory=list)
    zones: List[ZoneInfo] = Field(default_factory=list)


class SavePasswordRequest(BaseModel):
    """Request model for saving device password."""
    password: str = Field(..., min_length=4, max_length=6, description="Device password (4-6 digits)")


class DeviceListResponse(BaseModel):
    """Device list response model."""
    devices: List[DeviceResponse] = Field(..., description="List of devices")
    total: int = Field(..., description="Total number of devices")


def _parse_device(raw: dict, has_saved_password: bool = False, partitions_enabled: Optional[bool] = None) -> DeviceResponse:
    """Parse raw device data into response model.

    Args:
        raw: Raw device data from cloud API
        has_saved_password: Whether password is saved for this device
        partitions_enabled: Whether partitions are enabled (from ISECNet status cache).
                           If False, shows single partition instead of A/B.
    """
    # Extract partitions (handle None values)
    raw_partitions = raw.get("partitions") or []
    partitions = []

    # If partitions are explicitly disabled (from ISECNet status), show single partition
    if partitions_enabled is False and len(raw_partitions) > 0:
        # Use first partition's ID but show as single "Alarme" partition
        first_partition = raw_partitions[0]
        partitions.append(PartitionInfo(
            id=first_partition.get("id", 0),
            name="Alarme",
            status=first_partition.get("status"),
            is_in_alarm=any(p.get("is_in_alarm", False) for p in raw_partitions)
        ))
    else:
        # Show partitions as returned by cloud API
        for p in raw_partitions:
            partitions.append(PartitionInfo(
                id=p.get("id", 0),
                name=p.get("name") or p.get("friendly_name") or f"Partição {p.get('index', 0) + 1}",
                status=p.get("status"),  # May be None - need separate API call for status
                is_in_alarm=p.get("is_in_alarm", False)
            ))

    # Extract zones/sectors (handle None values)
    raw_zones = raw.get("zones") or raw.get("sectors") or []
    zones = []
    for z in raw_zones:
        zones.append(ZoneInfo(
            id=z.get("id", 0),
            name=z.get("name") or f"Zona {z.get('index', 0) + 1}",
            friendly_name=z.get("friendly_name"),
            status=z.get("status"),
            stay_enabled=z.get("stay_enabled", True),
            bypassed=z.get("bypassed", False)
        ))

    # Determine online status from connections
    connections = raw.get("connections") or {}
    is_online = connections.get("is_cloud_enabled", False) or connections.get("is_ip_receiver_server_enabled", False)

    return DeviceResponse(
        id=raw.get("id", 0),
        description=raw.get("description", raw.get("name", "Unknown")),
        mac=raw.get("central_mac") or raw.get("mac"),  # API uses central_mac
        model=raw.get("alarm_model") or raw.get("model"),  # API uses alarm_model
        is_online=is_online,
        has_saved_password=has_saved_password,
        partitions_enabled=partitions_enabled,
        partitions=partitions,
        zones=zones
    )


@router.get("", response_model=DeviceListResponse)
async def list_devices(x_session_id: str = Header(..., alias="X-Session-ID")):
    """
    List all alarm centrals (devices).

    Requires X-Session-ID header from login.
    """
    try:
        # Get valid token
        access_token = await auth_service.get_valid_token(x_session_id)

        # Fetch devices
        raw_devices = await guardian_client.get_alarm_centrals(access_token)

        # Get saved passwords for this session
        saved_passwords = await state_manager.get_all_device_passwords(x_session_id)

        # Debug: log raw response structure
        logger.info(f"Raw devices response: {len(raw_devices)} devices")
        for i, d in enumerate(raw_devices):
            logger.debug(f"Device {i}: keys={list(d.keys())}")
            logger.debug(f"Device {i} raw data: {d}")

        # Parse devices with password info and partitions_enabled status
        devices = []
        for d in raw_devices:
            device_id_str = str(d.get("id", 0))
            device_id_int = d.get("id", 0)
            has_password = device_id_str in saved_passwords
            # Get cached partitions_enabled from ISECNet status (set during auto-sync)
            partitions_enabled = await state_manager.get_device_partitions_enabled(device_id_int)
            devices.append(_parse_device(d, has_saved_password=has_password, partitions_enabled=partitions_enabled))

        # Cache device states
        for device in devices:
            await state_manager.set_device_state(device.id, device.model_dump())

        return DeviceListResponse(devices=devices, total=len(devices))

    except InvalidSessionError as e:
        raise HTTPException(status_code=401, detail=str(e.message))
    except APIConnectionError as e:
        raise HTTPException(status_code=503, detail=str(e.message))


@router.get("/{device_id}", response_model=DeviceResponse)
async def get_device(
    device_id: int,
    x_session_id: str = Header(..., alias="X-Session-ID")
):
    """
    Get a specific device by ID.

    Requires X-Session-ID header from login.
    """
    try:
        # Check cache first
        cached = await state_manager.get_device_state(device_id)
        if cached:
            return DeviceResponse(**cached)

        # Get valid token
        access_token = await auth_service.get_valid_token(x_session_id)

        # Fetch all devices and find the one we need
        raw_devices = await guardian_client.get_alarm_centrals(access_token)

        for raw in raw_devices:
            if raw.get("id") == device_id:
                # Get cached partitions_enabled from ISECNet status
                partitions_enabled = await state_manager.get_device_partitions_enabled(device_id)
                device = _parse_device(raw, partitions_enabled=partitions_enabled)
                await state_manager.set_device_state(device_id, device.model_dump())
                return device

        raise DeviceNotFoundError(f"Device {device_id} not found")

    except InvalidSessionError as e:
        raise HTTPException(status_code=401, detail=str(e.message))
    except DeviceNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e.message))
    except APIConnectionError as e:
        raise HTTPException(status_code=503, detail=str(e.message))


@router.get("/{device_id}/partitions/status")
async def get_partitions_status(
    device_id: int,
    x_session_id: str = Header(..., alias="X-Session-ID")
):
    """
    Get status for all partitions of a device.

    This makes individual API calls to get real-time status for each partition.

    Requires X-Session-ID header from login.
    """
    try:
        # Get valid token
        access_token = await auth_service.get_valid_token(x_session_id)

        # First get device to know partitions
        raw_devices = await guardian_client.get_alarm_centrals(access_token)
        device = None
        for d in raw_devices:
            if d.get("id") == device_id:
                device = d
                break

        if not device:
            raise DeviceNotFoundError(f"Device {device_id} not found")

        partitions = device.get("partitions") or []
        statuses = []

        # Fetch status for each partition
        for partition in partitions:
            partition_id = partition.get("id")
            if partition_id:
                try:
                    status_data = await guardian_client.get_partition_status(
                        access_token=access_token,
                        central_id=device_id,
                        partition_id=partition_id
                    )
                    statuses.append({
                        "partition_id": partition_id,
                        "name": partition.get("name") or partition.get("friendly_name") or f"Particao {partition_id}",
                        "status": status_data.get("status") or status_data.get("state"),
                        "is_in_alarm": status_data.get("is_in_alarm", False),
                        "raw": status_data
                    })
                except Exception as e:
                    logger.warning(f"Failed to get status for partition {partition_id}: {e}")
                    statuses.append({
                        "partition_id": partition_id,
                        "name": partition.get("name") or f"Particao {partition_id}",
                        "status": None,
                        "error": str(e)
                    })

        return {
            "device_id": device_id,
            "partitions": statuses
        }

    except InvalidSessionError as e:
        raise HTTPException(status_code=401, detail=str(e.message))
    except DeviceNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e.message))
    except APIConnectionError as e:
        raise HTTPException(status_code=503, detail=str(e.message))


@router.post("/{device_id}/password")
async def save_device_password(
    device_id: int,
    request: SavePasswordRequest,
    x_session_id: str = Header(..., alias="X-Session-ID")
):
    """
    Save password for a device.

    This allows automatic status updates without prompting for password.
    """
    try:
        # Validate session exists
        await auth_service.get_valid_token(x_session_id)

        # Save password
        await state_manager.set_device_password(
            session_id=x_session_id,
            device_id=str(device_id),
            password=request.password
        )

        logger.info(f"Password saved for device {device_id}")

        return {"success": True, "message": "Password saved"}

    except InvalidSessionError as e:
        raise HTTPException(status_code=401, detail=str(e.message))


@router.delete("/{device_id}/password")
async def delete_device_password(
    device_id: int,
    x_session_id: str = Header(..., alias="X-Session-ID")
):
    """
    Delete saved password for a device.
    """
    try:
        # Validate session exists
        await auth_service.get_valid_token(x_session_id)

        # Delete password
        await state_manager.delete_device_password(
            session_id=x_session_id,
            device_id=str(device_id)
        )

        logger.info(f"Password deleted for device {device_id}")

        return {"success": True, "message": "Password deleted"}

    except InvalidSessionError as e:
        raise HTTPException(status_code=401, detail=str(e.message))


@router.get("/{device_id}/password/check")
async def check_device_password(
    device_id: int,
    x_session_id: str = Header(..., alias="X-Session-ID")
):
    """
    Check if password is saved for a device (does not return the password).
    """
    try:
        # Validate session exists
        await auth_service.get_valid_token(x_session_id)

        # Check if password exists
        password = await state_manager.get_device_password(
            session_id=x_session_id,
            device_id=str(device_id)
        )

        return {
            "device_id": device_id,
            "has_saved_password": password is not None
        }

    except InvalidSessionError as e:
        raise HTTPException(status_code=401, detail=str(e.message))
