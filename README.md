# Mercury230 Python Polling Library

Python library for polling Mercury-230 power meters via vendor protocol.

Supported transports:
- RS-485 / COM (`serial`)
- TCP transparent tunnel (`rtu-over-tcp` bytes as-is, without MBAP)

## Features

- Passport data reading:
- network address
- serial number (for example: `03328747`)
- manufacture date
- software version
- transformation ratios
- Energy from reset:
- `sum`, `t1`, `t2`, `t3`, `t4`, `loss`
- active energy (`kWh`)
- reactive energy (`kVArh`)
- Monthly energy archive:
- one selected month
- all 12 months

## Requirements

```bash
pip install pyserial
```

## Address Format

Device `address` must be provided in decimal form (`0..255`), for example:
- `address=47`
- `address="47"`

Hex style like `0x2F` is not used in examples.

## Quick Start

```python
from mercury230 import Mercury230Client, MercuryNoResponseError, MercuryTransportError

# Serial
# meter = Mercury230Client.from_serial(
#     port="COM2",
#     address=47,
#     baudrate=9600,
#     timeout=1.0,
#     retries=2,
# )

# TCP transparent tunnel (RTU bytes are sent as-is, no MBAP)
meter = Mercury230Client.from_tcp(
    host="10.0.31.202",
    tcp_port=2222,
    address=47,
    timeout=1.0,
    retries=2,
)

try:
    with meter:
        passport = meter.read_passport()
        print(Mercury230Client.as_dict(passport))

        energy_reset = meter.read_energy_from_reset()
        print(Mercury230Client.format_energy_from_reset(energy_reset))

        all_months = meter.read_energy_all_months()
        for month in range(1, 13):
            print(month, Mercury230Client.format_energy_from_reset(all_months[month]))
except MercuryNoResponseError as exc:
    print(f"No response: {exc}")
except MercuryTransportError as exc:
    print(f"Transport/protocol error: {exc}")
```

## Project Structure

- `mercury230/protocol.py`: CRC16/Modbus, frame build/parse
- `mercury230/client.py`: client, transports, commands, decoding
- `example_poll.py`: usage example

## Notes

- Frame format: `[address][command][data...][crc_lo][crc_hi]`
- CRC: `CRC16/Modbus` (little-endian in frame)
- TCP mode is transparent: library does not add Modbus TCP MBAP header

## Disclaimer

Provided as-is. Validate on your exact meter model and firmware before production use.

