from __future__ import annotations

import socket
import time
from dataclasses import dataclass
from datetime import date
from typing import Any

import serial

from .protocol import MercuryProtocolError, build_frame, parse_frame


class MercuryTransportError(Exception):
    pass


class MercuryNoResponseError(MercuryTransportError):
    pass


class _TcpTransparentTransport:
    """
    Transparent TCP transport: bytes are passed as-is (RTU-over-TCP tunnel mode).
    No MBAP header is added.
    """

    def __init__(self, host: str, port: int, timeout: float = 1.0) -> None:
        self._timeout = timeout
        self._sock = socket.create_connection((host, port), timeout=timeout)
        self._sock.settimeout(timeout)
        self._rx_buffer = bytearray()

    def close(self) -> None:
        self._sock.close()

    def write(self, data: bytes) -> int:
        self._sock.sendall(data)
        return len(data)

    def _drain_socket_nonblocking(self) -> None:
        prev_timeout = self._sock.gettimeout()
        try:
            self._sock.setblocking(False)
            while True:
                try:
                    chunk = self._sock.recv(4096)
                    if not chunk:
                        break
                    self._rx_buffer.extend(chunk)
                except (BlockingIOError, InterruptedError):
                    break
        finally:
            self._sock.settimeout(prev_timeout)

    def reset_input_buffer(self) -> None:
        self._rx_buffer.clear()
        self._drain_socket_nonblocking()
        self._rx_buffer.clear()

    @property
    def in_waiting(self) -> int:
        self._drain_socket_nonblocking()
        return len(self._rx_buffer)

    def read(self, size: int) -> bytes:
        if size <= 0:
            return b""

        out = bytearray()
        deadline = time.monotonic() + self._timeout

        while len(out) < size:
            if self._rx_buffer:
                take = min(size - len(out), len(self._rx_buffer))
                out.extend(self._rx_buffer[:take])
                del self._rx_buffer[:take]
                continue

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break

            self._sock.settimeout(remaining)
            try:
                chunk = self._sock.recv(4096)
            except socket.timeout:
                break

            if not chunk:
                break
            self._rx_buffer.extend(chunk)

        return bytes(out)


@dataclass
class PassportData:
    address: int
    serial_raw: bytes | None
    serial_number_decimal: str | None
    build_date: date | None
    software_version: tuple[int, int, int] | None
    voltage_transform_ratio: int | None
    current_transform_ratio: int | None
    raw: dict[str, bytes]


@dataclass
class EnergyFromReset:
    active_wh: dict[str, int]
    reactive_varh: dict[str, int]
    raw: dict[str, bytes]


def _parse_build_date(data: bytes) -> date | None:
    if len(data) < 3:
        return None
    day, month, year = data[-3], data[-2], data[-1]
    full_year = 2000 + year
    try:
        return date(full_year, month, day)
    except ValueError:
        return None


def _parse_serial_decimal(info_response_cmd: int, info_payload: bytes) -> str | None:
    # For Mercury-230 info block in your exchange:
    # serial is represented by [response_cmd][payload0][payload1][payload2]
    # where each byte is rendered as decimal with 2 digits and concatenated.
    if len(info_payload) < 3:
        return None
    serial_str = f"{info_response_cmd:02d}{info_payload[0]:02d}{info_payload[1]:02d}{info_payload[2]:02d}"
    return serial_str


def _normalize_address(address: int | str) -> int:
    if isinstance(address, int):
        value = address
    elif isinstance(address, str):
        text = address.strip()
        if not text.isdigit():
            raise ValueError("address must be decimal (for example: 47)")
        value = int(text, 10)
    else:
        raise TypeError("address must be int or decimal string")

    if not (0 <= value <= 255):
        raise ValueError("address must be in range 0..255")
    return value


