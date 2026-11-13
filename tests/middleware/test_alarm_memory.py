"""Zone alarm memory alone must not keep the central in TRIGGERED.

2026-11-13 16:51 local: the electric fence (zone 04, a 24 h zone) fired with
the central disarmed. data[38] bit 2 (the official app's "siren triggered")
was set for 17 s, until the siren was silenced from the Intelbras app. The
zone alarm byte (data[7] bit 3) stayed set, because the central keeps alarm
memory until the next arm, and the parser kept reporting is_triggered=True
off that memory alone. Home Assistant stayed on TRIGGERED, and the zone 42
clip automation (condition: panel not disarmed) sent camera clips to WhatsApp
on every perimeter event.

The frames below are the real 0x5D replies captured from the middleware log
that afternoon (207 data bytes, AMT_2018_E_SMART, partitions disarmed).
"""
from app.services.isecnet_protocol import ISECNetProtocol

# Captured at 20:11:58 UTC: zone 40 open, zone 04 in alarm memory, d38=0x00.
_MEMORY_ONLY = bytes.fromhex(
    "e90000000080000800000000000000000000003470010044110b0d0b1a00000f000005"
    "000300000f0000ffff07fd0000ffff07020000000000000000000000000000ffff0700"
    "0000000000000000000000000000000000000000000000000303000000000000f80000"
    "000008060709080908080a0a0a08080a070304ffff0000000000ffff0707038fc0a851"
    "86ffffff00c0a85101c0a851c801010101546cac17874a000000000000000000000000"
    "0000000000000000000000000000000000000000000000000000000000000000"
)

ZONE_04 = 3  # 0-based
OUTPUT_BYTE = 38


def _frame(data: bytes) -> bytes:
    body = [len(data)] + list(data)
    x = 0
    for b in body:
        x ^= b
    return bytes(body + [x ^ 0xFF])


def _parse(output_byte=0x00, zone_memory=True):
    data = bytearray(_MEMORY_ONLY)
    data[OUTPUT_BYTE] = output_byte
    if not zone_memory:
        data[7:13] = bytes(6)
    proto = ISECNetProtocol()
    proto._is_v1 = True
    status = proto._parse_isecv1_status_response(_frame(bytes(data)))
    assert status.is_valid is not False
    return status


def test_fixture_is_the_captured_frame():
    assert len(_MEMORY_ONLY) == 207
    assert _MEMORY_ONLY[19] == 0x34  # AMT_2018_E_SMART


def test_alarm_memory_without_siren_is_not_triggered():
    status = _parse(output_byte=0x00)
    assert status.is_triggered is False
    # The memory is still reported per zone, so it is not lost.
    in_alarm = [z["index"] for z in status.zones if z.get("triggered")]
    assert in_alarm == [ZONE_04]


def test_siren_bit2_with_zone_memory_is_triggered():
    # 19:51:29 UTC, the first poll after the fence fired: d38=0x04.
    assert _parse(output_byte=0x04).is_triggered is True


def test_bit2_without_zone_memory_is_still_suppressed():
    # The arm/disarm transient from 2026-03-09 stays filtered.
    assert _parse(output_byte=0x04, zone_memory=False).is_triggered is False


def test_siren_bit7_is_triggered_regardless_of_memory():
    assert _parse(output_byte=0x80, zone_memory=False).is_triggered is True


def test_quiet_frame_is_not_triggered():
    assert _parse(output_byte=0x00, zone_memory=False).is_triggered is False
