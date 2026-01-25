"""Zone management endpoints."""
import logging
from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel, Field
from typing import Optional, List, Dict

from app.services.auth_service import auth_service
from app.services.guardian_client import guardian_client
from app.services.isecnet_client import isecnet_client
from app.services.state_manager import state_manager
from app.core.exceptions import (
    InvalidSessionError,
    DeviceNotFoundError,
    APIConnectionError
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/zones", tags=["Zones"])


class ZoneInfo(BaseModel):
    """Zone information."""
    index: int = Field(..., description="Zone index (0-based)")
    name: str = Field(..., description="Zone name (e.g., 'Zona 01')")
    friendly_name: Optional[str] = Field(None, description="User-defined friendly name")
    is_open: bool = Field(default=False, description="Whether zone is currently open/triggered")
    is_bypassed: bool = Field(default=False, description="Whether zone is bypassed")


class ZonesResponse(BaseModel):
    """Zones list response."""
    device_id: int = Field(..., description="Device ID")
    total_zones: int = Field(..., description="Total number of zones supported")
    zones: List[ZoneInfo] = Field(default_factory=list, description="List of zones")


class UpdateFriendlyNameRequest(BaseModel):
    """Update zone friendly name request."""
    friendly_name: str = Field(..., max_length=50, description="New friendly name for the zone")


class UpdateFriendlyNameResponse(BaseModel):
    """Update zone friendly name response."""
    success: bool = Field(..., description="Whether update succeeded")
    device_id: int = Field(..., description="Device ID")
    zone_index: int = Field(..., description="Zone index")
    friendly_name: str = Field(..., description="New friendly name")


class OpenZonesResponse(BaseModel):
    """Open zones response (used when arm fails)."""
    device_id: int = Field(..., description="Device ID")
    open_zones: List[ZoneInfo] = Field(default_factory=list, description="List of open zones")
    message: str = Field(..., description="Error message")


def _get_zone_count_for_model(model: str) -> int:
    """Get zone count based on alarm model."""
    # Based on APK AlarmModel.getZoneMaxCount()
    zone_counts = {
        "AMT_8000": 64,
        "AMT_8000_PRO": 64,
        "AMT_8000_LITE": 64,
        "AMT_4010": 64,
        "AMT_2018_E_SMART": 48,
        "AMT_2018_E3G": 48,
        "AMT_2018_E_EG": 48,
        "AMT_2118_EG": 48,
        "AMT_1016_NET": 16,
        "AMT_1000_SMART": 10,
        "ANM_24_NET": 24,
        "ANM_24_NET_G2": 24,
        "ELC_6012_NET": 8,
        "ELC_6012_IND": 8,
    }
    return zone_counts.get(model, 48)  # Default 48 zones


@router.get("/{device_id}", response_model=ZonesResponse)
async def get_zones(
    device_id: int,
    x_session_id: str = Header(..., alias="X-Session-ID")
):
    """
    Get all zones for a device with their status and friendly names.

    Returns zone information including:
    - Zone index and default name
    - User-defined friendly name (if set)
    - Current open/triggered status
    - Bypass status

    Requires X-Session-ID header from login and a saved password for the device.
    """
    try:
        # Get valid token
        access_token = await auth_service.get_valid_token(x_session_id)

        # Get saved password
        password = await state_manager.get_device_password(x_session_id, str(device_id))
        if not password:
            raise HTTPException(status_code=400, detail="No saved password for this device. Save a password first.")

        # Get device info from cloud API to determine model
        raw_devices = await guardian_client.get_alarm_centrals(access_token)
        device_info = None
        for device in raw_devices:
            if device.get("id") == device_id:
                device_info = device
                break

        if not device_info:
            raise DeviceNotFoundError(f"Device {device_id} not found")

        model = device_info.get("alarm_model", "UNKNOWN")
        total_zones = _get_zone_count_for_model(model)

        # Get connection info
        mac = device_info.get("central_mac") or device_info.get("mac")
        connections = device_info.get("connections") or {}
        is_ip_receiver = connections.get("is_ip_receiver_server_enabled", False)

        # Get zone friendly names from storage
        friendly_names = await state_manager.get_all_zone_friendly_names(device_id)

        # Try to get real-time zone status from device
        zones_status = {}
        try:
            if is_ip_receiver:
                success, status, message = await isecnet_client.get_status(
                    device_id=device_id,
                    mac=mac,
                    password=password,
                    use_ip_receiver=True,
                    ip_receiver_addr=device_info.get("ip_receiver_server_addr"),
                    ip_receiver_port=int(device_info.get("ip_receiver_server_port", 9009)),
                    ip_receiver_account=device_info.get("ip_receiver_server_account")
                )
                if success and status.zones:
                    for zone in status.zones:
                        zones_status[zone["index"]] = zone

                # Disconnect after getting status
                await isecnet_client.disconnect(device_id)
        except Exception as e:
            logger.warning(f"Could not get real-time zone status: {e}")

        # Build zones list
        zones = []
        for i in range(total_zones):
            zone_status = zones_status.get(i, {})
            zones.append(ZoneInfo(
                index=i,
                name=f"Zona {i + 1:02d}",
                friendly_name=friendly_names.get(i),
                is_open=zone_status.get("open", False),
                is_bypassed=zone_status.get("bypassed", False)
            ))

        return ZonesResponse(
            device_id=device_id,
            total_zones=total_zones,
            zones=zones
        )

    except InvalidSessionError as e:
        raise HTTPException(status_code=401, detail=str(e.message))
    except DeviceNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e.message))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error getting zones: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/{device_id}/{zone_index}/friendly_name", response_model=UpdateFriendlyNameResponse)
