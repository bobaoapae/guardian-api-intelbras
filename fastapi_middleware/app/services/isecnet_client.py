"""ISECNet Client Service - Connection Manager for Intelbras Alarms.

This service manages ISECNet protocol connections to multiple alarm panels.
It handles connection pooling, reconnection, and provides a high-level API
for alarm operations.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple

from app.services.isecnet_protocol import ISECNetProtocol, AlarmStatus

logger = logging.getLogger(__name__)


@dataclass
class DeviceConnection:
    """Represents an active connection to an alarm panel."""
    device_id: int
    mac: str
    password: str
    protocol: ISECNetProtocol
    use_ip_receiver: bool = False
    ip_receiver_addr: Optional[str] = None
    ip_receiver_port: Optional[int] = None
    ip_receiver_account: Optional[str] = None
    connected_at: datetime = field(default_factory=datetime.now)
    last_activity: datetime = field(default_factory=datetime.now)
    reconnect_attempts: int = 0
    max_reconnect_attempts: int = 3


class ISECNetClient:
    """
    ISECNet Client Service.

    Manages connections to multiple Intelbras alarm panels via ISECNet protocol.
    Provides connection pooling, automatic reconnection, and high-level operations.
    """

    # Connection timeout (disconnect if idle for this long)
    CONNECTION_TIMEOUT = timedelta(minutes=5)

    # Keep-alive interval
    KEEP_ALIVE_INTERVAL = 60  # seconds

    def __init__(self):
        """Initialize the ISECNet client."""
        self._connections: Dict[int, DeviceConnection] = {}
        self._lock = asyncio.Lock()
        self._keep_alive_task: Optional[asyncio.Task] = None
        self._running = False

    async def start(self):
        """Start the client service (including keep-alive loop)."""
        if self._running:
            return

        self._running = True
        self._keep_alive_task = asyncio.create_task(self._keep_alive_loop())
        logger.info("ISECNet client service started")

    async def stop(self):
        """Stop the client service and disconnect all devices."""
        self._running = False

        if self._keep_alive_task:
            self._keep_alive_task.cancel()
            try:
                await self._keep_alive_task
            except asyncio.CancelledError:
                pass

        # Disconnect all devices
        async with self._lock:
            for device_id in list(self._connections.keys()):
                await self._disconnect_device(device_id)

        logger.info("ISECNet client service stopped")

    async def _keep_alive_loop(self):
        """Background task to send keep-alive and clean up idle connections."""
        while self._running:
            try:
                await asyncio.sleep(self.KEEP_ALIVE_INTERVAL)

                async with self._lock:
                    now = datetime.now()
                    to_disconnect = []

                    for device_id, conn in self._connections.items():
                        # Check for idle timeout
                        if now - conn.last_activity > self.CONNECTION_TIMEOUT:
                            logger.info(f"Device {device_id} idle timeout, disconnecting")
                            to_disconnect.append(device_id)
                        # TODO: Could send keep-alive packets here if needed

                    # Disconnect idle devices
                    for device_id in to_disconnect:
                        await self._disconnect_device(device_id)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in keep-alive loop: {e}")

    async def _disconnect_device(self, device_id: int):
        """Disconnect a device (internal, assumes lock is held)."""
        if device_id in self._connections:
            conn = self._connections[device_id]
            try:
                await conn.protocol.disconnect()
            except Exception as e:
                logger.warning(f"Error disconnecting device {device_id}: {e}")
            del self._connections[device_id]
            logger.info(f"Device {device_id} disconnected")

    async def connect(
        self,
        device_id: int,
        mac: str,
        password: str,
        force_reconnect: bool = False,
        use_ip_receiver: bool = False,
        ip_receiver_addr: Optional[str] = None,
        ip_receiver_port: Optional[int] = None,
        ip_receiver_account: Optional[str] = None
    ) -> Tuple[bool, str]:
        """
        Connect to an alarm panel.

        Args:
            device_id: Device ID from cloud API
            mac: Device MAC address
            password: Alarm panel password (6 digits)
            force_reconnect: Force reconnection even if already connected
            use_ip_receiver: Whether to use IP receiver instead of cloud
            ip_receiver_addr: IP receiver server address
            ip_receiver_port: IP receiver server port
            ip_receiver_account: IP receiver account number

        Returns:
            Tuple of (success, message)
        """
        async with self._lock:
            # Check existing connection
            if device_id in self._connections:
                conn = self._connections[device_id]
                if conn.protocol.is_authenticated and not force_reconnect:
                    conn.last_activity = datetime.now()
                    return True, "Already connected"
                else:
                    # Disconnect existing
                    await self._disconnect_device(device_id)

            # Create new connection
            protocol = ISECNetProtocol()

            # Format MAC for protocol (remove colons if present)
            clean_mac = mac.replace(":", "").replace("-", "").upper()

            if use_ip_receiver:
                logger.info(f"Connecting to device {device_id} via IP Receiver {ip_receiver_addr}:{ip_receiver_port}")
                success, message = await protocol.connect(
                    mac=clean_mac,
                    password=password,
                    device_id=ip_receiver_account or str(device_id),
                    ip_receiver_address=ip_receiver_addr,
                    ip_receiver_port=ip_receiver_port
                )
            else:
                logger.info(f"Connecting to device {device_id} via Cloud (MAC: {clean_mac})")
                success, message = await protocol.connect(
                    mac=clean_mac,
                    password=password,
                    device_id=str(device_id)
                )

            if success:
                self._connections[device_id] = DeviceConnection(
                    device_id=device_id,
                    mac=clean_mac,
                    password=password,
                    protocol=protocol,
                    use_ip_receiver=use_ip_receiver,
                    ip_receiver_addr=ip_receiver_addr,
                    ip_receiver_port=ip_receiver_port,
                    ip_receiver_account=ip_receiver_account
                )
                logger.info(f"Device {device_id} connected successfully")
            else:
                logger.error(f"Device {device_id} connection failed: {message}")

            return success, message

    async def disconnect(self, device_id: int) -> Tuple[bool, str]:
        """
        Disconnect from an alarm panel.

        Args:
            device_id: Device ID

        Returns:
            Tuple of (success, message)
        """
        async with self._lock:
            if device_id not in self._connections:
                return True, "Not connected"

            await self._disconnect_device(device_id)
            return True, "Disconnected"

    async def _ensure_connected(
        self,
        device_id: int,
        mac: str,
        password: str,
        use_ip_receiver: bool = False,
        ip_receiver_addr: Optional[str] = None,
        ip_receiver_port: Optional[int] = None,
        ip_receiver_account: Optional[str] = None
    ) -> Tuple[bool, Optional[DeviceConnection]]:
        """Ensure device is connected, reconnecting if necessary."""
        async with self._lock:
            if device_id in self._connections:
                conn = self._connections[device_id]
                if conn.protocol.is_authenticated:
                    conn.last_activity = datetime.now()
                    return True, conn

        # Need to connect
        success, message = await self.connect(
            device_id=device_id,
            mac=mac,
            password=password,
            use_ip_receiver=use_ip_receiver,
            ip_receiver_addr=ip_receiver_addr,
            ip_receiver_port=ip_receiver_port,
            ip_receiver_account=ip_receiver_account
        )
        if success:
            async with self._lock:
                return True, self._connections.get(device_id)

        return False, None

    async def get_status(
        self,
        device_id: int,
        mac: str,
        password: str,
        use_ip_receiver: bool = False,
        ip_receiver_addr: Optional[str] = None,
        ip_receiver_port: Optional[int] = None,
        ip_receiver_account: Optional[str] = None
    ) -> Tuple[bool, AlarmStatus, str]:
        """
        Get alarm panel status.

        Args:
            device_id: Device ID
            mac: Device MAC address
            password: Alarm panel password
            use_ip_receiver: Use IP receiver instead of cloud
            ip_receiver_addr: IP receiver server address
            ip_receiver_port: IP receiver server port
            ip_receiver_account: IP receiver account

        Returns:
            Tuple of (success, AlarmStatus, message)
        """
        success, conn = await self._ensure_connected(
            device_id, mac, password,
            use_ip_receiver, ip_receiver_addr, ip_receiver_port, ip_receiver_account
        )
        if not success or not conn:
            return False, AlarmStatus(), "Not connected"

        try:
            success, status = await conn.protocol.get_status()
            if success:
                return True, status, "OK"
            else:
                return False, AlarmStatus(), "Failed to get status"
        except Exception as e:
            logger.error(f"Error getting status for device {device_id}: {e}")
            # Try reconnecting on error
            async with self._lock:
                await self._disconnect_device(device_id)
            return False, AlarmStatus(), str(e)

    async def arm(
        self,
        device_id: int,
        mac: str,
        password: str,
        mode: str = "away",
        partition_index: Optional[int] = None,
        use_ip_receiver: bool = False,
        ip_receiver_addr: Optional[str] = None,
        ip_receiver_port: Optional[int] = None,
        ip_receiver_account: Optional[str] = None,
        partitions_enabled: Optional[bool] = None
    ) -> Tuple[bool, str]:
        """
        Arm the alarm.

        Args:
            device_id: Device ID
            mac: Device MAC address
            password: Alarm panel password
            mode: "away" for total arm, "stay" for partial arm
            partition_index: Specific partition (None = all)
            use_ip_receiver: Use IP receiver instead of cloud
            ip_receiver_addr: IP receiver server address
            ip_receiver_port: IP receiver server port
            ip_receiver_account: IP receiver account
            partitions_enabled: If provided, use this to decide whether to include
                               partition byte in command (avoids 0xE3 error)

        Returns:
            Tuple of (success, message)
        """
        success, conn = await self._ensure_connected(
            device_id, mac, password,
            use_ip_receiver, ip_receiver_addr, ip_receiver_port, ip_receiver_account
        )
        if not success or not conn:
            return False, "Not connected"

        try:
            success, message = await conn.protocol.arm(mode, partition_index, partitions_enabled)
            return success, message
        except Exception as e:
            logger.error(f"Error arming device {device_id}: {e}")
            async with self._lock:
                await self._disconnect_device(device_id)
            return False, str(e)

    async def disarm(
        self,
        device_id: int,
        mac: str,
        password: str,
        partition_index: Optional[int] = None,
        use_ip_receiver: bool = False,
        ip_receiver_addr: Optional[str] = None,
        ip_receiver_port: Optional[int] = None,
        ip_receiver_account: Optional[str] = None,
        partitions_enabled: Optional[bool] = None
    ) -> Tuple[bool, str]:
        """
        Disarm the alarm.

        Args:
            device_id: Device ID
            mac: Device MAC address
            password: Alarm panel password
            partition_index: Specific partition (None = all)
            use_ip_receiver: Use IP receiver instead of cloud
            ip_receiver_addr: IP receiver server address
            ip_receiver_port: IP receiver server port
            ip_receiver_account: IP receiver account
            partitions_enabled: If provided, use this to decide whether to include
                               partition byte in command (avoids 0xE3 error)

        Returns:
            Tuple of (success, message)
        """
        success, conn = await self._ensure_connected(
            device_id, mac, password,
            use_ip_receiver, ip_receiver_addr, ip_receiver_port, ip_receiver_account
        )
        if not success or not conn:
            return False, "Not connected"

        try:
            success, message = await conn.protocol.disarm(partition_index, partitions_enabled)
            return success, message
        except Exception as e:
            logger.error(f"Error disarming device {device_id}: {e}")
            async with self._lock:
                await self._disconnect_device(device_id)
            return False, str(e)

    async def shock_on(
        self,
        device_id: int,
        mac: str,
        password: str,
        zones: list = None,
        use_ip_receiver: bool = False,
        ip_receiver_addr: str = None,
        ip_receiver_port: int = None,
        ip_receiver_account: str = None
    ) -> Tuple[bool, str]:
        """
        Turn on eletrificador shock (fence).

        This controls the shock/fence function independently from the alarm.

        Args:
            device_id: Device ID
            mac: Device MAC address
            password: Alarm panel password
            zones: List of zone indices (0-7) to turn on. If None, turns on all zones.
            use_ip_receiver: Use IP receiver instead of cloud
            ip_receiver_addr: IP receiver server address
            ip_receiver_port: IP receiver server port
            ip_receiver_account: IP receiver account

        Returns:
            Tuple of (success, message)
        """
        success, conn = await self._ensure_connected(
            device_id, mac, password,
            use_ip_receiver, ip_receiver_addr, ip_receiver_port, ip_receiver_account
        )
        if not success or not conn:
            return False, "Not connected"

        try:
            success, message = await conn.protocol.shock_on(zones)
            return success, message
        except Exception as e:
            logger.error(f"Error turning shock on for device {device_id}: {e}")
            async with self._lock:
                await self._disconnect_device(device_id)
            return False, str(e)

    async def shock_off(
        self,
        device_id: int,
        mac: str,
        password: str,
        zones: list = None,
        use_ip_receiver: bool = False,
        ip_receiver_addr: str = None,
        ip_receiver_port: int = None,
        ip_receiver_account: str = None
    ) -> Tuple[bool, str]:
        """
        Turn off eletrificador shock (fence).

        This controls the shock/fence function independently from the alarm.

        Args:
            device_id: Device ID
            mac: Device MAC address
            password: Alarm panel password
            zones: List of zone indices (0-7) to turn off. If None, turns off all zones.
            use_ip_receiver: Use IP receiver instead of cloud
            ip_receiver_addr: IP receiver server address
            ip_receiver_port: IP receiver server port
            ip_receiver_account: IP receiver account

        Returns:
            Tuple of (success, message)
        """
        success, conn = await self._ensure_connected(
            device_id, mac, password,
            use_ip_receiver, ip_receiver_addr, ip_receiver_port, ip_receiver_account
        )
        if not success or not conn:
            return False, "Not connected"

        try:
            success, message = await conn.protocol.shock_off(zones)
            return success, message
        except Exception as e:
            logger.error(f"Error turning shock off for device {device_id}: {e}")
            async with self._lock:
                await self._disconnect_device(device_id)
            return False, str(e)

    async def eletrificador_alarm_on(
        self,
        device_id: int,
        mac: str,
        password: str,
        use_ip_receiver: bool = False,
        ip_receiver_addr: str = None,
        ip_receiver_port: int = None,
        ip_receiver_account: str = None
    ) -> Tuple[bool, str]:
        """
        Turn on eletrificador ALARM (arm the alarm function).

        This controls the ALARM function independently from the SHOCK function.

        Args:
            device_id: Device ID
            mac: Device MAC address
            password: Alarm panel password
            use_ip_receiver: Use IP receiver instead of cloud
            ip_receiver_addr: IP receiver server address
            ip_receiver_port: IP receiver server port
            ip_receiver_account: IP receiver account

        Returns:
            Tuple of (success, message)
        """
        success, conn = await self._ensure_connected(
            device_id, mac, password,
            use_ip_receiver, ip_receiver_addr, ip_receiver_port, ip_receiver_account
        )
        if not success or not conn:
            return False, "Not connected"

        try:
            success, message = await conn.protocol.eletrificador_alarm_on()
            return success, message
        except Exception as e:
            logger.error(f"Error turning eletrificador alarm on for device {device_id}: {e}")
            async with self._lock:
                await self._disconnect_device(device_id)
            return False, str(e)

    async def eletrificador_alarm_off(
        self,
        device_id: int,
        mac: str,
        password: str,
        use_ip_receiver: bool = False,
        ip_receiver_addr: str = None,
        ip_receiver_port: int = None,
        ip_receiver_account: str = None
    ) -> Tuple[bool, str]:
        """
        Turn off eletrificador ALARM (disarm the alarm function).

        This controls the ALARM function independently from the SHOCK function.

        Args:
            device_id: Device ID
            mac: Device MAC address
            password: Alarm panel password
            use_ip_receiver: Use IP receiver instead of cloud
            ip_receiver_addr: IP receiver server address
            ip_receiver_port: IP receiver server port
            ip_receiver_account: IP receiver account

        Returns:
            Tuple of (success, message)
        """
        success, conn = await self._ensure_connected(
            device_id, mac, password,
            use_ip_receiver, ip_receiver_addr, ip_receiver_port, ip_receiver_account
        )
        if not success or not conn:
            return False, "Not connected"

        try:
            success, message = await conn.protocol.eletrificador_alarm_off()
            return success, message
        except Exception as e:
            logger.error(f"Error turning eletrificador alarm off for device {device_id}: {e}")
            async with self._lock:
                await self._disconnect_device(device_id)
            return False, str(e)

    def is_connected(self, device_id: int) -> bool:
        """Check if device is connected."""
        if device_id not in self._connections:
            return False
        return self._connections[device_id].protocol.is_authenticated


# Global client instance
isecnet_client = ISECNetClient()
