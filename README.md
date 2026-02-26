# Mercury230 Python Polling Library

Python-библиотека для опроса электросчетчиков **Меркурий-230** по протоколу производителя.

Поддерживаются:
- подключение через **RS-485 / COM (serial)**
- подключение через **TCP transparent tunnel** (RTU-кадры передаются как есть, без MBAP)

## Возможности

- чтение паспортных данных счетчика:
- сетевой адрес
- серийный номер (в формате, например, `03328747`)
- дата изготовления
- версия ПО
- коэффициенты трансформации
- чтение энергии от сброса:
- `sum`, `t1`, `t2`, `t3`, `t4`, `loss`
- активная энергия (`kWh`)
- реактивная энергия (`kVArh`)
- чтение месячных архивов энергии:
- за выбранный месяц
- за все 12 месяцев

## Установка

```bash
pip install pyserial
```

Клонируйте проект и используйте модуль `mercury230`.

## Быстрый старт

```python
from mercury230 import Mercury230Client

# Serial
meter = Mercury230Client.from_serial(
    port="COM2",
    address=0x2F,
    baudrate=9600,
    timeout=1.0,
)

# TCP transparent (RTU-over-TCP tunnel)
# meter = Mercury230Client.from_tcp(
#     host="192.168.1.100",
#     tcp_port=4001,
#     address=0x2F,
#     timeout=1.0,
# )

with meter:
    passport = meter.read_passport()
    print(Mercury230Client.as_dict(passport))

    energy_reset = meter.read_energy_from_reset()
    print(Mercury230Client.format_energy_from_reset(energy_reset))

    march = meter.read_energy_for_month(3)
    print("March:", Mercury230Client.format_energy_from_reset(march))
```

## Структура проекта

- `mercury230/protocol.py` - CRC16/Modbus, сборка/разбор кадров
- `mercury230/client.py` - клиент, транспорт, команды и декодирование
- `example_poll.py` - пример опроса

## Примечания

- Формат кадров: `[address][command][data...][crc_lo][crc_hi]`
- CRC: `CRC16/Modbus` (little-endian в кадре)
- Для TCP используется прозрачный режим: библиотека не добавляет Modbus TCP заголовок (MBAP)

## Ограничение ответственности

Проект предоставляется "как есть". Перед использованием в промышленном контуре рекомендуется валидация на вашем типе счетчика и прошивке.