class Mercury230Client:
    """
    Minimal Mercury-230 polling client over RS-485 (protocol with CRC16/Modbus).

    Frame format: [address][command][data...][crc_lo][crc_hi]
    """

    def __init__(
        self,
        port: str | None,
        address: int | str,
        baudrate: int = 9600,
        timeout: float = 1.0,
        retries: int = 1,
        transport: str = "serial",
        host: str | None = None,
        tcp_port: int | None = None,
    ) -> None:
        self.address = _normalize_address(address)
        if retries < 0:
            raise ValueError("retries must be >= 0")
        self.retries = retries
        if transport == "serial":
            if not port:
                raise ValueError("port is required for serial transport")
            self._io = serial.Serial(
                port=port,
                baudrate=baudrate,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=timeout,
            )
        elif transport == "tcp":
            if not host or tcp_port is None:
                raise ValueError("host and tcp_port are required for tcp transport")
            self._io = _TcpTransparentTransport(host=host, port=tcp_port, timeout=timeout)
        else:
            raise ValueError("transport must be 'serial' or 'tcp'")

    @classmethod
    def from_serial(
        cls,
        port: str,
        address: int | str,
        baudrate: int = 9600,
        timeout: float = 1.0,
        retries: int = 1,
    ) -> "Mercury230Client":
        return cls(
            port=port,
            address=address,
            baudrate=baudrate,
            timeout=timeout,
            retries=retries,
            transport="serial",
        )

    @classmethod
    def from_tcp(
        cls,
        host: str,
        tcp_port: int,
        address: int | str,
        timeout: float = 1.0,
        retries: int = 1,
    ) -> "Mercury230Client":
        return cls(
            port=None,
            address=address,
            timeout=timeout,
            retries=retries,
            transport="tcp",
            host=host,
            tcp_port=tcp_port,
        )

    def close(self) -> None:
        self._io.close()

    def __enter__(self) -> "Mercury230Client":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _exchange(self, command: int, data: bytes = b"", min_response: int = 4) -> tuple[int, bytes]:
        tx = build_frame(self.address, command, data)
        last_rx = b""
        last_error: Exception | None = None

        for _ in range(self.retries + 1):
            self._io.reset_input_buffer()
            self._io.write(tx)

            # Protocol in your log uses variable response length; read at least frame header+crc,
            # then grab whatever else is currently buffered.
            rx = self._io.read(min_response)
            rx += self._io.read(self._io.in_waiting)
            last_rx = rx

            if len(rx) < 4:
                continue

            try:
                _, response_cmd, payload = parse_frame(rx, expected_address=self.address)
                return response_cmd, payload
            except MercuryProtocolError as exc:
                last_error = exc
                continue

        if len(last_rx) < 4:
            raise MercuryNoResponseError(
                f"no response from meter address {self.address} for command 0x{command:02X} "
                f"after {self.retries + 1} attempt(s)"
            )

        if last_error:
            raise MercuryTransportError(
                f"invalid response for command 0x{command:02X}: {last_rx.hex(' ')} ({last_error})"
            )
        raise MercuryTransportError(f"invalid response for command 0x{command:02X}: {last_rx.hex(' ')}")

    def test_link(self) -> bool:
        """Sends command 0x00 and validates frame/CRC in response."""
        self._exchange(0x00)
        return True

    def open_session(self, access_level: int = 0x02, password: bytes = b"\x02\x02\x02\x02\x02\x02") -> None:
        """
        Opens channel (command 0x01).
        Typical default from your log: access_level=0x02 and password 02 02 02 02 02 02.
        """
        if len(password) != 6:
            raise ValueError("password must contain exactly 6 bytes")
        self._exchange(0x01, bytes([access_level]) + password)

    def read_network_address(self) -> int:
        """Command sequence from your log: 0x08 with subcommand 0x05."""
        _, payload = self._exchange(0x08, b"\x05")
        if not payload:
            raise MercuryProtocolError("empty payload for network address")
        return payload[0]

    def read_info_block(self) -> tuple[int, bytes]:
        """Command 0x08 / subcommand 0x00 (contains serial/date block in many revisions)."""
        response_cmd, payload = self._exchange(0x08, b"\x00")
        return response_cmd, payload

    def read_software_version(self) -> tuple[int, int, int] | None:
        """Command 0x08 / subcommand 0x03."""
        response_cmd, payload = self._exchange(0x08, b"\x03")
        if len(payload) < 3:
            # Some Mercury-230 revisions return version as:
            # response_cmd + payload[0] + payload[1], e.g. 02.02.84.
            if len(payload) == 2:
                return response_cmd, payload[0], payload[1]
            return None
        return payload[0], payload[1], payload[2]

    def read_transform_ratios(self) -> tuple[int | None, int | None, bytes]:
        """
        Command 0x08 / subcommand 0x02.
        In your log response payload is 4 bytes: 3C 00 78 31.
        Heuristic parse: U_ratio=uint16_le(first 2 bytes), I_ratio=third byte.
        """
        _, payload = self._exchange(0x08, b"\x02")
        if len(payload) < 3:
            return None, None, payload
        u_ratio = int.from_bytes(payload[0:2], "little")
        i_ratio = payload[2]
        return u_ratio, i_ratio, payload

    def read_passport(self) -> PassportData:
        """Runs a compact passport polling sequence aligned with your exchange log."""
        self.test_link()
        self.open_session()

        addr = self.read_network_address()
        info_response_cmd, info = self.read_info_block()
        sw = self.read_software_version()
        u_ratio, i_ratio, ratios_raw = self.read_transform_ratios()
        serial_raw = info[:-3] if len(info) >= 3 else None

        return PassportData(
            address=addr,
            serial_raw=serial_raw,
            serial_number_decimal=_parse_serial_decimal(info_response_cmd, info),
            build_date=_parse_build_date(info),
            software_version=sw,
            voltage_transform_ratio=u_ratio,
            current_transform_ratio=i_ratio,
            raw={
                "info_block": info,
                "ratios": ratios_raw,
            },
        )

    @staticmethod
    def _decode_energy_value(response_cmd: int, block: bytes) -> int:
        """
        Decodes one 4-byte energy block from Mercury-230 response (based on observed frames).
        Returns value in Wh (or varh).
        """
        if len(block) != 4:
            raise MercuryProtocolError("energy block must contain 4 bytes")

        b0, b1, b2, b3 = block
        if b0 == 0 and b1 == 0 and b2 == 0 and b3 == 0 and response_cmd == 0:
            return 0

        # Seen in tariff-2 frame where response_cmd is 0x00 and value lives in b2,b3.
        if response_cmd == 0 and b0 == 0 and b1 == 0 and b3 != 0xFF:
            return (b3 << 8) | b2

        # Main pattern from frames: value byte order is [b1, b2, response_cmd].
        return (response_cmd << 16) | (b2 << 8) | b1

    def _read_energy_register(self, group: int, index: int) -> tuple[int, int, bytes]:
        if not (0 <= group <= 0xFF):
            raise ValueError("group must be 0..255")
        if not (0 <= index <= 0xFF):
            raise ValueError("index must be 0..255")

        response_cmd, payload = self._exchange(0x05, bytes([group, index]))
        if len(payload) < 12:
            raise MercuryProtocolError(
                f"short energy payload for group/index {group:02X}/{index:02X}: {payload.hex(' ')}"
            )

        active_block = payload[0:4]
        reactive_block = payload[8:12]
        active_wh = self._decode_energy_value(response_cmd, active_block)
        reactive_varh = self._decode_energy_value(response_cmd, reactive_block)
        return active_wh, reactive_varh, payload

    def _prepare_energy_session(self) -> None:
        self.open_session(access_level=0x01, password=b"\x01\x01\x01\x01\x01\x01")
        self._exchange(0x08, b"\x12")

    def _read_energy_profile_group(self, group: int) -> EnergyFromReset:
        labels = {
            0: "sum",
            1: "t1",
            2: "t2",
            3: "t3",
            4: "t4",
            5: "loss",
        }
        active_wh: dict[str, int] = {}
        reactive_varh: dict[str, int] = {}
        raw: dict[str, bytes] = {}

        for idx, label in labels.items():
            a_wh, r_varh, payload = self._read_energy_register(group, idx)
            active_wh[label] = a_wh
            reactive_varh[label] = r_varh
            raw[f"idx_{idx:02d}"] = payload

        return EnergyFromReset(
            active_wh=active_wh,
            reactive_varh=reactive_varh,
            raw=raw,
        )

    def read_energy_from_reset(self) -> EnergyFromReset:
        """
        Reads cumulative energy from reset (command 0x05, request group 0x00).
        Output is in Wh/varh; divide by 1000 for kWh/kvarh.
        """
        self._prepare_energy_session()
        return self._read_energy_profile_group(0x00)

    def read_energy_for_month(self, month: int) -> EnergyFromReset:
        """
        Reads month archive energy (command 0x05, groups 0x31..0x3C for Jan..Dec).
        month: 1..12
        Output is in Wh/varh; divide by 1000 for kWh/kvarh.
        """
        if not (1 <= month <= 12):
            raise ValueError("month must be in range 1..12")

        self._prepare_energy_session()
        return self._read_energy_profile_group(0x30 + month)

    def read_energy_all_months(self) -> dict[int, EnergyFromReset]:
        """
        Reads month archive energy for all months (1..12) in one prepared session.
        """
        self._prepare_energy_session()
        result: dict[int, EnergyFromReset] = {}
        for month in range(1, 13):
            result[month] = self._read_energy_profile_group(0x30 + month)
        return result

    @staticmethod
    def format_energy_from_reset(energy: EnergyFromReset) -> dict[str, Any]:
        def _to_kilo(values: dict[str, int]) -> dict[str, float]:
            return {k: round(v / 1000.0, 3) for k, v in values.items()}

        return {
            "active_kwh": _to_kilo(energy.active_wh),
            "reactive_kvarh": _to_kilo(energy.reactive_varh),
            #"raw": {k: v.hex(" ") for k, v in energy.raw.items()},
        }

    @staticmethod
    def as_dict(passport: PassportData) -> dict[str, Any]:
        return {
            "address": passport.address,
            "serial_number_decimal": passport.serial_number_decimal,
            "build_date": passport.build_date.isoformat() if passport.build_date else None,
            "software_version": ".".join(map(str, passport.software_version)) if passport.software_version else None,
            "voltage_transform_ratio": passport.voltage_transform_ratio,
            "current_transform_ratio": passport.current_transform_ratio,
            #"raw": {k: v.hex(" ") for k, v in passport.raw.items()},
        }
