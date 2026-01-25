"""ISECNet V2 Protocol Implementation for Intelbras Alarm Systems.

This module implements the ISECNet V2 binary protocol used by Intelbras
alarm panels for communication. Based on reverse engineering of the
Guardian Android app.

Protocol flow:
1. Connect to cloud relay (amt8000.intelbras.com.br:9009)
2. Send server connection command
3. Send app connection command with MAC address
4. Authenticate with alarm password
5. Send commands (arm/disarm/status)
"""

import asyncio
import logging
import struct
from dataclasses import dataclass
from enum import IntEnum
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)


class Command(IntEnum):
    """ISECNet V2 command codes."""
    CONNECT = 0x30F6  # 12534
    APP_CONNECT = 0xFFF1  # 65521
    AUTHORIZE = 0xF0F0  # 61680
    KEEP_ALIVE = 0xF0F7  # 61687
    DISCONNECT = 0xF0F1  # 61681
    SYSTEM_ARM_DISARM = 0x401E  # 16414
    ALARM_PANEL_STATUS = 0x0B4A  # 2890
    PANIC_ALARM = 0x401A  # 16410
    TURN_OFF_SIREN = 0x4019  # 16409
    BYPASS_ZONE = 0x401F  # 16415
    GET_MAC = 0x3FAA  # 16298
    PGM_ON_OFF = 0x45AF  # 17839


class ISECNetV2Response(IntEnum):
    """ISECNet V2 response codes from APK."""
    ACK = 0xF0FE   # 61694 - Command accepted
    NACK = 0xF0FD  # 61693 - Command rejected


class AlarmOperation(IntEnum):
    """Alarm arm/disarm operations."""
    SYSTEM_DISARM = 0
    SYSTEM_ARM = 1  # Arm Away (Total)
    ARM_STAY = 2    # Arm Stay (Parcial)
    FORCE_ARM = 3


class ISECNetV1Command(IntEnum):
    """ISECNet V1 command codes (used after IP Receiver connection)."""
    ISEC_PROGRAM = 0xE9  # 233 - SDK_CTRL_LOAD_STORAGE
    GET_PARTIAL_STATUS = 0x5A  # 90
    GET_COMPLETE_STATUS = 0x53  # 83
    GET_COMPLETE_INFO = 0x49  # 73
    ACTIVATE_CENTRAL = 0x41  # 65 = 'A'
    DEACTIVATE_CENTRAL = 0x44  # 68 = 'D'
    PANIC = 0x50  # 80 = 'P'
    SIREN_OFF = 0x4F  # 79 = 'O'
    PGM = 0x47  # 71 = 'G'


class ISECNetServerCommand(IntEnum):
    """Server protocol commands."""
    # Cloud commands
    GET_BYTE = 0x0B
    CONNECT = 0x21
    TOKEN = 0x25
    # IP Receiver commands (from APK CtrlType.java)
    IP_RECEIVER_GET_BYTE = 0xE0  # SDK_CTRL_RAID = 224
    IP_RECEIVER_CONNECT = 0xE4   # SDK_CTRL_ARMED = 228


class AppConnectionResponse(IntEnum):
    """App connection response codes."""
    SUCCESS = 0
    NOT_CONNECTED = 1
    CENTRAL_NOT_FOUND = 2
    CENTRAL_BUSY = 3
    CENTRAL_OFFLINE = 4


class AuthResponse(IntEnum):
    """Authentication response codes."""
    ACCEPTED = 0
    INVALID_PASSWORD = 1
    BLOCKED_USER = 2
    NO_PERMISSION = 3


@dataclass
class AlarmStatus:
    """Alarm panel status."""
    model: Optional[str] = None
    mac: Optional[str] = None
    is_armed: bool = False
    arm_mode: str = "disarmed"  # disarmed, armed_away, armed_stay
    is_triggered: bool = False
    partitions: List[dict] = None
    zones: List[dict] = None
    partitions_enabled: bool = False  # True if device has partitions enabled (for arm/disarm commands)
    # Eletrificador-specific fields
    is_eletrificador: bool = False
    shock_enabled: bool = False  # eletricfierState - fence/shock on/off
    shock_triggered: bool = False  # isEletricfierTriggered - fence triggered
    alarm_enabled: bool = False  # generalState - alarm on/off
    alarm_triggered: bool = False  # isInAlarm - alarm triggered

    def __post_init__(self):
        if self.partitions is None:
            self.partitions = []
        if self.zones is None:
            self.zones = []


