"""Alarm control endpoints using ISECNet Protocol."""
import asyncio
import logging
from dataclasses import dataclass
from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel, Field
from typing import Optional, List
from enum import Enum

from app.services.auth_service import auth_service
from app.services.guardian_client import guardian_client
from app.services.isecnet_client import isecnet_client
from app.services.state_manager import state_manager
from app.services.event_stream import event_stream
from app.core.exceptions import (
    InvalidSessionError,
    AlarmOperationError,
    DeviceNotFoundError,
    APIConnectionError
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/alarm", tags=["Alarm Control"])


class ArmMode(str, Enum):
    """Arm mode options."""
    AWAY = "away"  # Total arm
    HOME = "home"  # Stay arm (partial)


class ArmRequest(BaseModel):
    """Arm request model."""
    partition_id: Optional[int] = Field(None, description="Partition ID to arm (None = all partitions)")
    mode: ArmMode = Field(default=ArmMode.AWAY, description="Arm mode: away (total) or home (stay)")
    password: Optional[str] = Field(None, min_length=4, max_length=6, description="Alarm panel password (4-6 digits). If not provided, uses saved password.")
    local_ip: Optional[str] = Field(None, description="Local IP address of the alarm panel (for direct connection)")
    local_port: Optional[int] = Field(None, description="Local port of the alarm panel (default: 9009)")
    save_password: bool = Field(default=False, description="Save password for future use")


class DisarmRequest(BaseModel):
    """Disarm request model."""
    partition_id: Optional[int] = Field(None, description="Partition ID to disarm (None = all partitions)")
    password: Optional[str] = Field(None, min_length=4, max_length=6, description="Alarm panel password (4-6 digits). If not provided, uses saved password.")
    save_password: bool = Field(default=False, description="Save password for future use")


class BypassZoneRequest(BaseModel):
    """Bypass zone request model."""
    zone_indices: List[int] = Field(..., description="Zone indices (0-based) to bypass/unbypass")
    bypass: bool = Field(default=True, description="True to bypass (anular), False to unbypass")
    password: Optional[str] = Field(None, min_length=4, max_length=6, description="Alarm panel password (4-6 digits). If not provided, uses saved password.")
    save_password: bool = Field(default=False, description="Save password for future use")


class OpenZoneInfo(BaseModel):
    """Open zone information for error responses."""
    index: int = Field(..., description="Zone index (0-based)")
    name: str = Field(..., description="Zone name")
    friendly_name: Optional[str] = Field(None, description="User-defined friendly name")


class AlarmOperationResponse(BaseModel):
    """Alarm operation response model."""
    success: bool = Field(..., description="Whether operation succeeded")
    device_id: int = Field(..., description="Device ID")
    partition_id: Optional[int] = Field(None, description="Partition ID (None if all)")
    new_status: Optional[str] = Field(None, description="New partition status")
    message: str = Field(..., description="Operation message")
    open_zones: Optional[List[OpenZoneInfo]] = Field(None, description="List of open zones (if arm failed due to open zones)")


class PartitionStatusInfo(BaseModel):
    """Partition status information."""
    index: int = Field(..., description="Partition index (0-based)")
    state: str = Field(..., description="Partition state: disarmed, armed_away, armed_stay, triggered")


class ZoneStatusInfo(BaseModel):
    """Zone status information."""
    index: int = Field(..., description="Zone index (0-based)")
    name: str = Field(..., description="Zone name (e.g., 'Zona 01')")
    is_open: bool = Field(default=False, description="Whether zone is open/triggered")
    is_bypassed: bool = Field(default=False, description="Whether zone is bypassed")
    is_wireless: bool = Field(default=False, description="Whether zone has a wireless sensor")
    battery_low: bool = Field(default=False, description="Whether wireless sensor has low battery")
    signal_strength: Optional[int] = Field(None, description="Wireless signal strength (0-10, None if not wireless)")
    tamper: bool = Field(default=False, description="Whether zone has tamper alert")


class AlarmStatusResponse(BaseModel):
    """Alarm status response model."""
    device_id: int = Field(..., description="Device ID")
    model: Optional[str] = Field(None, description="Alarm model")
    mac: Optional[str] = Field(None, description="MAC address")
    is_armed: bool = Field(..., description="Whether alarm is armed")
    arm_mode: str = Field(..., description="Current arm mode: disarmed, armed_away, armed_stay")
    is_triggered: bool = Field(..., description="Whether alarm is triggered")
    partitions: List[PartitionStatusInfo] = Field(default_factory=list, description="Partition statuses")
    partitions_enabled: bool = Field(default=False, description="Whether partitions are enabled on device")
    zones: List[ZoneStatusInfo] = Field(default_factory=list, description="Zone statuses")
    message: str = Field(..., description="Status message")
    # Eletrificador-specific fields
    is_eletrificador: bool = Field(default=False, description="Whether this is an electric fence device")
    shock_enabled: bool = Field(default=False, description="Whether shock/fence is enabled (eletrificador only)")
    shock_triggered: bool = Field(default=False, description="Whether shock/fence is triggered (eletrificador only)")
    alarm_enabled: bool = Field(default=False, description="Whether alarm is enabled (eletrificador only)")
    alarm_triggered: bool = Field(default=False, description="Whether alarm is triggered (eletrificador only)")
    # Connection status fields (for handling connection failures)
    connection_unavailable: bool = Field(default=False, description="True if using cached data due to connection failure")
    last_updated: Optional[str] = Field(None, description="ISO timestamp of when status was last successfully fetched")


class GetStatusRequest(BaseModel):
    """Get status request model."""
    password: Optional[str] = Field(None, min_length=4, max_length=6, description="Alarm panel password (4-6 digits). If not provided, uses saved password.")
    local_ip: Optional[str] = Field(None, description="Local IP address of the alarm panel (for direct connection)")
    local_port: Optional[int] = Field(None, description="Local port of the alarm panel (default: 9009)")
    save_password: bool = Field(default=False, description="Save password for future use")


@dataclass
class DeviceConnectionInfo:
    """Device connection information."""
    mac: str
    use_ip_receiver: bool = False
    ip_receiver_addr: Optional[str] = None
    ip_receiver_port: Optional[int] = None
    ip_receiver_account: Optional[str] = None


async def _get_password(session_id: str, device_id: int, request_password: Optional[str], save_password: bool = False) -> Optional[str]:
    """Get password from request or saved storage."""
    password = request_password

    # If no password provided, try to get saved one
    if not password:
        password = await state_manager.get_device_password(session_id, str(device_id))
        if password:
            logger.debug(f"Using saved password for device {device_id}")

    # Save password if requested and provided
    if save_password and request_password:
        await state_manager.set_device_password(session_id, str(device_id), request_password)
        logger.info(f"Saved password for device {device_id}")

    return password


async def _get_device_connection_info(access_token: str, device_id: int, use_cache: bool = True) -> Optional[DeviceConnectionInfo]:
    """Get device connection info from cache or cloud API.

    Args:
        access_token: OAuth access token
        device_id: Device ID
        use_cache: If True, check cache first (default: True)

    Returns:
        DeviceConnectionInfo or None if not found
    """
    # Check cache first for performance
    if use_cache:
        cached = await state_manager.get_device_conn_info(device_id)
        if cached:
            logger.debug(f"Using cached connection info for device {device_id}")
            return DeviceConnectionInfo(
                mac=cached.get("mac"),
                use_ip_receiver=cached.get("use_ip_receiver", False),
                ip_receiver_addr=cached.get("ip_receiver_addr"),
                ip_receiver_port=cached.get("ip_receiver_port"),
                ip_receiver_account=cached.get("ip_receiver_account")
            )

    # Fetch from cloud API
    try:
        raw_devices = await guardian_client.get_alarm_centrals(access_token)
        for device in raw_devices:
            if device.get("id") == device_id:
                mac = device.get("central_mac") or device.get("mac")
                if not mac:
                    return None

                # Check connection type
                connections = device.get("connections") or {}
                is_cloud = connections.get("is_cloud_enabled", False)
                is_ip_receiver = connections.get("is_ip_receiver_server_enabled", False)

                conn_info = None
                if is_cloud:
                    # Use cloud relay
                    conn_info = DeviceConnectionInfo(mac=mac, use_ip_receiver=False)
                elif is_ip_receiver:
                    # Use IP receiver
                    conn_info = DeviceConnectionInfo(
                        mac=mac,
                        use_ip_receiver=True,
                        ip_receiver_addr=device.get("ip_receiver_server_addr"),
                        ip_receiver_port=int(device.get("ip_receiver_server_port", 9009)),
                        ip_receiver_account=device.get("ip_receiver_server_account")
                    )
                else:
                    # Neither enabled - try cloud as fallback
                    logger.warning(f"Device {device_id} has no cloud or IP receiver enabled, trying cloud")
                    conn_info = DeviceConnectionInfo(mac=mac, use_ip_receiver=False)

                # Cache the connection info for future use
                if conn_info:
                    await state_manager.set_device_conn_info(device_id, {
                        "mac": conn_info.mac,
                        "use_ip_receiver": conn_info.use_ip_receiver,
                        "ip_receiver_addr": conn_info.ip_receiver_addr,
                        "ip_receiver_port": conn_info.ip_receiver_port,
                        "ip_receiver_account": conn_info.ip_receiver_account
                    })
                    logger.debug(f"Cached connection info for device {device_id}")

                return conn_info

    except Exception as e:
        logger.error(f"Error getting device connection info: {e}")
    return None


async def _get_open_zones(
    device_id: int,
    conn_info: DeviceConnectionInfo,
    password: str
) -> List[OpenZoneInfo]:
    """Get list of open zones for a device.

    This is called when arm fails with "Open zones" error to show which zones are open.

    Args:
        device_id: Device ID
        conn_info: Device connection info
        password: Device password

    Returns:
        List of open zones with their friendly names
    """
    open_zones = []
    try:
        # Get zone friendly names from storage
        friendly_names = await state_manager.get_all_zone_friendly_names(device_id)

        # Get status to find open zones
        success, status, message = await isecnet_client.get_status(
            device_id=device_id,
            mac=conn_info.mac,
            password=password,
            use_ip_receiver=conn_info.use_ip_receiver,
            ip_receiver_addr=conn_info.ip_receiver_addr,
            ip_receiver_port=conn_info.ip_receiver_port,
            ip_receiver_account=conn_info.ip_receiver_account
        )

        # Disconnect after getting status
        await isecnet_client.disconnect(device_id)

        if success and status.zones:
            for zone in status.zones:
                if zone.get("open", False):
                    zone_index = zone["index"]
                    open_zones.append(OpenZoneInfo(
                        index=zone_index,
                        name=f"Zona {zone_index + 1:02d}",
                        friendly_name=friendly_names.get(zone_index)
                    ))

        logger.debug(f"Found {len(open_zones)} open zones for device {device_id}")

    except Exception as e:
        logger.error(f"Error getting open zones: {e}")

    return open_zones


async def _get_partition_index(access_token: str, device_id: int, partition_id: int) -> Optional[int]:
    """Convert partition API ID to 0-based index.

    The API returns partitions with large IDs (e.g., 1589800), but the ISECNet
    protocol expects a partition index (0, 1, 2, etc.).

    IMPORTANT: If the device has only 1 partition (or no partitions), this returns
    None to indicate the partition byte should NOT be sent in the ISECNet command.
    Devices without partitions enabled return error 0xE3 (CENTRAL_DOES_NOT_HAVE_PARTITIONS)
    if a partition byte is included.

    Args:
        access_token: OAuth access token
        device_id: Device ID
        partition_id: Partition API ID

    Returns:
        0-based partition index if device has >1 partitions, or None if device
        has <=1 partitions (to skip partition byte in protocol)
    """
    try:
        raw_devices = await guardian_client.get_alarm_centrals(access_token)
        for device in raw_devices:
            if device.get("id") == device_id:
                partitions = device.get("partitions") or []

                # If device has only 1 partition or no partitions, return None
                # This tells the protocol to NOT include partition byte
                # (devices without partitions enabled return error 0xE3)
                if len(partitions) <= 1:
                    logger.debug(f"Device {device_id} has {len(partitions)} partition(s), skipping partition byte")
                    return None

                # Device has multiple partitions - find the index
                for idx, partition in enumerate(partitions):
                    if partition.get("id") == partition_id:
                        logger.debug(f"Partition ID {partition_id} -> index {idx}")
                        return idx
                # If partition_id is small (1, 2, 3), treat it as index directly
                if partition_id <= len(partitions):
                    logger.debug(f"Partition ID {partition_id} treated as 1-based index -> {partition_id - 1}")
                    return partition_id - 1
                break
    except Exception as e:
        logger.error(f"Error getting partition index: {e}")
    return None


@router.post("/{device_id}/arm", response_model=AlarmOperationResponse)
async def arm_partition(
    device_id: int,
    request: ArmRequest,
    x_session_id: str = Header(..., alias="X-Session-ID")
):
    """
    Arm a partition using ISECNet protocol.

    WARNING: This will actually arm your alarm system!

    The operation connects directly to the alarm panel via Intelbras cloud relay
    and sends the arm command using ISECNet V2 protocol.

    Args:
        device_id: Alarm central ID
        request: Arm request with partition_id, mode, and password (optional if saved)

    Requires X-Session-ID header from login.
    """
    try:
        # Get valid token to fetch device info
        access_token = await auth_service.get_valid_token(x_session_id)

        # Get password (from request or saved)
        password = await _get_password(x_session_id, device_id, request.password, request.save_password)
        if not password:
            raise AlarmOperationError("Password required. Provide password or save one first.")

        # Get device connection info from cloud API
        conn_info = await _get_device_connection_info(access_token, device_id)
        if not conn_info:
            raise DeviceNotFoundError(f"Device {device_id} not found or connection info not available")

        # Convert partition_id (API ID) to index (0-based) for protocol
        # Note: _get_partition_index returns None for single-partition devices
        # to indicate partition byte should be skipped (avoids 0xE3 error)
        partition_index = None
        if request.partition_id is not None:
            partition_index = await _get_partition_index(access_token, device_id, request.partition_id)
            # Don't fall back to index 0 - None is intentional for single-partition devices

        conn_type = "IP Receiver" if conn_info.use_ip_receiver else "Cloud"

        # Get cached partitions_enabled to know whether to include partition byte
        # This is set by auto-sync when getting status
        cached_partitions_enabled = await state_manager.get_device_partitions_enabled(device_id)
        logger.info(f"Arming device {device_id} (MAC: {conn_info.mac}) via {conn_type} partition_index={partition_index} mode={request.mode} partitions_enabled={cached_partitions_enabled}")

        # Arm using ISECNet protocol
        success, message = await isecnet_client.arm(
            device_id=device_id,
            mac=conn_info.mac,
            password=password,
            mode=request.mode.value,
            partition_index=partition_index,
            use_ip_receiver=conn_info.use_ip_receiver,
            ip_receiver_addr=conn_info.ip_receiver_addr,
            ip_receiver_port=conn_info.ip_receiver_port,
            ip_receiver_account=conn_info.ip_receiver_account,
            partitions_enabled=cached_partitions_enabled
        )

        # If command was sent but not confirmed (no response from panel),
        # verify status to check if arm actually succeeded
        if success and "command sent" in message:
            logger.info("ARM command sent without confirmation, verifying with status check...")
            # Small delay to allow panel to process the command
            await asyncio.sleep(0.5)

            # Check status to verify
            verify_success, status, verify_msg = await isecnet_client.get_status(
                device_id=device_id,
                mac=conn_info.mac,
                password=password,
                use_ip_receiver=conn_info.use_ip_receiver,
                ip_receiver_addr=conn_info.ip_receiver_addr,
                ip_receiver_port=conn_info.ip_receiver_port,
                ip_receiver_account=conn_info.ip_receiver_account
            )

            # Disconnect after status check
            await isecnet_client.disconnect(device_id)

            if verify_success:
                expected_mode = "armed_away" if request.mode == ArmMode.AWAY else "armed_stay"
                # Check if the panel is now armed (or arming)
                if status.is_armed or status.arm_mode in ["armed_away", "armed_stay"]:
                    logger.info(f"ARM verified: status shows {status.arm_mode}")
                    success = True
                    message = f"Armed ({request.mode.value})"
                elif status.arm_mode == "disarmed":
                    # Panel is still disarmed - ARM likely failed due to open zones
                    # Check for open zones - we already have them from status
                    open_zones_list = [z for z in status.zones if z.get("open", False)]
                    if open_zones_list:
                        logger.warning(f"ARM failed - panel still disarmed, {len(open_zones_list)} open zones detected")
                        # Get friendly names for the zones we found
                        friendly_names = await state_manager.get_all_zone_friendly_names(device_id)
                        open_zones = [
                            OpenZoneInfo(
                                index=z["index"],
                                name=f"Zona {z['index'] + 1:02d}",
                                friendly_name=friendly_names.get(z["index"])
                            )
                            for z in open_zones_list
                        ]
                        # Return error response directly with open zones
                        raise HTTPException(
                            status_code=400,
                            detail={
                                "error": "OpenZonesError",
                                "message": "Não é possível armar: existem zonas abertas",
                                "open_zones": [
                                    {
                                        "index": z.index,
                                        "name": z.name,
                                        "friendly_name": z.friendly_name
                                    }
                                    for z in open_zones
                                ]
                            }
                        )
                    else:
                        # No open zones but still disarmed - command may not have been received
                        logger.warning("ARM failed - panel still disarmed, no open zones detected")
                        success = False
                        message = "Arm command not accepted by panel"
            else:
                # Could not verify - assume command was sent and let UI sync handle the rest
                logger.warning(f"Could not verify ARM status: {verify_msg}")
                # Keep success=True since command was sent

        if not success:
            # Check if this is a connection error (central busy, offline, etc.)
            connection_errors = ["busy", "offline", "timeout", "connection", "not connected", "connect"]
            is_connection_error = any(err in message.lower() for err in connection_errors)

            if is_connection_error:
                # Connection blocked - likely AMT legacy app or network issue
                raise HTTPException(
                    status_code=503,
                    detail={
                        "error": "ConnectionUnavailable",
                        "message": f"Conexao com a central indisponivel: {message}. Verifique se o app AMT nao esta aberto.",
                    }
                )

            # Check if this is an "Open zones" error - if so, fetch which zones are open
            if "Open zones" in message or "open zones" in message.lower():
                open_zones = await _get_open_zones(
                    device_id=device_id,
                    conn_info=conn_info,
                    password=password
                )

                # Return error response with open zones info
                raise HTTPException(
                    status_code=400,
                    detail={
                        "error": "OpenZonesError",
                        "message": "Não é possível armar: existem zonas abertas",
                        "open_zones": [
                            {
                                "index": z.index,
                                "name": z.name,
                                "friendly_name": z.friendly_name
                            }
                            for z in open_zones
                        ]
                    }
                )
            raise AlarmOperationError(f"Failed to arm: {message}")

        # Clear device cache to force refresh
        await state_manager.delete_device_state(device_id)

        # Determine new status based on mode
        new_status = "armed_away" if request.mode == ArmMode.AWAY else "armed_stay"

        # Broadcast SSE event for instant HA state update
        await event_stream.broadcast_event({
            "event_type": "state_changed",
            "device_id": device_id,
            "partition_id": request.partition_id,
            "new_status": new_status,
            "source": "command",
        }, event_type="alarm_event")

        return AlarmOperationResponse(
            success=True,
            device_id=device_id,
            partition_id=request.partition_id,
            new_status=new_status,
            message=f"Armed in {request.mode.value} mode"
        )

    except HTTPException:
        # Re-raise HTTPExceptions (including OpenZonesError) as-is
        raise
    except InvalidSessionError as e:
        raise HTTPException(status_code=401, detail=str(e.message))
    except AlarmOperationError as e:
        raise HTTPException(status_code=400, detail=str(e.message))
    except DeviceNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e.message))
    except APIConnectionError as e:
        raise HTTPException(status_code=503, detail=str(e.message))
    except Exception as e:
        logger.error(f"Unexpected error arming: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{device_id}/disarm", response_model=AlarmOperationResponse)