async def update_zone_friendly_name(
    device_id: int,
    zone_index: int,
    request: UpdateFriendlyNameRequest,
    x_session_id: str = Header(..., alias="X-Session-ID")
):
    """
    Update the friendly name for a zone.

    The friendly name is stored locally and will be used in:
    - Zone listings
    - Open zones error messages
    - Notifications

    Requires X-Session-ID header from login.
    """
    try:
        # Validate session
        await auth_service.get_valid_token(x_session_id)

        # Save friendly name
        await state_manager.set_zone_friendly_name(device_id, zone_index, request.friendly_name)

        return UpdateFriendlyNameResponse(
            success=True,
            device_id=device_id,
            zone_index=zone_index,
            friendly_name=request.friendly_name
        )

    except InvalidSessionError as e:
        raise HTTPException(status_code=401, detail=str(e.message))
    except Exception as e:
        logger.error(f"Unexpected error updating zone friendly name: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{device_id}/{zone_index}/friendly_name")
async def delete_zone_friendly_name(
    device_id: int,
    zone_index: int,
    x_session_id: str = Header(..., alias="X-Session-ID")
):
    """
    Delete the friendly name for a zone.

    Requires X-Session-ID header from login.
    """
    try:
        # Validate session
        await auth_service.get_valid_token(x_session_id)

        # Delete friendly name
        await state_manager.delete_zone_friendly_name(device_id, zone_index)

        return {"success": True, "message": f"Friendly name removed for zone {zone_index}"}

    except InvalidSessionError as e:
        raise HTTPException(status_code=401, detail=str(e.message))
    except Exception as e:
        logger.error(f"Unexpected error deleting zone friendly name: {e}")
        raise HTTPException(status_code=500, detail=str(e))


async def get_open_zones_for_device(device_id: int, session_id: str) -> List[ZoneInfo]:
    """
    Get list of open zones for a device.

    This is used when arm fails with "Open zones" error to show which zones are open.

    Args:
        device_id: Device ID
        session_id: Session ID for authentication

    Returns:
        List of open zones with their friendly names
    """
    try:
        # Get valid token
        access_token = await auth_service.get_valid_token(session_id)

        # Get saved password
        password = await state_manager.get_device_password(session_id, str(device_id))
        if not password:
            return []

        # Get device info
        raw_devices = await guardian_client.get_alarm_centrals(access_token)
        device_info = None
        for device in raw_devices:
            if device.get("id") == device_id:
                device_info = device
                break

        if not device_info:
            return []

        mac = device_info.get("central_mac") or device_info.get("mac")
        connections = device_info.get("connections") or {}
        is_ip_receiver = connections.get("is_ip_receiver_server_enabled", False)

        # Get zone friendly names
        friendly_names = await state_manager.get_all_zone_friendly_names(device_id)

        # Get status to find open zones
        if is_ip_receiver:
            success, status, message = await isecnet_client.get_status(
                device_id=device_id,
                mac=mac,
                password=password,
                use_ip_receiver=True,
                ip_receiver_addr=device_info.get("ip_receiver_server_addr"),
                ip_receiver_port=int(device_info.get("ip_receiver_server_port", 9009)),
                ip_receiver_account=device_info.get("ip_receiver_server_account")
            )

            # Disconnect after getting status
            await isecnet_client.disconnect(device_id)

            if success and status.zones:
                open_zones = []
                for zone in status.zones:
                    if zone.get("open", False):
                        open_zones.append(ZoneInfo(
                            index=zone["index"],
                            name=f"Zona {zone['index'] + 1:02d}",
                            friendly_name=friendly_names.get(zone["index"]),
                            is_open=True,
                            is_bypassed=zone.get("bypassed", False)
                        ))
                return open_zones

        return []

    except Exception as e:
        logger.error(f"Error getting open zones: {e}")
        return []
