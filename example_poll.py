from mercury230 import Mercury230Client, MercuryNoResponseError, MercuryTransportError

# Choose one connection option:
# 1) Serial RS-485:
# meter = Mercury230Client.from_serial(port="COM2", address=47, baudrate=9600, timeout=1.0, retries=2)
# 2) TCP transparent tunnel (RTU bytes are sent as-is, without MBAP):
meter = Mercury230Client.from_tcp(host="10.0.31.202", tcp_port=2222, address=1, timeout=1.0, retries=2)

try:
    with meter:
        passport = meter.read_passport()
        energy = meter.read_energy_from_reset()
        all_months = meter.read_energy_all_months()

        print(Mercury230Client.as_dict(passport))
        print(Mercury230Client.format_energy_from_reset(energy))
        for month in range(1, 13):
            print(month, Mercury230Client.format_energy_from_reset(all_months[month]))
except MercuryNoResponseError as exc:
    print(f"No response: {exc}")
    print("Check meter address, connection, and transport settings (serial/tcp).")
except MercuryTransportError as exc:
    print(f"Transport/protocol error: {exc}")