async def disarm_partition(
    device_id: int,
    request: DisarmRequest,
    x_session_id: str = Header(..., alias="X-Session-ID")
):
    """
    Disarm a partition using ISECNet protocol.

    WARNING: This will actually disarm your alarm system!

    The operation connects directly to the alarm panel via Intelbras cloud relay
    and sends the disarm command using ISECNet V2 protocol.

    Args:
        device_id: Alarm central ID
        request: Disarm request with partition_id and password (optional if saved)

    Requires X-Session-ID header from login.
    """
    try:
        # Get valid token to fetch device info
        access_token = await auth_service.get_valid_token(x_session_id)

        # Get password (from request or saved)
        password = await _get_password(x_session_id, device_id, request.password, request.save_password)
        if not password:
            raise AlarmOperationError("Password required. Provide password or save one first.")

        # Get device connection info from cloud API
        conn_info = await _get_device_connection_info(access_token, device_id)
        if not conn_info:
            raise DeviceNotFoundError(f"Device {device_id} not found or connection info not available")

        # Convert partition_id (API ID) to index (0-based) for protocol
        # Note: _get_partition_index returns None for single-partition devices
        # to indicate partition byte should be skipped (avoids 0xE3 error)
        partition_index = None
        if request.partition_id is not None:
            partition_index = await _get_partition_index(access_token, device_id, request.partition_id)
            # Don't fall back to index 0 - None is intentional for single-partition devices

        conn_type = "IP Receiver" if conn_info.use_ip_receiver else "Cloud"

        # Get cached partitions_enabled to know whether to include partition byte
        # This is set by auto-sync when getting status
        cached_partitions_enabled = await state_manager.get_device_partitions_enabled(device_id)
        logger.info(f"Disarming device {device_id} (MAC: {conn_info.mac}) via {conn_type} partition_index={partition_index} partitions_enabled={cached_partitions_enabled}")

        # Disarm using ISECNet protocol
        success, message = await isecnet_client.disarm(
            device_id=device_id,
            mac=conn_info.mac,
            password=password,
            partition_index=partition_index,
            use_ip_receiver=conn_info.use_ip_receiver,
            ip_receiver_addr=conn_info.ip_receiver_addr,
            ip_receiver_port=conn_info.ip_receiver_port,
            ip_receiver_account=conn_info.ip_receiver_account,
            partitions_enabled=cached_partitions_enabled
        )

        if not success:
            # Check if this is a connection error (central busy, offline, etc.)
            connection_errors = ["busy", "offline", "timeout", "connection", "not connected", "connect"]
            is_connection_error = any(err in message.lower() for err in connection_errors)

            if is_connection_error:
                # Connection blocked - likely AMT legacy app or network issue
                raise HTTPException(
                    status_code=503,
                    detail={
                        "error": "ConnectionUnavailable",
                        "message": f"Conexao com a central indisponivel: {message}. Verifique se o app AMT nao esta aberto.",
                    }
                )

            raise AlarmOperationError(f"Failed to disarm: {message}")

        # Clear device cache to force refresh
        await state_manager.delete_device_state(device_id)

        # Broadcast SSE event for instant HA state update
        await event_stream.broadcast_event({
            "event_type": "state_changed",
            "device_id": device_id,
            "partition_id": request.partition_id,
            "new_status": "disarmed",
            "source": "command",
        }, event_type="alarm_event")

        return AlarmOperationResponse(
            success=True,
            device_id=device_id,
            partition_id=request.partition_id,
            new_status="disarmed",
            message="Disarmed successfully"
        )

    except HTTPException:
        # Re-raise HTTPExceptions as-is
        raise
    except InvalidSessionError as e:
        raise HTTPException(status_code=401, detail=str(e.message))
    except AlarmOperationError as e:
        raise HTTPException(status_code=400, detail=str(e.message))
    except DeviceNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e.message))
    except APIConnectionError as e:
        raise HTTPException(status_code=503, detail=str(e.message))
    except Exception as e:
        logger.error(f"Unexpected error disarming: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{device_id}/bypass-zone", response_model=AlarmOperationResponse)
