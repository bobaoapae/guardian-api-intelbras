"""AMT 4010 Smart: extended status (0x5B) must be recognised as model 65.

Captured on a real AMT 4010 (firmware 0x66) over the local ISECNet port.
The panel rejects the partial status 0x5A with E9 E5 (COMMAND_DEPRECATED)
and answers 0x5B with 55 data bytes whose model byte is data[25], not
data[19]. Before the fix the parser cached model 0 and kept asking 0x5A.
"""
from app.services.isecnet_protocol import ISECNetProtocol

# Real frames. Zone 19 open in BASE; zones 17, 19 and 24 open in Z24_OPEN;
# partition A armed in ARM_A; partition B armed in ARM_B.
BASE = "37e90000040000000000000000000000000000000000000000004166010000040f2d170b1a00000000000f000000000000000000000000002c"
Z24_OPEN = "37e90000850000000000000000000000000000000000000000004166010000040f2d170b1a00000000000f00000000000000000000000000ad"
ARM_A = "37e90000000000000000000000000000000000000000000000004166010100080f30170b1a00000000000f0000000000000000000000000038"
ARM_B = "37e90000000000000000000000000000000000000000000000004166010200080f31170b1a00000000000f000000000000000000000000003a"


def _parse(frame_hex):
    proto = ISECNetProtocol()
    status = proto._parse_isecv1_status_response(bytes.fromhex(frame_hex))
    return proto, status


def _open_zones(status):
    return [z["index"] + 1 for z in status.zones if z.get("open")]


def test_model_detected_from_extended_status():
    proto, status = _parse(BASE)
    assert proto._model_code == 65
    assert status.model == "AMT_4010"
    assert len(status.zones) == 64
    assert status.partitions_enabled is True


def test_open_zones_bitmap():
    assert _open_zones(_parse(BASE)[1]) == [19]
    assert _open_zones(_parse(Z24_OPEN)[1]) == [17, 19, 24]


def test_partition_arm_bits():
    _, a = _parse(ARM_A)
    assert [p["state"] for p in a.partitions][:2] == ["armed_away", "disarmed"]
    _, b = _parse(ARM_B)
    assert [p["state"] for p in b.partitions][:2] == ["disarmed", "armed_away"]
    assert not a.is_triggered and not b.is_triggered