class ISECNetProtocol:
    """ISECNet V2 Protocol handler."""

    # Cloud relay server
    AMT_SERVER = "amt8000.intelbras.com.br"
    AMT_PORTS = [9009, 80]
    TIMEOUT = 10  # seconds

    def __init__(self):
        self.reader: Optional[asyncio.StreamReader] = None
        self.writer: Optional[asyncio.StreamWriter] = None
        self.source_id: List[int] = [0, 0]
        self.is_connected = False
        self.is_authenticated = False
        self._lock = asyncio.Lock()
        self._password: Optional[str] = None  # Stored for ISECNet V1 commands
        self._is_ip_receiver: bool = False  # True if using IP Receiver (ISECNet V1)
        self._partitions_enabled: Optional[bool] = None  # True if device has partitions enabled (from status)

    @staticmethod
    def _checksum(data: List[int]) -> int:
        """Calculate XOR checksum for ISECNet V2 packets (with 0xFF inversion)."""
        result = 0
        for byte in data:
            result ^= byte
        return result ^ 0xFF

    @staticmethod
    def _checksum_xor(data: List[int]) -> int:
        """Calculate pure XOR checksum for ISECNet V1 packets (no inversion)."""
        result = 0
        for byte in data:
            result ^= byte
        return result & 0xFF

    @staticmethod
    def _checksum_sum(data: List[int]) -> int:
        """Calculate SUM checksum for IP Receiver handshake packets."""
        result = 0
        for byte in data:
            result += byte
        return result & 0xFF

    @staticmethod
    def _to_two_bytes(value: int) -> List[int]:
        """Convert integer to 2 bytes (big endian)."""
        return [(value >> 8) & 0xFF, value & 0xFF]

    @staticmethod
    def _from_two_bytes(data: List[int]) -> int:
        """Convert 2 bytes to integer (big endian)."""
        return ((data[0] & 0xFF) << 8) | (data[1] & 0xFF)

    def _build_packet(
        self,
        command: Command,
        payload: List[int],
        source_id: Optional[List[int]] = None,
        encrypt_byte: Optional[int] = None
    ) -> bytes:
        """Build ISECNet V2 packet.

        Packet structure:
        [destination 2][source 2][size 2][command 2][payload][checksum 1]
        """
        if source_id is None:
            source_id = self.source_id

        packet = []

        # Destination (always 0, 0)
        packet.extend([0, 0])

        # Source ID
        packet.extend(source_id)

        # Command + payload for size calculation
        cmd_payload = self._to_two_bytes(command) + payload

        # Size (length of command + payload)
        packet.extend(self._to_two_bytes(len(cmd_payload)))

        # Command and payload
        packet.extend(cmd_payload)

        # Checksum
        packet.append(self._checksum(packet))

        # Optional encryption
        if encrypt_byte is not None:
            packet = [b ^ encrypt_byte for b in packet]

        return bytes(packet)

    def _build_server_connection_cmd(self, is_ip_receiver: bool = False) -> bytes:
        """Build server connection command."""
        if is_ip_receiver:
            # IP Receiver: [0x02, 0xE0 (IP_RECEIVER_GET_BYTE), 0x01, checksum]
            # From APK: uses SDKListExtensionsKt.checkSum() which is XOR ^ 0xFF
            packet = [0x02, ISECNetServerCommand.IP_RECEIVER_GET_BYTE, 0x01]
            packet.append(self._checksum(packet))  # XOR ^ 0xFF checksum
            logger.debug(f"IP Receiver GET_BYTE: packet={bytes(packet).hex()}")
            return bytes(packet)
        else:
            # Cloud connection uses standard packet format
            return self._build_packet(Command.CONNECT, [0], [0, 0])

    def _build_app_connection_cmd(
        self,
        mac: str,
        device_id: str,
        byte_value: int,
        is_ip_receiver: bool = False
    ) -> bytes:
        """Build app connection command."""
        if is_ip_receiver:
            # IP Receiver mode: [length, 0xE4, connection_type, account_chars..., checksum]
            # Length = number of bytes after length byte, excluding checksum
            # Format: [len] [cmd] [type] [account...] [checksum]
            # Connection types: 0x45 (69) = ETHERNET, 0x47 (71) = SIM01
            account_bytes = [ord(char) for char in device_id]
            length = 1 + 1 + len(account_bytes)  # cmd + type + account (excluding checksum)

            # Connection type: 0x45 = ETHERNET (69) - used by the official APK
            # The APK always sends 69 (ETHERNET) for IP Receiver connections
            connection_type = 0x45  # ETHERNET - as per APK ISECNetV2ServerProtocol

            packet = [length, ISECNetServerCommand.IP_RECEIVER_CONNECT, connection_type]
            packet.extend(account_bytes)
            # From APK: uses SDKIntExtensionsKt.toFixedInt(SDKListExtensionsKt.checkSum())
            # which is (XOR ^ 0xFF) & 0xFF = XOR ^ 0xFF (since result is already 8-bit)
            packet.append(self._checksum(packet))  # XOR ^ 0xFF checksum
            logger.debug(f"IP Receiver APP_CONNECT: length={length}, account={device_id}, type=0x{connection_type:02X}, packet={bytes(packet).hex()}")
            return bytes(packet)
        else:
            # Cloud mode - use alarm name format
            alarm_name = f"AMT8000-{mac}"
            payload = [ord(c) for c in alarm_name]
            return self._build_packet(Command.APP_CONNECT, payload, [0, 0], byte_value)

    def _build_auth_cmd(self, password: str, is_ip_receiver: bool = False) -> bytes:
        """Build authentication command.

        Password is converted to digits (0-9), with '0' becoming 10.
        For IP Receiver: Uses standard ISECNet V2 format but WITHOUT encryption.
        """
        # Convert password to digit array
        password_digits = []
        for char in password:
            if char == '0':
                password_digits.append(10)
            else:
                password_digits.append(int(char))

        # Pad to 6 digits
        while len(password_digits) < 6:
            password_digits.append(0)

        payload = [0x03]  # Auth type
        payload.extend(password_digits)
        payload.extend(self._to_two_bytes(1))  # Software version

        # Both IP Receiver and Cloud use standard ISECNet V2 format
        # For IP Receiver: sourceID is [0, 0] and NO encryption
        # For Cloud: sourceID is assigned and may have encryption
        if is_ip_receiver:
            # Use standard packet format with sourceID [0, 0], no encryption
            packet = self._build_packet(Command.AUTHORIZE, payload, [0, 0], None)
            logger.debug(f"IP Receiver AUTH (ISECNet V2): packet={packet.hex()}")
            return packet
        else:
            return self._build_packet(Command.AUTHORIZE, payload)

    def _build_status_cmd(self) -> bytes:
        """Build get status command."""
        return self._build_packet(Command.ALARM_PANEL_STATUS, [])

    def _build_arm_cmd(
        self,
        operation: AlarmOperation,
        partition_index: Optional[int] = None
    ) -> bytes:
        """Build arm/disarm command."""
        payload = []

        # Partition index (255 = all partitions, or index+1)
        if partition_index is None:
            payload.append(0xFF)
        else:
            payload.append(partition_index + 1)

        # Operation
        payload.append(operation)

        return self._build_packet(Command.SYSTEM_ARM_DISARM, payload)

    def _build_eletrificador_shock_cmd(self, enable: bool, zones: Optional[List[int]] = None) -> bytes:
        """Build eletrificador shock (fence) on/off command.

        Uses BYPASS_ZONE command (0x401F) with special payload for eletrificador.

        Based on APK assembleEletricfierBypassCommand (ISECNetV2Protocol.java:292-305):
        - Creates array of 8 zeros
        - For each zone in zonesIndex, sets: isActivation ? 1 : 0
        - Adds 0xFF at position 0

        So the logic is:
        - isActivation=true (enable shock) → byte = 1
        - isActivation=false (disable shock) → byte = 0

        Args:
            enable: True to turn shock ON, False to turn OFF
            zones: List of zone indices (0-7) to control. If None, controls all zones.

        Returns:
            Command packet bytes
        """
        # Initialize 8 zone bytes (for 8 possible zones/channels)
        zone_states = [0x00] * 8

        if zones is None:
            # Control all zones
            # From APK: isActivation=true → 1, isActivation=false → 0
            for i in range(8):
                zone_states[i] = 0x01 if enable else 0x00
        else:
            # Control specific zones
            for zone_idx in zones:
                if 0 <= zone_idx < 8:
                    zone_states[zone_idx] = 0x01 if enable else 0x00

        # Build payload: [0xFF marker] + [8 zone state bytes]
        payload = [0xFF] + zone_states

        logger.debug(f"Eletrificador shock cmd: enable={enable}, zones={zones}, payload={bytes(payload).hex()} (0x01=ON, 0x00=OFF)")

        return self._build_packet(Command.BYPASS_ZONE, payload)

    def _build_pgm_cmd(self, pgm_index: int, enable: bool) -> bytes:
        """Build PGM (programmable output) on/off command.

        Uses PGM_ON_OFF command (0x45AF).

        Args:
            pgm_index: PGM output index (0-7)
            enable: True to turn ON, False to turn OFF

        Returns:
            Command packet bytes
        """
        # Payload: [pgm_index, state]
        # state: 0x01 = ON, 0x00 = OFF
        payload = [pgm_index, 0x01 if enable else 0x00]

        logger.debug(f"PGM cmd: index={pgm_index}, enable={enable}, payload={bytes(payload).hex()}")

        return self._build_packet(Command.PGM_ON_OFF, payload)

    def _build_eletrificador_shock_v2_cmd(self, enable: bool) -> bytes:
        """Build eletrificador shock on/off command using ARM/DISARM.

        Some eletrificador models control shock via a special arm/disarm variant.

        This uses partition index 0xFF to indicate shock control specifically.

        Args:
            enable: True to turn shock ON, False to turn OFF

        Returns:
            Command packet bytes
        """
        # Try using ARM/DISARM with special partition 0xFF for shock
        # Operation: 0 = disarm (shock off), 1 = arm (shock on)
        operation = AlarmOperation.SYSTEM_ARM if enable else AlarmOperation.SYSTEM_DISARM

        # Payload: [partition_index, operation]
        # Use 0xFF as partition to indicate shock control
        payload = [0xFF, operation]

        logger.debug(f"Eletrificador shock V2 cmd: enable={enable}, payload={bytes(payload).hex()}")

        return self._build_packet(Command.SYSTEM_ARM_DISARM, payload)

    def _build_get_mac_cmd(self) -> bytes:
        """Build get MAC address command."""
        return self._build_packet(Command.GET_MAC, [0])

    def _build_disconnect_cmd(self) -> bytes:
        """Build disconnect command."""
        return self._build_packet(Command.DISCONNECT, [])

    # ISECNet V1 commands (for IP Receiver)
    def _build_isecv1_cmd(self, command: List[int], password: str) -> bytes:
        """Build ISECNet V1 command packet.

        Format: [size] [ISEC_PROGRAM] [0x21] [password_ascii] [command] [0x21] [checksum]
        This format includes password directly in commands, no separate AUTH.

        From APK ISECNetProtocol.assembleIsec:
        - Uses SDKListExtensionsKt.checkSum() which is XOR ^ 0xFF
        """
        packet = []

        # Size = command length + password length + 3 (ISEC_PROGRAM, 0x21, 0x21)
        size = len(command) + len(password) + 3
        packet.append(size)

        # ISEC_PROGRAM command
        packet.append(ISECNetV1Command.ISEC_PROGRAM)

        # Delimiter
        packet.append(0x21)  # '!'

        # Password as ASCII
        for char in password:
            packet.append(ord(char))

        # Command bytes
        packet.extend(command)

        # End delimiter
        packet.append(0x21)  # '!'

        # Checksum: XOR ^ 0xFF (same as V2, per APK SDKListExtensionsKt.checkSum)
        packet.append(self._checksum(packet))

        logger.debug(f"ISECNet V1 command: {bytes(packet).hex()}")
        return bytes(packet)

    def _build_isecv1_status_cmd(self, password: str) -> bytes:
        """Build ISECNet V1 get partial status command."""
        return self._build_isecv1_cmd([ISECNetV1Command.GET_PARTIAL_STATUS], password)

    def _build_isecv1_arm_cmd(self, password: str, partition_index: Optional[int] = None, stay: bool = False, include_partition: bool = True) -> bytes:
        """Build ISECNet V1 arm command.

        From APK ISECNetProtocol.assembleActivateCentralCommand:
        - Command starts with ACTIVATE_CENTRAL (0x41 = 'A')
        - Partition index: A=0x41, B=0x42, C=0x43, D=0x44
        - Stay mode: append 0x50 ('P')

        Args:
            password: Alarm password
            partition_index: Partition index (0-3), or None for all
            stay: True for stay/partial mode
            include_partition: If False, don't include partition byte (some panels require this)
        """
        command = [ISECNetV1Command.ACTIVATE_CENTRAL]
        if include_partition and partition_index is not None:
            # Partition mapping based on APK CentralPartitions enum:
            # index 0 -> PARTITION_A (0x41='A')
            # index 1 -> PARTITION_B (0x42='B')
            # index 2 -> PARTITION_C (0x43='C')
            # index 3 -> PARTITION_D (0x44='D')
            command.append(0x41 + partition_index)
        if stay:
            command.append(0x50)  # 'P' for stay/parcial mode
        return self._build_isecv1_cmd(command, password)

    def _build_isecv1_disarm_cmd(self, password: str, partition_index: Optional[int] = None) -> bytes:
        """Build ISECNet V1 disarm command.

        From APK ISECNetProtocol.assembleDeactivateCentralCommand:
        - Command starts with DEACTIVATE_CENTRAL (0x44 = 'D')
        - Partition index: A=0x41, B=0x42, C=0x43, D=0x44
        """
        command = [ISECNetV1Command.DEACTIVATE_CENTRAL]
        if partition_index is not None:
            # Partition mapping based on APK CentralPartitions enum
            command.append(0x41 + partition_index)
        return self._build_isecv1_cmd(command, password)

    def _parse_isecv1_response(self, response: bytes) -> Tuple[bool, List[int]]:
        """Parse ISECNet V1 response.

        Response format: [size] [data...] [checksum]
        Checksum is XOR ^ 0xFF of all bytes before checksum.
        Returns (valid, data_bytes)
        """
        if not response or len(response) < 2:
            logger.warning(f"ISECNet V1 response too short: {len(response) if response else 0} bytes")
            return False, []

        size = response[0]
        expected_len = size + 2  # size byte + data bytes + checksum

        if len(response) < expected_len:
            logger.warning(f"ISECNet V1 response too short: expected {expected_len}, got {len(response)}")
            # Try to parse anyway with available data
            if len(response) >= 2:
                return True, list(response[1:])
            return False, []

        # Verify checksum (XOR ^ 0xFF)
        data_for_checksum = list(response[:size + 1])  # size + data (before checksum)
        expected_checksum = self._checksum(data_for_checksum)
        actual_checksum = response[size + 1]

        if expected_checksum != actual_checksum:
            logger.warning(f"ISECNet V1 checksum mismatch: expected 0x{expected_checksum:02X}, got 0x{actual_checksum:02X}")
            # Continue anyway for debugging

        # Extract data (excluding size byte and checksum)
        return True, list(response[1:size + 1])

    def _is_eletrificador_model(self, model_code: int) -> bool:
        """Check if model is an eletrificador (electric fence)."""
        # ELC_6012_NET = 53 (0x35), ELC_6012_IND = 57 (0x39)
        return model_code in [53, 57]

    def _parse_eletrificador_state(self, status_byte: int) -> Tuple[bool, bool]:
        """Parse eletrificador state from status byte.

        Based on APK parseEletricfierState:
        - Bit 0: ACTIVATED (enabled) or DEACTIVATED (disabled)
        - Bit 2: isTriggered

        Returns: (enabled, triggered)
        """
        enabled = bool(status_byte & 0x01)  # Bit 0
        triggered = bool(status_byte & 0x04)  # Bit 2
        return enabled, triggered

    def _parse_eletrificador_alarm_state(self, alarm_byte: int, panic_byte: int) -> Tuple[str, bool]:
        """Parse eletrificador alarm state from status bytes.

        Based on APK parseEletricfierAlarmState:
        - Bit 0 of alarm_byte: armed or not
        - Bit 1 of alarm_byte: stay mode
        - Bit 2 of alarm_byte OR panic_byte == 1: isInAlarm

        Returns: (state_string, is_triggered)
        """
        is_armed = bool(alarm_byte & 0x01)  # Bit 0
        is_stay = bool(alarm_byte & 0x02)   # Bit 1
        is_triggered = bool(alarm_byte & 0x04) or (panic_byte == 1)  # Bit 2 or panic

        if not is_armed:
            state = "disarmed"
        elif is_armed and is_stay:
            state = "armed_stay"
        else:
            state = "armed_away"

        return state, is_triggered

    def _parse_isecv1_status_response(self, response: bytes) -> AlarmStatus:
        """Parse ISECNet V1 partial status response.

        Based on APK ISECNetParserHelper analysis:
        - Full response is 46 bytes (size + 44 data + checksum)
        - After removing size byte, data indices correspond to APK bytes[n+1]

        Key byte positions in raw packet (APK bytes[n]):
        - bytes[0]: size (0x2C = 44)
        - bytes[1]: 0xE9 command echo
        - bytes[2]: response code (0x00 = success)
        - bytes[20]: model code (e.g., 0x34 = AMT_2018_E_SMART)
        - bytes[21]: firmware version
        - bytes[22]: partition enabled (0=single, 1=multiple) OR eletrificador shock state
        - bytes[23]: partition armed status bits OR eletrificador alarm state
        - bytes[25-29]: time (min, hour, day, month, year-2000)
        - bytes[32]: battery level byte
        - bytes[39]: output/PGM/siren status byte

        In our data array (response[1:size+1]), subtract 1 from APK indices:
        - data[19]: model code
        - data[20]: firmware version
        - data[21]: partition enabled OR eletrificador shock state
        - data[22]: partition armed bits OR eletrificador alarm state
        - data[31]: battery level
        - data[38]: output/siren status
        """
        status = AlarmStatus()

        valid, data = self._parse_isecv1_response(response)
        if not valid or len(data) < 10:
            logger.warning(f"ISECNet V1 status response invalid or too short: {len(data)} bytes")
            return status

        logger.debug(f"ISECNet V1 status data ({len(data)} bytes): {bytes(data).hex()}")

        # First byte should be 0xE9 (command echo)
        if data[0] != 0xE9:
            logger.warning(f"ISECNet V1 status response doesn't start with 0xE9: 0x{data[0]:02X}")

        # Response code at data[1] (APK bytes[2])
        if len(data) > 1:
            response_code = data[1]
            if response_code != 0x00:
                logger.warning(f"ISECNet V1 status response code: 0x{response_code:02X}")

        # Check if we have full partial status response (46 bytes total = 44 data bytes)
        if len(data) < 40:
            logger.warning(f"Response too short for partial status parsing: {len(data)} bytes")
            return status

        # Model code at data[19] (APK bytes[20])
        model_code = data[19]
        status.model = self._get_model_name(model_code)
        logger.debug(f"Model code: 0x{model_code:02X} ({model_code}) = {status.model}")

        # Firmware version at data[20] (APK bytes[21])
        firmware_version = data[20]
        logger.debug(f"Firmware version: {firmware_version}")

        # Check if this is an eletrificador (electric fence)
        if self._is_eletrificador_model(model_code):
            status.is_eletrificador = True
            logger.info(f"Detected eletrificador model: {status.model}")

            # For eletrificador, byte layout is different:
            # data[21] = shock status byte (eletricfierState)
            # data[22] = alarm status byte (generalState)
            # data[38] or similar = panic byte
            shock_byte = data[21]
            alarm_byte = data[22]
            panic_byte = data[38] if len(data) > 38 else 0

            logger.debug(f"Eletrificador bytes: shock=0x{shock_byte:02X}, alarm=0x{alarm_byte:02X}, panic=0x{panic_byte:02X}")

            # Parse shock (fence) state
            status.shock_enabled, status.shock_triggered = self._parse_eletrificador_state(shock_byte)
            logger.info(f"Eletrificador SHOCK: enabled={status.shock_enabled}, triggered={status.shock_triggered}")

            # Parse alarm state
            alarm_state, status.alarm_triggered = self._parse_eletrificador_alarm_state(alarm_byte, panic_byte)
            status.alarm_enabled = alarm_state != "disarmed"
            logger.info(f"Eletrificador ALARM: enabled={status.alarm_enabled}, state={alarm_state}, triggered={status.alarm_triggered}")

            # Set overall status based on both shock and alarm
            # If either is triggered, overall is triggered
            status.is_triggered = status.shock_triggered or status.alarm_triggered

            # Overall armed status - if either shock or alarm is enabled
            if status.shock_enabled or status.alarm_enabled:
                status.is_armed = True
                status.arm_mode = alarm_state if status.alarm_enabled else "armed_away"
            else:
                status.is_armed = False
                status.arm_mode = "disarmed"

            logger.info(f"Eletrificador status: shock_enabled={status.shock_enabled}, alarm_enabled={status.alarm_enabled}, "
                       f"shock_triggered={status.shock_triggered}, alarm_triggered={status.alarm_triggered}")

            return status

        # Standard alarm panel parsing (non-eletrificador)
        # Partition enabled at data[21] (APK bytes[22])
        # 0 = single partition mode, 1 = multiple partitions enabled
        partition_enabled = data[21]
        status.partitions_enabled = bool(partition_enabled)
        self._partitions_enabled = bool(partition_enabled)  # Cache for arm/disarm commands
        logger.debug(f"Partition enabled: {partition_enabled}")

        # Partition armed status bits at data[22] (APK bytes[23])
        # Each bit represents a partition: bit 0 = partition A, bit 1 = partition B, etc.
        partition_status_byte = data[22]
        logger.debug(f"Partition status byte: 0x{partition_status_byte:02X} (binary: {bin(partition_status_byte)})")

        # Parse partitions
        partitions = []
        num_partitions = self._get_max_partitions_for_model(model_code)

        # APK parsePartitions: converts byte to boolean list where each bit = partition status
        # For most models, bits 0-1 from first byte, bits 0-1 from second byte (if 2-byte status)
        # Bit interpretation (from APK analysis):
        #   - Even bit (0,2,4...) = partition armed
        #   - Odd bit (1,3,5...) = armed in TOTAL mode (all zones)
        # So: armed=1, total=0 → armed_stay (partial/perimeter only)
        #     armed=1, total=1 → armed_away (all zones)
        for i in range(num_partitions):
            is_armed = bool(partition_status_byte & (1 << (i * 2)))  # Even bits for armed
            is_total = bool(partition_status_byte & (1 << (i * 2 + 1)))  # Odd bits for total mode

            if is_armed and is_total:
                state = "armed_away"  # Armed total (all zones)
            elif is_armed:
                state = "armed_stay"  # Armed partial (perimeter only)
            else:
                state = "disarmed"

            partitions.append({
                "index": i,
                "state": state,
                "armed": is_armed,
                "total": is_total
            })
            logger.debug(f"Partition {i}: armed={is_armed}, total={is_total}, state={state}")

        # If no partitions detected from bits, check if single partition mode
        if not any(p["armed"] for p in partitions) and partition_enabled == 0:
            # Single partition mode - check if overall system is armed
            # In this case partition_status_byte may be 0 even when armed
            # Check additional status indicators
            pass

        status.partitions = partitions

        # Determine overall status
        if partitions:
            # Find if any partition is armed
            armed_away = any(p["state"] == "armed_away" for p in partitions)
            armed_stay = any(p["state"] == "armed_stay" for p in partitions)

            # Prioritize armed_away (total) over armed_stay (partial)
            if armed_away:
                status.arm_mode = "armed_away"
                status.is_armed = True
            elif armed_stay:
                status.arm_mode = "armed_stay"
                status.is_armed = True
            else:
                status.arm_mode = "disarmed"
                status.is_armed = False

        # Battery level at data[31] (APK bytes[32])
        battery_byte = data[31]
        logger.debug(f"Battery byte: 0x{battery_byte:02X}")

        # Output/Siren status at data[38] (APK bytes[39])
        # This byte contains siren and PGM status bits
        output_byte = data[38]
        logger.debug(f"Output/siren byte: 0x{output_byte:02X}")

        # Siren status is typically in the output byte
        # Exact bit position depends on model
        siren_active = bool(output_byte & 0x80)  # Bit 7 often indicates siren
        status.is_triggered = siren_active
        if siren_active:
            logger.info("Siren is ACTIVE")

        # Parse zone/sector status
        # Zone status bytes are at different positions depending on model
        # For most models: data[1:7] (APK bytes[2:8]) = zone open status
        zones = []
        zone_bytes_start = 1  # After command echo
        zone_bytes_count = 6  # Typical for 48-zone models

        if len(data) > zone_bytes_start + zone_bytes_count:
            for byte_idx in range(zone_bytes_count):
                zone_byte = data[zone_bytes_start + byte_idx]
                for bit_idx in range(8):
                    zone_num = byte_idx * 8 + bit_idx
                    is_open = bool(zone_byte & (1 << bit_idx))
                    zones.append({
                        "index": zone_num,
                        "triggered": False,
                        "open": is_open,
                        "state": "open" if is_open else "closed"
                    })

        status.zones = zones

        logger.info(f"Parsed status: model={status.model}, armed={status.is_armed}, "
                   f"mode={status.arm_mode}, triggered={status.is_triggered}")

        return status

    def _get_max_partitions_for_model(self, model_code: int) -> int:
        """Get maximum partition count for a given model code."""
        # Based on APK AlarmModel.getPartitionMaxCount()
        partition_counts = {
            65: 4,    # AMT_4010
            36: 0,    # ANM_24_NET
            37: 0,    # ANM_24_NET_G2
            1: 16,    # AMT_8000
            2: 16,    # AMT_8000_LITE
            3: 16,    # AMT_8000_PRO
            144: 8,   # AMT_9000
            54: 0,    # AMT_1000_SMART
        }
        # Default: 2 partitions for most models
        return partition_counts.get(model_code, 2)

    def _parse_isecv1_command_response(self, response: bytes) -> Tuple[bool, str]:
        """Parse ISECNet V1 command response (arm/disarm).

        Response format from APK analysis:
        - 46 bytes = partial status response = success
        - 96+ bytes = complete status response = success
        - Otherwise check bytes[2] for ISECNetResponse code

        ISECNetResponse codes (from APK):
        - 254 (0xFE) = SUCCESS
        - 224 (0xE0) = INVALID_PACKAGE
        - 225 (0xE1) = INCORRECT_PASSWORD
        - 226 (0xE2) = INVALID_COMMAND
        - 227 (0xE3) = CENTRAL_DOES_NOT_HAVE_PARTITIONS
        - 228 (0xE4) = OPEN_ZONES
        - 229 (0xE5) = COMMAND_DEPRECATED
        - 255 (0xFF) = INVALID_MODEL
        - 230 (0xE6) = BYPASS_DENIED
        - 231 (0xE7) = DEACTIVATION_DENIED
        - 232 (0xE8) = BYPASS_CENTRAL_ACTIVATED
        - 0 (0x00) = UNKNOWN_ERROR
        """
        if not response or len(response) < 2:
            return False, "No response"

        logger.debug(f"ISECNet V1 command response ({len(response)} bytes): {response.hex()}")

        # 46-byte response = partial status response = success
        if len(response) == 46:
            logger.debug("46-byte response (partial status) - treating as success")
            return True, "OK"

        # 96+ bytes = complete status response = success
        if len(response) >= 96:
            logger.debug(f"{len(response)}-byte response (complete status) - treating as success")
            return True, "OK"

        # For shorter responses, check the response code at bytes[2]
        if len(response) >= 3:
            response_code = response[2]

            # ISECNetResponse code mapping
            response_messages = {
                254: ("OK", True),              # SUCCESS
                0: ("Unknown error", False),    # UNKNOWN_ERROR
                224: ("Invalid package", False),       # INVALID_PACKAGE
                225: ("Incorrect password", False),    # INCORRECT_PASSWORD
                226: ("Invalid command", False),       # INVALID_COMMAND
                227: ("No partitions", False),         # CENTRAL_DOES_NOT_HAVE_PARTITIONS
                228: ("Open zones", False),            # OPEN_ZONES
                229: ("Command deprecated", False),    # COMMAND_DEPRECATED
                255: ("Invalid model", False),         # INVALID_MODEL
                230: ("Bypass denied", False),         # BYPASS_DENIED
                231: ("Deactivation denied", False),   # DEACTIVATION_DENIED
                232: ("Bypass - central activated", False),  # BYPASS_CENTRAL_ACTIVATED
            }

            if response_code in response_messages:
                message, success = response_messages[response_code]
                if not success:
                    logger.warning(f"ISECNet V1 command failed: {message} (0x{response_code:02X})")
                return success, message
            else:
                logger.warning(f"Unknown ISECNet V1 response code: 0x{response_code:02X}")
                # If it's not a known error code and we have data, assume success
                return True, "OK"

        return False, "Invalid response"

    async def _send_and_receive(
        self,
        data: bytes,
        timeout: float = 10.0,
        retries: int = 0,
        retry_delay: float = 1.0
    ) -> Optional[bytes]:
        """Send data and receive response with optional retry."""
        if not self.writer:
            logger.error("Not connected")
            return None

        for attempt in range(retries + 1):
            try:
                if attempt > 0:
                    logger.info(f"Retry attempt {attempt}/{retries} after {retry_delay}s delay")
                    await asyncio.sleep(retry_delay)

                logger.debug(f"Sending: {data.hex()}")
                self.writer.write(data)
                await self.writer.drain()

                # Read response (max 1024 bytes)
                response = await asyncio.wait_for(
                    self.reader.read(1024),
                    timeout=timeout
                )
                logger.debug(f"Received: {response.hex() if response else 'empty'}")
                return response

            except asyncio.TimeoutError:
                logger.error(f"Timeout waiting for response (attempt {attempt + 1}/{retries + 1})")
                if attempt < retries:
                    continue
                return None
            except Exception as e:
                logger.error(f"Error sending/receiving: {e}")
                return None

        return None

    def _parse_source_id(self, response: bytes) -> List[int]:
        """Parse source ID from response."""
        if len(response) >= 11:
            return [response[9], response[10]]
        return [0, 0]

    def _parse_byte_response(self, response: bytes, is_ip_receiver: bool) -> Optional[int]:
        """Parse byte value from server connection response."""
        if is_ip_receiver:
            # IP Receiver: response[2] == 1 means success
            if len(response) >= 3 and response[2] == 0x01:
                return 0x01  # Success indicator
            logger.warning(f"IP Receiver GET_BYTE failed: response={response.hex() if response else 'empty'}")
            return None
        else:
            # Cloud: response[8] contains the XOR byte value
            if len(response) >= 9:
                return response[8]
            return None

    def _parse_app_connection_response(
        self,
        response: bytes,
        is_ip_receiver: bool
    ) -> AppConnectionResponse:
        """Parse app connection response."""
        logger.debug(f"Parsing APP_CONNECT response: {response.hex() if response else 'empty'}, is_ip_receiver={is_ip_receiver}")
        if is_ip_receiver:
            # IP Receiver: response[2] contains result code
            # From APK ISECNetV2ServerProtocol.parseAppConnectionResponse:
            # response[2] == 1 means SUCCESS (maps to enum 0)
            # response[2] != 1 means failure (maps to enum 1)
            if len(response) >= 3:
                result_code = response[2]
                logger.debug(f"IP Receiver APP_CONNECT response code: {result_code}")
                if result_code == 0x01:
                    # Success - device responded with 1
                    return AppConnectionResponse.SUCCESS
                elif result_code == 0x00:
                    # Failure - device responded with 0
                    return AppConnectionResponse.NOT_CONNECTED
                else:
                    logger.warning(f"IP Receiver APP_CONNECT unknown response code: {result_code} (0x{result_code:02X})")
                    # Treat unknown as failure
                    return AppConnectionResponse.NOT_CONNECTED
            logger.warning(f"IP Receiver APP_CONNECT response too short: {len(response)} bytes")
        else:
            # Cloud: response[8] contains the result code
            if len(response) >= 9:
                return AppConnectionResponse(response[8])
        return AppConnectionResponse.NOT_CONNECTED

    def _parse_auth_response(self, response: bytes) -> Optional[AuthResponse]:
        """Parse authentication response."""
        if len(response) >= 9:
            return AuthResponse(response[8])
        return None

    def _parse_command_response(self, response: bytes) -> Tuple[bool, int]:
        """Check if command response is success.

        Based on APK ISECNetV2Protocol.isCommandResponseSuccess():
        - Checks if response command equals NACK (0xF0FD = 61693)
        - If NACK, parses error code from response[8]
        - Otherwise, command is considered successful

        Returns (success, command_code).
        """
        if len(response) < 8:
            logger.warning(f"Response too short: {len(response)} bytes")
            return False, 0

        # Extract command from response (bytes 6-7, big endian)
        cmd = self._from_two_bytes([response[6], response[7]])
        logger.debug(f"Response command: 0x{cmd:04X} ({cmd})")

        # Check for NACK response (0xF0FD = 61693)
        if cmd == ISECNetV2Response.NACK:
            error_code = response[8] if len(response) > 8 else 0
            logger.warning(f"NACK response received, error_code={error_code}")
            return False, error_code

        # Check for ACK response (0xF0FE = 61694)
        if cmd == ISECNetV2Response.ACK:
            logger.debug("ACK response received")
            return True, cmd

        # Any other response (like status packet 0x0B4A) is also considered success
        # This matches APK behavior where non-NACK responses are success
        logger.debug(f"Non-NACK response received: 0x{cmd:04X}")
        return True, cmd

    def _parse_status_response(self, response: bytes) -> AlarmStatus:
        """Parse alarm status from response (V2 protocol - Cloud).

        Status response structure (144+ bytes):
        - Bytes 8-9: Model code
        - Bytes 10-...: Partition and zone status

        For Eletrificador (V2):
        - Byte 30: Shock/fence status (bit 0 = enabled, bit 2 = triggered)
        - Byte 31: Alarm status (bit 0 = armed, bit 1 = stay, bit 2 = triggered)
        - Byte 70: Panic byte (approx)
        """
        status = AlarmStatus()

        if len(response) < 32:
            logger.warning(f"Status response too short: {len(response)} bytes")
            return status

        # Parse model (byte 8 for single-byte model code)
        model_code = response[8]
        status.model = self._get_model_name(model_code)
        logger.debug(f"V2 status - Model code: 0x{model_code:02X} ({model_code}) = {status.model}")

        # Check if this is an eletrificador (electric fence)
        if self._is_eletrificador_model(model_code):
            status.is_eletrificador = True
            logger.info(f"V2: Detected eletrificador model: {status.model}")

            # For eletrificador via Cloud (V2), byte layout:
            # response[30] = shock status byte (eletricfierState)
            # response[31] = alarm status byte (generalState)
            # response[70] = panic byte (if available)
            if len(response) > 31:
                shock_byte = response[30]
                alarm_byte = response[31]
                panic_byte = response[70] if len(response) > 70 else 0

                logger.debug(f"V2 Eletrificador bytes: shock=0x{shock_byte:02X}, alarm=0x{alarm_byte:02X}, panic=0x{panic_byte:02X}")

                # Parse shock (fence) state
                status.shock_enabled, status.shock_triggered = self._parse_eletrificador_state(shock_byte)
                logger.info(f"V2 Eletrificador SHOCK: enabled={status.shock_enabled}, triggered={status.shock_triggered}")

                # Parse alarm state
                alarm_state, status.alarm_triggered = self._parse_eletrificador_alarm_state(alarm_byte, panic_byte)
                status.alarm_enabled = alarm_state != "disarmed"
                logger.info(f"V2 Eletrificador ALARM: enabled={status.alarm_enabled}, state={alarm_state}, triggered={status.alarm_triggered}")

                # Set overall status based on alarm state
                status.arm_mode = alarm_state
                status.is_armed = status.alarm_enabled
                status.is_triggered = status.shock_triggered or status.alarm_triggered
            else:
                logger.warning(f"V2 response too short for eletrificador parsing: {len(response)} bytes")

            return status

        # Standard alarm panel parsing (non-eletrificador)
        if len(response) < 144:
            logger.warning(f"Status response too short for alarm panel: {len(response)} bytes")
            return status

        # Parse partition states (simplified)
        # Byte 10: Partition 1 state
        # Byte 11: Partition 2 state
        # etc.

        partition_states = []
        for i in range(4):  # Max 4 partitions
            if 10 + i < len(response):
                state = response[10 + i]
                partition_states.append({
                    "index": i,
                    "state": self._parse_partition_state(state)
                })

        status.partitions = partition_states

        # Determine overall arm state from first partition
        if partition_states:
            first_state = partition_states[0]["state"]
            status.arm_mode = first_state
            status.is_armed = first_state in ["armed_away", "armed_stay"]

        # Check for alarm triggered (byte 14+ typically)
        if len(response) > 14:
            # Simplified: check if any alarm flag is set
            status.is_triggered = response[14] != 0

        return status

    def _parse_partition_state(self, state_byte: int) -> str:
        """Parse partition state byte."""
        # States based on APK analysis
        if state_byte == 0:
            return "disarmed"
        elif state_byte == 1:
            return "armed_away"
        elif state_byte == 2:
            return "armed_stay"
        elif state_byte == 3:
            return "triggered"
        else:
            return "unknown"

    def _get_model_name(self, model_code: int) -> str:
        """Get model name from code.

        Based on APK AlarmModel.java hexValue mapping:
        - Single byte model codes used in ISECNet V1 protocol
        """
        models = {
            # From APK AlarmModel enum
            30: "AMT_2018_E_EG",      # 0x1E
            49: "AMT_2016_E3G",       # 0x31
            50: "AMT_2018_E3G",       # 0x32
            65: "AMT_4010",           # 0x41
            97: "AMT_1016_NET",       # 0x61
            46: "AMT_2118_EG",        # 0x2E
            36: "ANM_24_NET",         # 0x24
            37: "ANM_24_NET_G2",      # 0x25
            1:  "AMT_8000",           # 0x01
            3:  "AMT_8000_PRO",       # 0x03
            2:  "AMT_8000_LITE",      # 0x02
            52: "AMT_2018_E_SMART",   # 0x34
            54: "AMT_1000_SMART",     # 0x36
            53: "ELC_6012_NET",       # 0x35
            57: "ELC_6012_IND",       # 0x39
            144: "AMT_9000",          # 0x90
        }
        return models.get(model_code, f"UNKNOWN_0x{model_code:02X}")

    async def connect(
        self,
        mac: str,
        password: str,
        device_id: Optional[str] = None,
        ip_receiver_address: Optional[str] = None,
        ip_receiver_port: Optional[int] = None
    ) -> Tuple[bool, str]:
        """Connect to alarm panel via cloud relay or IP receiver.

        Args:
            mac: Alarm panel MAC address
            password: Alarm panel password (6 digits)
            device_id: Device ID from cloud API
            ip_receiver_address: Direct IP receiver address (optional)
            ip_receiver_port: Direct IP receiver port (optional)

        Returns:
            Tuple of (success, message)
        """
        async with self._lock:
            try:
                is_ip_receiver = ip_receiver_address is not None

                # Determine server and port
                if is_ip_receiver:
                    server = ip_receiver_address
                    ports = [ip_receiver_port or 9009]
                else:
                    server = self.AMT_SERVER
                    ports = self.AMT_PORTS

                # Try connecting to each port
                connected = False
                for port in ports:
                    try:
                        logger.info(f"Connecting to {server}:{port}")
                        self.reader, self.writer = await asyncio.wait_for(
                            asyncio.open_connection(server, port),
                            timeout=self.TIMEOUT
                        )
                        connected = True
                        logger.info(f"Socket connected to {server}:{port}")
                        break
                    except Exception as e:
                        logger.warning(f"Failed to connect to {server}:{port}: {e}")
                        continue

                if not connected:
                    return False, "Failed to connect to all ports"

                # Step 1: Server connection
                logger.info("Sending server connection command")
                cmd = self._build_server_connection_cmd(is_ip_receiver)
                response = await self._send_and_receive(cmd)

                if not response:
                    return False, "No response to server connection"

                byte_value = self._parse_byte_response(response, is_ip_receiver)
                if byte_value is None:
                    return False, "Failed to parse server connection response"

                logger.info(f"Server connection successful, byte={byte_value}")

                # Step 2: App connection
                # After IP Receiver handshake, use standard ISECNet protocol
                logger.info("Sending app connection command")
                device_id = device_id or mac

                # Use appropriate APP_CONNECT format
                cmd = self._build_app_connection_cmd(mac, device_id, byte_value, is_ip_receiver)

                response = await self._send_and_receive(cmd)

                if not response:
                    return False, "No response to app connection"

                # Parse response based on connection type
                app_response = self._parse_app_connection_response(response, is_ip_receiver)
                if app_response != AppConnectionResponse.SUCCESS:
                    error_messages = {
                        AppConnectionResponse.NOT_CONNECTED: "Not connected",
                        AppConnectionResponse.CENTRAL_NOT_FOUND: "Central not found",
                        AppConnectionResponse.CENTRAL_BUSY: "Central is busy",
                        AppConnectionResponse.CENTRAL_OFFLINE: "Central is offline"
                    }
                    return False, error_messages.get(app_response, f"App connection failed: {app_response}")

                # Extract source ID
                self.source_id = self._parse_source_id(response)
                logger.info(f"App connection successful, sourceID={self.source_id}")

                self.is_connected = True
                self._is_ip_receiver = is_ip_receiver
                self._password = password  # Store for V1 commands

                if is_ip_receiver:
                    # IP Receiver mode: The panel likely uses ISECNet V1 protocol
                    # V1 doesn't have a separate AUTH command - password is embedded in each command
                    # The IP Receiver handshake (GET_BYTE + APP_CONNECT) establishes the tunnel
                    # From here, we send V1 commands with embedded password
                    logger.info("IP Receiver: Tunnel established, using ISECNet V1 protocol (no separate AUTH)")
                    self.is_authenticated = True
                    return True, "Connected via IP Receiver (ISECNet V1 mode)"

                # Cloud mode: Step 3: Authenticate with ISECNet V2
                logger.info("Sending authentication command (ISECNet V2)")
                cmd = self._build_auth_cmd(password, is_ip_receiver)
                response = await self._send_and_receive(cmd)

                if not response:
                    return False, "No response to authentication"

                logger.debug(f"Auth response: {response.hex()}")

                # Check response
                success, error_code = self._parse_command_response(response)
                if not success:
                    logger.warning(f"AUTH command failed with error code: {error_code}")
                    return False, f"Authentication command failed (NACK): error={error_code}"

                auth_response = self._parse_auth_response(response)
                if auth_response is None:
                    logger.warning(f"Could not parse auth response from: {response.hex()}")
                    return False, "Could not parse authentication response"

                if auth_response != AuthResponse.ACCEPTED:
                    error_messages = {
                        AuthResponse.INVALID_PASSWORD: "Invalid password",
                        AuthResponse.BLOCKED_USER: "User is blocked",
                        AuthResponse.NO_PERMISSION: "No permission"
                    }
                    return False, error_messages.get(auth_response, f"Authentication failed: {auth_response}")

                self.is_authenticated = True
                logger.info("Authentication successful")

                return True, "Connected and authenticated"

            except Exception as e:
                logger.error(f"Connection error: {e}")
                await self.disconnect()
                return False, str(e)

    async def disconnect(self):
        """Disconnect from alarm panel."""
        async with self._lock:
            try:
                if self.writer:
                    # Send disconnect command
                    if self.is_connected:
                        cmd = self._build_disconnect_cmd()
                        try:
                            self.writer.write(cmd)
                            await self.writer.drain()
                        except:
                            pass

                    self.writer.close()
                    await self.writer.wait_closed()
            except:
                pass
            finally:
                self.reader = None
                self.writer = None
                self.is_connected = False
                self.is_authenticated = False
                self.source_id = [0, 0]

    async def get_status(self) -> Tuple[bool, AlarmStatus]:
        """Get alarm panel status.

        Returns:
            Tuple of (success, AlarmStatus)
        """
        async with self._lock:
            if not self.is_authenticated:
                return False, AlarmStatus()

            try:
                if self._is_ip_receiver:
                    # ISECNet V1 mode - command includes password
                    logger.debug("Getting status using ISECNet V1 (IP Receiver mode)")
                    cmd = self._build_isecv1_status_cmd(self._password)
                    response = await self._send_and_receive(cmd)

                    if not response:
                        return False, AlarmStatus()

                    logger.debug(f"V1 Status response ({len(response)} bytes): {response.hex()}")
                    status = self._parse_isecv1_status_response(response)
                    return True, status
                else:
                    # ISECNet V2 mode (Cloud)
                    logger.debug("Getting status using ISECNet V2")
                    cmd = self._build_status_cmd()
                    response = await self._send_and_receive(cmd)

                    if not response:
                        return False, AlarmStatus()

                    logger.debug(f"V2 Status response ({len(response)} bytes): {response.hex()}")

                    success, error_code = self._parse_command_response(response)
                    if not success:
                        logger.warning(f"Status command failed: error_code={error_code}")
                        return False, AlarmStatus()

                    status = self._parse_status_response(response)
                    return True, status

            except Exception as e:
                logger.error(f"Error getting status: {e}")
                return False, AlarmStatus()

    async def arm(
        self,
        mode: str = "away",
        partition_index: Optional[int] = None,
        partitions_enabled: Optional[bool] = None
    ) -> Tuple[bool, str]:
        """Arm the alarm.

        Args:
            mode: "away" for total arm, "stay" for partial arm
            partition_index: Specific partition (None = all)
            partitions_enabled: If provided, use this value to decide whether to include
                               partition byte. If None, use self._partitions_enabled (from status).

        Returns:
            Tuple of (success, message)
        """
        async with self._lock:
            if not self.is_authenticated:
                return False, "Not authenticated"

            try:
                # Use provided partitions_enabled, fall back to instance cache
                effective_partitions_enabled = partitions_enabled if partitions_enabled is not None else self._partitions_enabled

                if self._is_ip_receiver:
                    # ISECNet V1 mode - command includes password
                    stay = (mode == "stay")

                    # Only include partition byte if device has partitions enabled
                    effective_partition = partition_index
                    if effective_partitions_enabled is False:
                        logger.debug(f"Device has partitions disabled (cached), skipping partition byte")
                        effective_partition = None

                    logger.debug(f"Arming using ISECNet V1 (IP Receiver mode), mode={mode}, partition={effective_partition}, partitions_enabled={effective_partitions_enabled}")
                    cmd = self._build_isecv1_arm_cmd(self._password, effective_partition, stay)

                    # ARM command behavior: Unlike DISARM, the panel may not respond immediately
                    # due to exit delay or zone checking. Use a shorter timeout and treat
                    # timeout as potential success (verify with status later).
                    response = await self._send_and_receive(cmd, timeout=3.0)

                    if not response:
                        # No response to ARM is common - panel may be processing exit delay
                        # This is NOT necessarily a failure - we'll verify with status check
                        logger.info("No immediate response to ARM command (may be processing exit delay)")
                        return True, f"Armed ({mode}) - command sent"

                    logger.debug(f"V1 Arm response: {response.hex()}")
                    success, message = self._parse_isecv1_command_response(response)

                    # Fallback: if we got "No partitions" error and we sent a partition byte,
                    # retry WITHOUT the partition byte (in case partitions_enabled was not known)
                    if not success and "No partitions" in message and effective_partition is not None:
                        logger.info(f"Device doesn't have partitions enabled, retrying without partition byte")
                        self._partitions_enabled = False  # Update instance cache
                        cmd = self._build_isecv1_arm_cmd(self._password, None, stay)
                        response = await self._send_and_receive(cmd, timeout=3.0)

                        if not response:
                            # Same as above - no response may mean command is being processed
                            logger.info("No immediate response to ARM retry (may be processing exit delay)")
                            return True, f"Armed ({mode}) - command sent"

                        logger.debug(f"V1 Arm response (retry): {response.hex()}")
                        success, message = self._parse_isecv1_command_response(response)

                    if success:
                        return True, f"Armed ({mode})"
                    else:
                        return False, f"Arm command failed: {message}"
                else:
                    # ISECNet V2 mode (Cloud)
                    logger.debug(f"Arming using ISECNet V2, mode={mode}, partition={partition_index}")
                    operation = AlarmOperation.SYSTEM_ARM if mode == "away" else AlarmOperation.ARM_STAY
                    cmd = self._build_arm_cmd(operation, partition_index)
                    response = await self._send_and_receive(cmd)

                    if not response:
                        return False, "No response"

                    logger.debug(f"V2 Arm response: {response.hex()}")

                    success, error_code = self._parse_command_response(response)
                    if not success:
                        error_messages = {
                            1: "Zone open",
                            2: "Battery low",
                            3: "No permission"
                        }
                        return False, error_messages.get(error_code, f"Arm failed: {error_code}")

                    return True, f"Armed ({mode})"

            except Exception as e:
                logger.error(f"Error arming: {e}")
                return False, str(e)

    async def disarm(
        self,
        partition_index: Optional[int] = None,
        partitions_enabled: Optional[bool] = None
    ) -> Tuple[bool, str]:
        """Disarm the alarm.

        Args:
            partition_index: Specific partition (None = all)
            partitions_enabled: If provided, use this value to decide whether to include
                               partition byte. If None, use self._partitions_enabled (from status).

        Returns:
            Tuple of (success, message)
        """
        async with self._lock:
            if not self.is_authenticated:
                return False, "Not authenticated"

            try:
                # Use provided partitions_enabled, fall back to instance cache
                effective_partitions_enabled = partitions_enabled if partitions_enabled is not None else self._partitions_enabled

                if self._is_ip_receiver:
                    # ISECNet V1 mode - command includes password
                    # Only include partition byte if device has partitions enabled
                    effective_partition = partition_index
                    if effective_partitions_enabled is False:
                        logger.debug(f"Device has partitions disabled (cached), skipping partition byte")
                        effective_partition = None

                    logger.debug(f"Disarming using ISECNet V1 (IP Receiver mode), partition={effective_partition}, partitions_enabled={effective_partitions_enabled}")
                    cmd = self._build_isecv1_disarm_cmd(self._password, effective_partition)
                    response = await self._send_and_receive(cmd)

                    if not response:
                        return False, "No response"

                    logger.debug(f"V1 Disarm response: {response.hex()}")
                    success, message = self._parse_isecv1_command_response(response)

                    # Fallback: if we got "No partitions" error and we sent a partition byte,
                    # retry WITHOUT the partition byte (in case partitions_enabled was not known)
                    if not success and "No partitions" in message and effective_partition is not None:
                        logger.info(f"Device doesn't have partitions enabled, retrying without partition byte")
                        self._partitions_enabled = False  # Update instance cache
                        cmd = self._build_isecv1_disarm_cmd(self._password, None)
                        response = await self._send_and_receive(cmd)

                        if not response:
                            return False, "No response on retry"

                        logger.debug(f"V1 Disarm response (retry): {response.hex()}")
                        success, message = self._parse_isecv1_command_response(response)

                    if success:
                        return True, "Disarmed"
                    else:
                        return False, f"Disarm command failed: {message}"
                else:
                    # ISECNet V2 mode (Cloud)
                    logger.debug(f"Disarming using ISECNet V2, partition={partition_index}")
                    cmd = self._build_arm_cmd(AlarmOperation.SYSTEM_DISARM, partition_index)
                    response = await self._send_and_receive(cmd)

                    if not response:
                        return False, "No response"

                    logger.debug(f"V2 Disarm response: {response.hex()}")

                    success, error_code = self._parse_command_response(response)
                    if not success:
                        return False, f"Disarm failed: {error_code}"

                    return True, "Disarmed"

            except Exception as e:
                logger.error(f"Error disarming: {e}")
                return False, str(e)

    async def shock_on(self, zones: Optional[List[int]] = None) -> Tuple[bool, str]:
        """Turn on eletrificador shock (fence).

        This controls the shock/fence function independently from the alarm.

        Based on APK ISECNetV2SDK.java:910-919 activateEletricfier():
        - Uses SYSTEM_ARM_DISARM command (0x401E)
        - With operation SYSTEM_ARM
        - With partition_index=1 (becomes 2 in payload due to +1 encoding)

        Args:
            zones: Not used - shock is controlled by partition, not zones.

        Returns:
            Tuple of (success, message)
        """
        async with self._lock:
            if not self.is_authenticated:
                return False, "Not authenticated"

            try:
                logger.info(f"Turning shock ON using SYSTEM_ARM with partition_index=1")

                # APK uses: assembleSystemArmCentralCommand(sourceID, SYSTEM_ARM, 1)
                # partition_index=1 → payload byte = 2 (due to +1 encoding)
                cmd = self._build_arm_cmd(AlarmOperation.SYSTEM_ARM, partition_index=1)
                logger.debug(f"Shock ON command: {cmd.hex()}")
                response = await self._send_and_receive(cmd)

                if not response:
                    return False, "No response"

                logger.debug(f"Shock ON response ({len(response)} bytes): {response.hex()}")

                success, error_code = self._parse_command_response(response)
                if not success:
                    return False, f"Shock ON failed: error_code={error_code}"

                return True, "Shock turned ON"

            except Exception as e:
                logger.error(f"Error turning shock on: {e}")
                return False, str(e)

    async def shock_off(self, zones: Optional[List[int]] = None) -> Tuple[bool, str]:
        """Turn off eletrificador shock (fence).

        This controls the shock/fence function independently from the alarm.

        Based on APK ISECNetV2SDK.java:922-931 deactivateEletricfier():
        - Uses SYSTEM_ARM_DISARM command (0x401E)
        - With operation SYSTEM_DISARM
        - With partition_index=1 (becomes 2 in payload due to +1 encoding)

        Args:
            zones: Not used - shock is controlled by partition, not zones.

        Returns:
            Tuple of (success, message)
        """
        async with self._lock:
            if not self.is_authenticated:
                return False, "Not authenticated"

            try:
                logger.info(f"Turning shock OFF using SYSTEM_DISARM with partition_index=1")

                # APK uses: assembleSystemArmCentralCommand(sourceID, SYSTEM_DISARM, 1)
                # partition_index=1 → payload byte = 2 (due to +1 encoding)
                cmd = self._build_arm_cmd(AlarmOperation.SYSTEM_DISARM, partition_index=1)
                logger.debug(f"Shock OFF command: {cmd.hex()}")
                response = await self._send_and_receive(cmd)

                if not response:
                    return False, "No response"

                logger.debug(f"Shock OFF response ({len(response)} bytes): {response.hex()}")

                success, error_code = self._parse_command_response(response)
                if not success:
                    return False, f"Shock OFF failed: error_code={error_code}"

                return True, "Shock turned OFF"

            except Exception as e:
                logger.error(f"Error turning shock off: {e}")
                return False, str(e)

    async def eletrificador_alarm_on(self) -> Tuple[bool, str]:
        """Turn on eletrificador ALARM (arm the alarm function).

        This controls the ALARM function independently from the SHOCK function.

        Based on APK ISECNetV2SDK.java activateCentral() with isEletricfier=true:
        - Uses SYSTEM_ARM_DISARM command (0x401E)
        - With operation SYSTEM_ARM
        - With partition_index=0 (becomes 1 in payload due to +1 encoding)

        Returns:
            Tuple of (success, message)
        """
        async with self._lock:
            if not self.is_authenticated:
                return False, "Not authenticated"

            try:
                logger.info(f"Turning eletrificador ALARM ON using SYSTEM_ARM with partition_index=0")

                # APK uses: assembleSystemArmCentralCommand(sourceID, SYSTEM_ARM, 0)
                # For eletrificador, partitionIndex is forced to 0
                # partition_index=0 → payload byte = 1 (due to +1 encoding)
                cmd = self._build_arm_cmd(AlarmOperation.SYSTEM_ARM, partition_index=0)
                logger.debug(f"Eletrificador ALARM ON command: {cmd.hex()}")
                response = await self._send_and_receive(cmd)

                if not response:
                    return False, "No response"

                logger.debug(f"Eletrificador ALARM ON response ({len(response)} bytes): {response.hex()}")

                success, error_code = self._parse_command_response(response)
                if not success:
                    return False, f"Eletrificador ALARM ON failed: error_code={error_code}"

                return True, "Eletrificador ALARM turned ON"

            except Exception as e:
                logger.error(f"Error turning eletrificador alarm on: {e}")
                return False, str(e)

    async def eletrificador_alarm_off(self) -> Tuple[bool, str]:
        """Turn off eletrificador ALARM (disarm the alarm function).

        This controls the ALARM function independently from the SHOCK function.

        Based on APK ISECNetV2SDK.java deactivateCentral() with isEletricfier=true:
        - Uses SYSTEM_ARM_DISARM command (0x401E)
        - With operation SYSTEM_DISARM
        - With partition_index=0 (becomes 1 in payload due to +1 encoding)

        Returns:
            Tuple of (success, message)
        """
        async with self._lock:
            if not self.is_authenticated:
                return False, "Not authenticated"

            try:
                logger.info(f"Turning eletrificador ALARM OFF using SYSTEM_DISARM with partition_index=0")

                # APK uses: assembleSystemArmCentralCommand(sourceID, SYSTEM_DISARM, 0)
                # For eletrificador, partitionIndex is forced to 0
                # partition_index=0 → payload byte = 1 (due to +1 encoding)
                cmd = self._build_arm_cmd(AlarmOperation.SYSTEM_DISARM, partition_index=0)
                logger.debug(f"Eletrificador ALARM OFF command: {cmd.hex()}")
                response = await self._send_and_receive(cmd)

                if not response:
                    return False, "No response"

                logger.debug(f"Eletrificador ALARM OFF response ({len(response)} bytes): {response.hex()}")

                success, error_code = self._parse_command_response(response)
                if not success:
                    return False, f"Eletrificador ALARM OFF failed: error_code={error_code}"

                return True, "Eletrificador ALARM turned OFF"

            except Exception as e:
                logger.error(f"Error turning eletrificador alarm off: {e}")
                return False, str(e)

    async def get_mac(self) -> Tuple[bool, str]:
        """Get alarm panel MAC address.

        Returns:
            Tuple of (success, mac_address)
        """
        async with self._lock:
            if not self.is_authenticated:
                return False, ""

            try:
                cmd = self._build_get_mac_cmd()
                response = await self._send_and_receive(cmd)

                if not response or len(response) <= 10:
                    return False, ""

                # MAC is in bytes 9 to end-1
                mac_bytes = response[9:-1]
                mac = ":".join(f"{b:02X}" for b in mac_bytes)

                return True, mac

            except Exception as e:
                logger.error(f"Error getting MAC: {e}")
                return False, ""