async def bypass_zones(
    device_id: int,
    request: BypassZoneRequest,
    x_session_id: str = Header(..., alias="X-Session-ID")
):
    """
    Bypass (anular) or unbypass zones using ISECNet protocol.

    Bypassed zones are ignored during arm, allowing the alarm to arm
    even with open zones.

    Args:
        device_id: Alarm central ID
        request: Bypass request with zone_indices and bypass flag

    Requires X-Session-ID header from login.
    """
    try:
        access_token = await auth_service.get_valid_token(x_session_id)

        password = await _get_password(x_session_id, device_id, request.password, request.save_password)
        if not password:
            raise AlarmOperationError("Password required. Provide password or save one first.")

        conn_info = await _get_device_connection_info(access_token, device_id)
        if not conn_info:
            raise DeviceNotFoundError(f"Device {device_id} not found or connection info not available")

        conn_type = "IP Receiver" if conn_info.use_ip_receiver else "Cloud"
        action = "Bypassing" if request.bypass else "Unbypassing"
        logger.info(f"{action} zones {request.zone_indices} on device {device_id} (MAC: {conn_info.mac}) via {conn_type}")

        success, message = await isecnet_client.bypass_zones(
            device_id=device_id,
            mac=conn_info.mac,
            password=password,
            zone_indices=request.zone_indices,
            bypass=request.bypass,
            use_ip_receiver=conn_info.use_ip_receiver,
            ip_receiver_addr=conn_info.ip_receiver_addr,
            ip_receiver_port=conn_info.ip_receiver_port,
            ip_receiver_account=conn_info.ip_receiver_account
        )

        if not success:
            connection_errors = ["busy", "offline", "timeout", "connection", "not connected", "connect"]
            is_connection_error = any(err in message.lower() for err in connection_errors)

            if is_connection_error:
                raise HTTPException(
                    status_code=503,
                    detail={
                        "error": "ConnectionUnavailable",
                        "message": f"Conexao com a central indisponivel: {message}. Verifique se o app AMT nao esta aberto.",
                    }
                )

            raise AlarmOperationError(f"Bypass failed: {message}")

        # Clear device cache to force refresh
        await state_manager.delete_device_state(device_id)

        return AlarmOperationResponse(
            success=True,
            device_id=device_id,
            partition_id=None,
            new_status=None,
            message=message
        )

    except HTTPException:
        raise
    except InvalidSessionError as e:
        raise HTTPException(status_code=401, detail=str(e.message))
    except AlarmOperationError as e:
        raise HTTPException(status_code=400, detail=str(e.message))
    except DeviceNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e.message))
    except APIConnectionError as e:
        raise HTTPException(status_code=503, detail=str(e.message))
    except Exception as e:
        logger.error(f"Unexpected error bypassing zones: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{device_id}/status", response_model=AlarmStatusResponse)
