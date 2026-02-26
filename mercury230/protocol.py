from __future__ import annotations


class MercuryProtocolError(Exception):
    pass


def crc16_modbus(data: bytes) -> int:
    """CRC16/Modbus (poly 0xA001, init 0xFFFF)."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc & 0xFFFF


def build_frame(address: int, command: int, data: bytes = b"") -> bytes:
    if not (0 <= address <= 0xFF):
        raise ValueError("address must be 0..255")
    if not (0 <= command <= 0xFF):
        raise ValueError("command must be 0..255")
    payload = bytes([address, command]) + data
    crc = crc16_modbus(payload)
    return payload + crc.to_bytes(2, "little")


def parse_frame(frame: bytes, expected_address: int | None = None) -> tuple[int, int, bytes]:
    if len(frame) < 4:
        raise MercuryProtocolError("frame is too short")

    body = frame[:-2]
    received_crc = int.from_bytes(frame[-2:], "little")
    calculated_crc = crc16_modbus(body)
    if received_crc != calculated_crc:
        raise MercuryProtocolError(
            f"bad crc: got 0x{received_crc:04X}, expected 0x{calculated_crc:04X}"
        )

    address = frame[0]
    command = frame[1]
    if expected_address is not None and address != expected_address:
        raise MercuryProtocolError(
            f"unexpected address: got {address}, expected {expected_address}"
        )

    return address, command, frame[2:-2]
