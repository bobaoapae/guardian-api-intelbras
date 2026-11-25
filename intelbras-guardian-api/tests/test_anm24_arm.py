"""ANM 24 NET status parse + V1 arm command variants."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.services.isecnet_protocol import ISECNetProtocol, ISECNetV1Command


# Captured from a live ANM 24 NET GET_PARTIAL_STATUS (44 data bytes).
# data[19]=0x24 (model 36), data[21]=0x01 (NOT a partition-enable flag).
ANM24_STATUS_DATA = bytes.fromhex(
    "e900000000000000000000000000000000000024640100002128160926"
    "000000000000000000400000000000"
)


def _wrap_v1(data: bytes) -> bytes:
    size = len(data)
    packet = bytes([size]) + data
    checksum = 0
    for b in packet:
        checksum ^= b
    checksum ^= 0xFF
    return packet + bytes([checksum])


def test_anm24_ignores_partition_enabled_byte():
    proto = ISECNetProtocol()
    status = proto._parse_isecv1_status_response(_wrap_v1(ANM24_STATUS_DATA))
    assert status.model == "ANM_24_NET"
    assert status.partitions_enabled is False
    assert proto._partitions_enabled is False
    assert status.is_armed is False
    assert len(status.zones) == 24
    assert all(not z["open"] for z in status.zones)


def test_anm24_max_partitions_is_zero():
    proto = ISECNetProtocol()
    assert proto._get_max_partitions_for_model(36) == 0
    assert proto._get_max_partitions_for_model(37) == 0


def test_arm_cmd_without_partition():
    proto = ISECNetProtocol()
    cmd = proto._build_isecv1_arm_cmd("1502", partition_index=None, stay=False)
    # [size][0xE9][0x21][password][0x41][0x21][checksum]
    assert cmd[1] == ISECNetV1Command.ISEC_PROGRAM
    assert cmd[2] == 0x21
    assert cmd[7] == ISECNetV1Command.ACTIVATE_CENTRAL  # after 4-digit password
    assert cmd[8] == 0x21


def test_arm_cmd_with_partition_a():
    proto = ISECNetProtocol()
    cmd = proto._build_isecv1_arm_cmd("1502", partition_index=0, stay=False)
    # [size][0xE9][0x21][1502][0x41][0x41][0x21][checksum]
    assert cmd[7] == ISECNetV1Command.ACTIVATE_CENTRAL
    assert cmd[8] == 0x41  # partition A
    assert cmd[9] == 0x21


def test_arm_cmd_home_stay_suffix():
    proto = ISECNetProtocol()
    cmd = proto._build_isecv1_arm_cmd("1502", partition_index=None, stay=True)
    assert 0x50 in cmd  # 'P'


if __name__ == "__main__":
    test_anm24_ignores_partition_enabled_byte()
    test_anm24_max_partitions_is_zero()
    test_arm_cmd_without_partition()
    test_arm_cmd_with_partition_a()
    test_arm_cmd_home_stay_suffix()
    print("ok")