async def get_alarm_status(
    device_id: int,
    request: GetStatusRequest,
    x_session_id: str = Header(..., alias="X-Session-ID")
):
    """
    Get current alarm status using ISECNet protocol.

    Returns real-time partition status directly from the alarm panel.

    Args:
        device_id: Alarm central ID
        request: Status request with password (optional if saved)

    Requires X-Session-ID header from login.
    """
    try:
        # Get valid token to fetch device info
        access_token = await auth_service.get_valid_token(x_session_id)

        # Get password (from request or saved)
        password = await _get_password(x_session_id, device_id, request.password, request.save_password)
        if not password:
            raise AlarmOperationError("Password required. Provide password or save one first.")

        # Get device connection info from cloud API
        conn_info = await _get_device_connection_info(access_token, device_id)
        if not conn_info:
            raise DeviceNotFoundError(f"Device {device_id} not found or connection info not available")

        conn_type = "IP Receiver" if conn_info.use_ip_receiver else "Cloud"
        logger.info(f"Getting status for device {device_id} (MAC: {conn_info.mac}) via {conn_type}")

        # Get status using ISECNet protocol
        success, status, message = await isecnet_client.get_status(
            device_id=device_id,
            mac=conn_info.mac,
            password=password,
            use_ip_receiver=conn_info.use_ip_receiver,
            ip_receiver_addr=conn_info.ip_receiver_addr,
            ip_receiver_port=conn_info.ip_receiver_port,
            ip_receiver_account=conn_info.ip_receiver_account
        )

        if not success:
            raise APIConnectionError(f"Failed to get status: {message}")

        # Cache partitions_enabled for arm/disarm commands
        await state_manager.set_device_partitions_enabled(device_id, status.partitions_enabled)

        # Convert partitions to response format
        partitions = [
            PartitionStatusInfo(index=p["index"], state=p["state"])
            for p in status.partitions
        ]

        # Convert zones to response format
        zones = [
            ZoneStatusInfo(
                index=z["index"],
                name=f"Zona {z['index'] + 1:02d}",
                is_open=z.get("open", False),
                is_bypassed=z.get("bypassed", False),
                is_wireless=z.get("is_wireless", False),
                battery_low=z.get("battery_low", False),
                signal_strength=z.get("signal"),
                tamper=z.get("tamper", False),
            )
            for z in status.zones
        ]

        return AlarmStatusResponse(
            device_id=device_id,
            model=status.model,
            mac=conn_info.mac,
            is_armed=status.is_armed,
            arm_mode=status.arm_mode,
            is_triggered=status.is_triggered,
            partitions=partitions,
            partitions_enabled=status.partitions_enabled,
            zones=zones,
            message="Status retrieved successfully",
            # Eletrificador-specific fields
            is_eletrificador=status.is_eletrificador,
            shock_enabled=status.shock_enabled,
            shock_triggered=status.shock_triggered,
            alarm_enabled=status.alarm_enabled,
            alarm_triggered=status.alarm_triggered
        )

    except InvalidSessionError as e:
        raise HTTPException(status_code=401, detail=str(e.message))
    except AlarmOperationError as e:
        raise HTTPException(status_code=400, detail=str(e.message))
    except DeviceNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e.message))
    except APIConnectionError as e:
        raise HTTPException(status_code=503, detail=str(e.message))
    except Exception as e:
        logger.error(f"Unexpected error getting status: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{device_id}/status/auto", response_model=AlarmStatusResponse)
async def get_alarm_status_auto(
    device_id: int,
    x_session_id: str = Header(..., alias="X-Session-ID")
):
    """
    Get current alarm status using saved password.

    This is the auto-sync endpoint that uses a previously saved password.
    Returns real-time partition status directly from the alarm panel.

    If the connection to the alarm panel fails (e.g., AMT legacy app is blocking
    the connection), this endpoint will return the last known status with
    `connection_unavailable: true` flag.

    NOTE: This endpoint disconnects after getting status to allow
    sequential syncing of multiple devices without conflicts.

    Args:
        device_id: Alarm central ID

    Requires X-Session-ID header from login and a saved password for the device.
    """
    try:
        # Get valid token to fetch device info
        access_token = await auth_service.get_valid_token(x_session_id)

        # Get saved password
        password = await state_manager.get_device_password(x_session_id, str(device_id))
        if not password:
            raise AlarmOperationError("No saved password for this device. Save a password first.")

        # Get device connection info from cloud API
        conn_info = await _get_device_connection_info(access_token, device_id)
        if not conn_info:
            raise DeviceNotFoundError(f"Device {device_id} not found or connection info not available")

        conn_type = "IP Receiver" if conn_info.use_ip_receiver else "Cloud"
        logger.info(f"Auto-sync status for device {device_id} (MAC: {conn_info.mac}) via {conn_type}")

        # Get status using ISECNet protocol
        success, status, message = await isecnet_client.get_status(
            device_id=device_id,
            mac=conn_info.mac,
            password=password,
            use_ip_receiver=conn_info.use_ip_receiver,
            ip_receiver_addr=conn_info.ip_receiver_addr,
            ip_receiver_port=conn_info.ip_receiver_port,
            ip_receiver_account=conn_info.ip_receiver_account
        )

        # Keep connection alive for fast subsequent polls.
        # The ISECNet client's keep-alive loop handles idle cleanup (5 min timeout).
        # Only disconnect on failure (handled below).

        # If connection failed, try to return last known status
        if not success:
            logger.warning(f"Failed to get real-time status for device {device_id}: {message}")

            # Try to get last known status from persistent cache
            last_known = await state_manager.get_last_known_status(device_id)
            if last_known:
                logger.info(f"Returning last known status for device {device_id} (from {last_known.get('_last_updated')})")

                # Convert cached data to response format
                partitions = [
                    PartitionStatusInfo(index=p["index"], state=p["state"])
                    for p in last_known.get("partitions", [])
                ]
                zones = [
                    ZoneStatusInfo(
                        index=z["index"],
                        name=z.get("name", f"Zona {z['index'] + 1:02d}"),
                        is_open=z.get("is_open", False),
                        is_bypassed=z.get("is_bypassed", False),
                        is_wireless=z.get("is_wireless", False),
                        battery_low=z.get("battery_low", False),
                        signal_strength=z.get("signal_strength"),
                        tamper=z.get("tamper", False),
                    )
                    for z in last_known.get("zones", [])
                ]

                return AlarmStatusResponse(
                    device_id=device_id,
                    model=last_known.get("model"),
                    mac=last_known.get("mac"),
                    is_armed=last_known.get("is_armed", False),
                    arm_mode=last_known.get("arm_mode", "disarmed"),
                    is_triggered=last_known.get("is_triggered", False),
                    partitions=partitions,
                    partitions_enabled=last_known.get("partitions_enabled", False),
                    zones=zones,
                    message=f"Conexao indisponivel - usando ultimo estado conhecido. Erro: {message}",
                    # Eletrificador-specific fields
                    is_eletrificador=last_known.get("is_eletrificador", False),
                    shock_enabled=last_known.get("shock_enabled", False),
                    shock_triggered=last_known.get("shock_triggered", False),
                    alarm_enabled=last_known.get("alarm_enabled", False),
                    alarm_triggered=last_known.get("alarm_triggered", False),
                    # Connection status
                    connection_unavailable=True,
                    last_updated=last_known.get("_last_updated")
                )
            else:
                # No cached status available - raise error
                raise APIConnectionError(f"Failed to get status: {message}")

        # Cache partitions_enabled for arm/disarm commands
        # This is crucial because after disconnect, the protocol instance is destroyed
        # and we need this info to know whether to include partition byte in commands
        await state_manager.set_device_partitions_enabled(device_id, status.partitions_enabled)
        logger.debug(f"Cached partitions_enabled={status.partitions_enabled} for device {device_id}")

        # Convert partitions to response format
        partitions = [
            PartitionStatusInfo(index=p["index"], state=p["state"])
            for p in status.partitions
        ]

        # Convert zones to response format
        zones = [
            ZoneStatusInfo(
                index=z["index"],
                name=f"Zona {z['index'] + 1:02d}",
                is_open=z.get("open", False),
                is_bypassed=z.get("bypassed", False),
                is_wireless=z.get("is_wireless", False),
                battery_low=z.get("battery_low", False),
                signal_strength=z.get("signal"),
                tamper=z.get("tamper", False),
            )
            for z in status.zones
        ]

        # Save last known status for future connection failures
        from datetime import datetime
        last_known_data = {
            "model": status.model,
            "mac": conn_info.mac,
            "is_armed": status.is_armed,
            "arm_mode": status.arm_mode,
            "is_triggered": status.is_triggered,
            "partitions": [{"index": p.index, "state": p.state} for p in partitions],
            "partitions_enabled": status.partitions_enabled,
            "zones": [{
                "index": z.index, "name": z.name, "is_open": z.is_open,
                "is_bypassed": z.is_bypassed, "is_wireless": z.is_wireless,
                "battery_low": z.battery_low, "signal_strength": z.signal_strength,
                "tamper": z.tamper,
            } for z in zones],
            "is_eletrificador": status.is_eletrificador,
            "shock_enabled": status.shock_enabled,
            "shock_triggered": status.shock_triggered,
            "alarm_enabled": status.alarm_enabled,
            "alarm_triggered": status.alarm_triggered,
        }
        await state_manager.set_last_known_status(device_id, last_known_data)

        return AlarmStatusResponse(
            device_id=device_id,
            model=status.model,
            mac=conn_info.mac,
            is_armed=status.is_armed,
            arm_mode=status.arm_mode,
            is_triggered=status.is_triggered,
            partitions=partitions,
            partitions_enabled=status.partitions_enabled,
            zones=zones,
            message="Auto-sync status retrieved successfully",
            # Eletrificador-specific fields
            is_eletrificador=status.is_eletrificador,
            shock_enabled=status.shock_enabled,
            shock_triggered=status.shock_triggered,
            alarm_enabled=status.alarm_enabled,
            alarm_triggered=status.alarm_triggered,
            # Connection status
            connection_unavailable=False,
            last_updated=datetime.utcnow().isoformat()
        )

    except InvalidSessionError as e:
        raise HTTPException(status_code=401, detail=str(e.message))
    except AlarmOperationError as e:
        raise HTTPException(status_code=400, detail=str(e.message))
    except DeviceNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e.message))
    except APIConnectionError as e:
        raise HTTPException(status_code=503, detail=str(e.message))
    except Exception as e:
        logger.error(f"Unexpected error getting auto status: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{device_id}/info")
async def get_alarm_info(
    device_id: int,
    x_session_id: str = Header(..., alias="X-Session-ID")
):
    """
    Get alarm device info from cloud API.

    Returns device information including partitions and zones from cloud.
    Note: This does NOT return real-time status - use POST /status for that.

    Requires X-Session-ID header from login.
    """
    try:
        # Get valid token
        access_token = await auth_service.get_valid_token(x_session_id)

        # Fetch device info from cloud API
        raw_devices = await guardian_client.get_alarm_centrals(access_token)

        for device in raw_devices:
            if device.get("id") == device_id:
                return {
                    "device_id": device_id,
                    "description": device.get("description"),
                    "mac": device.get("central_mac") or device.get("mac"),
                    "model": device.get("alarm_model") or device.get("model"),
                    "partitions": device.get("partitions", []),
                    "zones": device.get("sectors", device.get("zones", []))
                }

        raise DeviceNotFoundError(f"Device {device_id} not found")

    except InvalidSessionError as e:
        raise HTTPException(status_code=401, detail=str(e.message))
    except DeviceNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e.message))
    except APIConnectionError as e:
        raise HTTPException(status_code=503, detail=str(e.message))


class EletrificadorRequest(BaseModel):
    """Electric fence operation request model."""
    password: Optional[str] = Field(None, min_length=4, max_length=6, description="Electric fence password (4-6 digits). If not provided, uses saved password.")
    save_password: bool = Field(default=False, description="Save password for future use")


class EletrificadorOperationResponse(BaseModel):
    """Electric fence operation response model."""
    success: bool = Field(..., description="Whether operation succeeded")
    device_id: int = Field(..., description="Device ID")
    new_status: Optional[str] = Field(None, description="New device status")
    message: str = Field(..., description="Operation message")


@router.post("/{device_id}/eletrificador/activate", response_model=EletrificadorOperationResponse)
async def activate_eletrificador(
    device_id: int,
    request: EletrificadorRequest,
    x_session_id: str = Header(..., alias="X-Session-ID")
):
    """
    Activate (arm) eletrificador ALARM using ISECNet protocol.

    WARNING: This arms the ALARM function, not the shock!
    For shock control, use /eletrificador/shock/on and /eletrificador/shock/off

    This controls the ALARM function independently from the SHOCK function.
    - Activate = armar alarme
    - Deactivate = desarmar alarme

    Args:
        device_id: Electric fence device ID
        request: Request with password (optional if saved)

    Requires X-Session-ID header from login.
    """
    try:
        # Get valid token to fetch device info
        access_token = await auth_service.get_valid_token(x_session_id)

        # Get password (from request or saved)
        password = await _get_password(x_session_id, device_id, request.password, request.save_password)
        if not password:
            raise AlarmOperationError("Password required. Provide password or save one first.")

        # Get device connection info from cloud API
        conn_info = await _get_device_connection_info(access_token, device_id)
        if not conn_info:
            raise DeviceNotFoundError(f"Device {device_id} not found or connection info not available")

        conn_type = "IP Receiver" if conn_info.use_ip_receiver else "Cloud"
        logger.info(f"Activating (arming) eletrificador ALARM {device_id} (MAC: {conn_info.mac}) via {conn_type}")

        # Activate = ARM the alarm using dedicated eletrificador method (partition_index=0)
        # This controls ONLY the alarm, not the shock
        success, message = await isecnet_client.eletrificador_alarm_on(
            device_id=device_id,
            mac=conn_info.mac,
            password=password,
            use_ip_receiver=conn_info.use_ip_receiver,
            ip_receiver_addr=conn_info.ip_receiver_addr,
            ip_receiver_port=conn_info.ip_receiver_port,
            ip_receiver_account=conn_info.ip_receiver_account
        )

        if not success:
            raise AlarmOperationError(f"Failed to activate alarm: {message}")

        # Clear device cache to force refresh
        await state_manager.delete_device_state(device_id)

        return EletrificadorOperationResponse(
            success=True,
            device_id=device_id,
            new_status="alarm_armed",
            message="Alarme do eletrificador ARMADO com sucesso"
        )

    except InvalidSessionError as e:
        raise HTTPException(status_code=401, detail=str(e.message))
    except AlarmOperationError as e:
        raise HTTPException(status_code=400, detail=str(e.message))
    except DeviceNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e.message))
    except APIConnectionError as e:
        raise HTTPException(status_code=503, detail=str(e.message))
    except Exception as e:
        logger.error(f"Unexpected error activating eletrificador: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{device_id}/eletrificador/deactivate", response_model=EletrificadorOperationResponse)
async def deactivate_eletrificador(
    device_id: int,
    request: EletrificadorRequest,
    x_session_id: str = Header(..., alias="X-Session-ID")
):
    """
    Deactivate (disarm) eletrificador ALARM using ISECNet protocol.

    WARNING: This disarms the ALARM function, not the shock!
    For shock control, use /eletrificador/shock/on and /eletrificador/shock/off

    This controls the ALARM function independently from the SHOCK function.
    - Activate = armar alarme
    - Deactivate = desarmar alarme

    Args:
        device_id: Electric fence device ID
        request: Request with password (optional if saved)

    Requires X-Session-ID header from login.
    """
    try:
        # Get valid token to fetch device info
        access_token = await auth_service.get_valid_token(x_session_id)

        # Get password (from request or saved)
        password = await _get_password(x_session_id, device_id, request.password, request.save_password)
        if not password:
            raise AlarmOperationError("Password required. Provide password or save one first.")

        # Get device connection info from cloud API
        conn_info = await _get_device_connection_info(access_token, device_id)
        if not conn_info:
            raise DeviceNotFoundError(f"Device {device_id} not found or connection info not available")

        conn_type = "IP Receiver" if conn_info.use_ip_receiver else "Cloud"
        logger.info(f"Deactivating (disarming) eletrificador ALARM {device_id} (MAC: {conn_info.mac}) via {conn_type}")

        # Deactivate = DISARM the alarm using dedicated eletrificador method (partition_index=0)
        # This controls ONLY the alarm, not the shock
        success, message = await isecnet_client.eletrificador_alarm_off(
            device_id=device_id,
            mac=conn_info.mac,
            password=password,
            use_ip_receiver=conn_info.use_ip_receiver,
            ip_receiver_addr=conn_info.ip_receiver_addr,
            ip_receiver_port=conn_info.ip_receiver_port,
            ip_receiver_account=conn_info.ip_receiver_account
        )

        if not success:
            raise AlarmOperationError(f"Failed to deactivate alarm: {message}")

        # Clear device cache to force refresh
        await state_manager.delete_device_state(device_id)

        return EletrificadorOperationResponse(
            success=True,
            device_id=device_id,
            new_status="alarm_disarmed",
            message="Alarme do eletrificador DESARMADO com sucesso"
        )

    except InvalidSessionError as e:
        raise HTTPException(status_code=401, detail=str(e.message))
    except AlarmOperationError as e:
        raise HTTPException(status_code=400, detail=str(e.message))
    except DeviceNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e.message))
    except APIConnectionError as e:
        raise HTTPException(status_code=503, detail=str(e.message))
    except Exception as e:
        logger.error(f"Unexpected error deactivating eletrificador: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{device_id}/eletrificador/shock/on", response_model=EletrificadorOperationResponse)
async def shock_on(
    device_id: int,
    request: EletrificadorRequest,
    x_session_id: str = Header(..., alias="X-Session-ID")
):
    """
    Turn ON the electric fence shock (cerca/choque).

    WARNING: This will enable the shock on the electric fence!

    This controls the SHOCK/FENCE function independently from the ALARM function.
    - Shock ON = cerca energizada (fence energized)
    - Shock OFF = cerca desligada (fence off)

    Args:
        device_id: Electric fence device ID
        request: Request with password (optional if saved)

    Requires X-Session-ID header from login.
    """
    try:
        # Get valid token to fetch device info
        access_token = await auth_service.get_valid_token(x_session_id)

        # Get password (from request or saved)
        password = await _get_password(x_session_id, device_id, request.password, request.save_password)
        if not password:
            raise AlarmOperationError("Password required. Provide password or save one first.")

        # Get device connection info from cloud API
        conn_info = await _get_device_connection_info(access_token, device_id)
        if not conn_info:
            raise DeviceNotFoundError(f"Device {device_id} not found or connection info not available")

        conn_type = "IP Receiver" if conn_info.use_ip_receiver else "Cloud"
        logger.info(f"Turning SHOCK ON for eletrificador {device_id} (MAC: {conn_info.mac}) via {conn_type}")

        # Use shock_on command (SYSTEM_ARM_DISARM with partition_index=1)
        success, message = await isecnet_client.shock_on(
            device_id=device_id,
            mac=conn_info.mac,
            password=password,
            use_ip_receiver=conn_info.use_ip_receiver,
            ip_receiver_addr=conn_info.ip_receiver_addr,
            ip_receiver_port=conn_info.ip_receiver_port,
            ip_receiver_account=conn_info.ip_receiver_account
        )

        if not success:
            raise AlarmOperationError(f"Failed to turn shock on: {message}")

        # Clear device cache to force refresh
        await state_manager.delete_device_state(device_id)

        return EletrificadorOperationResponse(
            success=True,
            device_id=device_id,
            new_status="shock_on",
            message="Choque LIGADO com sucesso"
        )

    except InvalidSessionError as e:
        raise HTTPException(status_code=401, detail=str(e.message))
    except AlarmOperationError as e:
        raise HTTPException(status_code=400, detail=str(e.message))
    except DeviceNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e.message))
    except APIConnectionError as e:
        raise HTTPException(status_code=503, detail=str(e.message))
    except Exception as e:
        logger.error(f"Unexpected error turning shock on: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{device_id}/eletrificador/shock/off", response_model=EletrificadorOperationResponse)
async def shock_off(
    device_id: int,
    request: EletrificadorRequest,
    x_session_id: str = Header(..., alias="X-Session-ID")
):
    """
    Turn OFF the electric fence shock (cerca/choque).

    WARNING: This will disable the shock on the electric fence!

    This controls the SHOCK/FENCE function independently from the ALARM function.
    - Shock ON = cerca energizada (fence energized)
    - Shock OFF = cerca desligada (fence off)

    Args:
        device_id: Electric fence device ID
        request: Request with password (optional if saved)

    Requires X-Session-ID header from login.
    """
    try:
        # Get valid token to fetch device info
        access_token = await auth_service.get_valid_token(x_session_id)

        # Get password (from request or saved)
        password = await _get_password(x_session_id, device_id, request.password, request.save_password)
        if not password:
            raise AlarmOperationError("Password required. Provide password or save one first.")

        # Get device connection info from cloud API
        conn_info = await _get_device_connection_info(access_token, device_id)
        if not conn_info:
            raise DeviceNotFoundError(f"Device {device_id} not found or connection info not available")

        conn_type = "IP Receiver" if conn_info.use_ip_receiver else "Cloud"
        logger.info(f"Turning SHOCK OFF for eletrificador {device_id} (MAC: {conn_info.mac}) via {conn_type}")

        # Use shock_off command (SYSTEM_ARM_DISARM with partition_index=1)
        success, message = await isecnet_client.shock_off(
            device_id=device_id,
            mac=conn_info.mac,
            password=password,
            use_ip_receiver=conn_info.use_ip_receiver,
            ip_receiver_addr=conn_info.ip_receiver_addr,
            ip_receiver_port=conn_info.ip_receiver_port,
            ip_receiver_account=conn_info.ip_receiver_account
        )

        if not success:
            raise AlarmOperationError(f"Failed to turn shock off: {message}")

        # Clear device cache to force refresh
        await state_manager.delete_device_state(device_id)

        return EletrificadorOperationResponse(
            success=True,
            device_id=device_id,
            new_status="shock_off",
            message="Choque DESLIGADO com sucesso"
        )

    except InvalidSessionError as e:
        raise HTTPException(status_code=401, detail=str(e.message))
    except AlarmOperationError as e:
        raise HTTPException(status_code=400, detail=str(e.message))
    except DeviceNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e.message))
    except APIConnectionError as e:
        raise HTTPException(status_code=503, detail=str(e.message))
    except Exception as e:
        logger.error(f"Unexpected error turning shock off: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{device_id}/siren/off", response_model=EletrificadorOperationResponse)
async def turn_off_siren(
    device_id: int,
    request: EletrificadorRequest,
    x_session_id: str = Header(..., alias="X-Session-ID")
):
    """
    Turn off the alarm siren without changing the arm/disarm state.

    This silences the siren while keeping the alarm in its current state
    (armed_away, armed_home, etc).

    Args:
        device_id: Alarm central ID
        request: Request with password (optional if saved)

    Requires X-Session-ID header from login.
    """
    try:
        # Get valid token to fetch device info
        access_token = await auth_service.get_valid_token(x_session_id)

        # Get password (from request or saved)
        password = await _get_password(x_session_id, device_id, request.password, request.save_password)
        if not password:
            raise AlarmOperationError("Password required. Provide password or save one first.")

        # Get device connection info from cloud API
        conn_info = await _get_device_connection_info(access_token, device_id)
        if not conn_info:
            raise DeviceNotFoundError(f"Device {device_id} not found or connection info not available")

        conn_type = "IP Receiver" if conn_info.use_ip_receiver else "Cloud"
        logger.info(f"Turning siren OFF for device {device_id} (MAC: {conn_info.mac}) via {conn_type}")

        # Turn off siren using ISECNet protocol
        success, message = await isecnet_client.turn_off_siren(
            device_id=device_id,
            mac=conn_info.mac,
            password=password,
            use_ip_receiver=conn_info.use_ip_receiver,
            ip_receiver_addr=conn_info.ip_receiver_addr,
            ip_receiver_port=conn_info.ip_receiver_port,
            ip_receiver_account=conn_info.ip_receiver_account
        )

        if not success:
            # Check if this is a connection error
            connection_errors = ["busy", "offline", "timeout", "connection", "not connected", "connect"]
            is_connection_error = any(err in message.lower() for err in connection_errors)

            if is_connection_error:
                raise HTTPException(
                    status_code=503,
                    detail={
                        "error": "ConnectionUnavailable",
                        "message": f"Conexao com a central indisponivel: {message}. Verifique se o app AMT nao esta aberto.",
                    }
                )

            raise AlarmOperationError(f"Failed to turn off siren: {message}")

        # Broadcast SSE event - siren off doesn't change arm state,
        # but we signal it so HA can clear the triggered state
        # Get current arm mode from last known status
        last_known = await state_manager.get_last_known_status(device_id)
        current_status = last_known.get("arm_mode", "disarmed") if last_known else "disarmed"

        await event_stream.broadcast_event({
            "event_type": "state_changed",
            "device_id": device_id,
            "new_status": current_status,
            "source": "command",
        }, event_type="alarm_event")

        return EletrificadorOperationResponse(
            success=True,
            device_id=device_id,
            new_status=current_status,
            message="Sirene desligada com sucesso"
        )

    except HTTPException:
        raise
    except InvalidSessionError as e:
        raise HTTPException(status_code=401, detail=str(e.message))
    except AlarmOperationError as e:
        raise HTTPException(status_code=400, detail=str(e.message))
    except DeviceNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e.message))
    except APIConnectionError as e:
        raise HTTPException(status_code=503, detail=str(e.message))
    except Exception as e:
        logger.error(f"Unexpected error turning off siren: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{device_id}/debug/complete-status")
async def get_complete_status_debug(
    device_id: int,
    x_session_id: str = Header(..., alias="X-Session-ID")
):
    """
    Debug endpoint: get complete status as raw hex.

    Returns the raw hex bytes from GET_COMPLETE_STATUS (0x53) command.
    Use this to analyze byte positions for wireless sensor data (battery, signal).
    """
    try:
        access_token = await auth_service.get_valid_token(x_session_id)

        password = await state_manager.get_device_password(x_session_id, str(device_id))
        if not password:
            raise AlarmOperationError("No saved password for this device.")

        conn_info = await _get_device_connection_info(access_token, device_id)
        if not conn_info:
            raise DeviceNotFoundError(f"Device {device_id} not found")

        # Also get partial status for comparison
        partial_success, partial_status, _ = await isecnet_client.get_status(
            device_id=device_id,
            mac=conn_info.mac,
            password=password,
            use_ip_receiver=conn_info.use_ip_receiver,
            ip_receiver_addr=conn_info.ip_receiver_addr,
            ip_receiver_port=conn_info.ip_receiver_port,
            ip_receiver_account=conn_info.ip_receiver_account
        )

        # Get complete status
        success, hex_str = await isecnet_client.get_complete_status_raw(
            device_id=device_id,
            mac=conn_info.mac,
            password=password,
            use_ip_receiver=conn_info.use_ip_receiver,
            ip_receiver_addr=conn_info.ip_receiver_addr,
            ip_receiver_port=conn_info.ip_receiver_port,
            ip_receiver_account=conn_info.ip_receiver_account
        )

        if not success:
            raise AlarmOperationError(f"Failed: {hex_str}")

        # Format hex in groups for readability
        raw_bytes = bytes.fromhex(hex_str)
        formatted = " ".join(f"{b:02x}" for b in raw_bytes)
        # Also show with byte index annotations
        annotated = []
        for i, b in enumerate(raw_bytes):
            annotated.append(f"[{i:3d}] 0x{b:02X} ({b:3d})")

        # Determine which command was used based on model
        model_name = partial_status.model if partial_success else "unknown"
        cmd_map = {
            "AMT_2018_E_SMART": ("GET_SMART_STATUS (0x5D)", 93),
            "AMT_1000_SMART": ("GET_SMART_STATUS (0x5D)", 93),
            "AMT_4010": ("GET_EXTENDED_STATUS (0x5B)", 91),
        }
        cmd_info = cmd_map.get(model_name, ("GET_PARTIAL_STATUS (0x5A)", 90))

        # Annotate known byte positions for AMT 2018 E Smart (0x5D response)
        byte_map = {}
        if len(raw_bytes) > 100:
            byte_map = {
                "1": "0xE9 command echo",
                "2-7": "Zone open (48 zones, 6 bytes)",
                "8-13": "Zone violated/alarm (48 zones)",
                "14-19": "Zone bypassed (48 zones)",
                "20": "Model code",
                "21": "Firmware version",
                "22": "Partition config",
                "23": "Partition armed status",
                "32": "Battery level byte",
                "39": "Output/siren status",
                "40-45": "Enabled zones (48 zones)",
                "46-57": "Partition zone assignment",
                "58-63": "Stay zones",
                "64-69": "Wireless device present (bitmap, 48 zones)",
                "70-75": "Zone tamper (bitmap, 48 zones)",
                "76-81": "Zone in short (bitmap, 48 zones)",
                "82-87": "ZONE BATTERY LOW (bitmap, 48 zones)",
                "94": "Stay armed status",
                "95": "User partition permission",
                "97-99": "Zone supervision failure",
                "100-107": "Wireless device model",
                "108-115": "WIRELESS DEVICE SIGNAL",
                "132-134": "Zone supervision mode",
                "135": "Zone type",
            }

        return {
            "device_id": device_id,
            "model": model_name,
            "command": cmd_info[0],
            "command_byte": f"0x{cmd_info[1]:02X}",
            "total_bytes": len(raw_bytes),
            "hex_raw": hex_str,
            "hex_formatted": formatted,
            "bytes_annotated": annotated,
            "byte_map": byte_map if byte_map else None,
        }

    except InvalidSessionError as e:
        raise HTTPException(status_code=401, detail=str(e.message))
    except AlarmOperationError as e:
        raise HTTPException(status_code=400, detail=str(e.message))
    except DeviceNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e.message))
    except Exception as e:
        logger.error(f"Debug complete status error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{device_id}/disconnect")
async def disconnect_device(
    device_id: int,
    x_session_id: str = Header(..., alias="X-Session-ID")
):
    """
    Disconnect ISECNet connection to a device.

    This closes the socket connection to the alarm panel.
    Useful for freeing resources or forcing a fresh connection.

    Args:
        device_id: Alarm central ID

    Requires X-Session-ID header from login.
    """
    try:
        # Validate session
        await auth_service.get_valid_token(x_session_id)

        # Disconnect
        success, message = await isecnet_client.disconnect(device_id)

        return {
            "success": success,
            "device_id": device_id,
            "message": message
        }

    except InvalidSessionError as e:
        raise HTTPException(status_code=401, detail=str(e.message))
