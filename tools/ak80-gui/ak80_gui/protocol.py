"""Разбор вывода консоли ak80-mega-test и сборка её команд.

Консоль (tools/ak80-mega-test/src/main.cpp) пишет строки двух видов:
телеметрию CSV, которая начинается с цифры, и сообщения для человека.
Здесь только чистые функции без ввода-вывода, их проверяют тесты.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

BAUD = 115200

# Режимы в телеметрии, по порядку enum Mode в прошивке.
MODE_NAMES = {
    "O": "не в режиме управления",
    "L": "вал свободен",
    "H": "удержание",
    "M": "ход",
    "V": "вращение",
}
_MODE_BY_NAME = {name: code for code, name in MODE_NAMES.items()}

# Коды ошибок драйвера, как в lib/ak80_mit.
ERROR_NAMES = {
    0: "нет ошибок",
    1: "перегрев мотора",
    2: "перегрузка по току",
    3: "перенапряжение",
    4: "недонапряжение",
    5: "ошибка энкодера",
    6: "перегрев MOSFET",
    7: "блокировка вала",
}

TELEMETRY_HEADER = "t_ms,mode,p_ref_deg,pos_deg,vel_dps,torque_nm,temp_c,error"
BANNER_PREFIX = "AK80-64, консоль стенда"

# Пределы команд, как в прошивке.
POSITION_LIMIT_DEG = 687.5
SPEED_LIMIT_DPS = 300.0
ACCEL_LIMIT_DPS2 = 1000.0


@dataclass(frozen=True)
class Telemetry:
    t_ms: int
    mode: str
    p_ref_deg: float
    pos_deg: float
    vel_dps: float
    torque_nm: float
    temp_c: int
    error: int


@dataclass(frozen=True)
class MoveResult:
    leg: int | None  # номер хода в цикле, None для одиночного хода
    legs: int | None
    from_deg: float
    to_deg: float
    duration_s: float
    error_deg: float
    lag_deg: float
    torque_peak_nm: float


@dataclass(frozen=True)
class Settings:
    speed_dps: float
    accel_dps2: float
    kp: float
    kd: float
    maxt_nm: float


def error_name(code: int) -> str:
    return ERROR_NAMES.get(code, f"неизвестный код {code}")


def parse_telemetry(line: str) -> Telemetry | None:
    parts = line.split(",")
    if len(parts) != 8 or parts[1] not in MODE_NAMES:
        return None
    try:
        return Telemetry(
            t_ms=int(parts[0]),
            mode=parts[1],
            p_ref_deg=float(parts[2]),
            pos_deg=float(parts[3]),
            vel_dps=float(parts[4]),
            torque_nm=float(parts[5]),
            temp_c=int(parts[6]),
            error=int(parts[7]),
        )
    except ValueError:
        return None


_NUM = r"(-?\d+(?:\.\d+)?)"
_MOVE_RE = re.compile(
    rf"^Ход(?: (\d+)/(\d+))?: {_NUM}° → {_NUM}° за {_NUM} с, "
    rf"ошибка {_NUM}°, отставание до {_NUM}°, пик момента {_NUM} Н·м$"
)
_SETTINGS_RE = re.compile(
    rf"^Настройки: speed {_NUM} °/с, accel {_NUM} °/с², kp {_NUM}, "
    rf"kd {_NUM}, maxt {_NUM} Н·м"
)
_STATUS_RE = re.compile(r"^Режим: ([^,]+)")


def parse_move(line: str) -> MoveResult | None:
    m = _MOVE_RE.match(line)
    if not m:
        return None
    leg, legs = m.group(1), m.group(2)
    return MoveResult(
        leg=int(leg) if leg else None,
        legs=int(legs) if legs else None,
        from_deg=float(m.group(3)),
        to_deg=float(m.group(4)),
        duration_s=float(m.group(5)),
        error_deg=float(m.group(6)),
        lag_deg=float(m.group(7)),
        torque_peak_nm=float(m.group(8)),
    )


def parse_settings(line: str) -> Settings | None:
    m = _SETTINGS_RE.match(line)
    if not m:
        return None
    speed, accel, kp, kd, maxt = (float(g) for g in m.groups())
    return Settings(speed, accel, kp, kd, maxt)


def mode_from_line(line: str) -> str | None:
    """Режим, о котором сообщает строка консоли, если сообщает."""
    m = _STATUS_RE.match(line)
    if m:
        return _MODE_BY_NAME.get(m.group(1).strip())
    if line.startswith(BANNER_PREFIX):
        return "O"  # после перезагрузки Mega мотор выведен из режима управления
    if line.startswith("Мотор вышел из режима управления"):
        return "O"
    if line.startswith(("Мотор в режиме управления", "Вал свободен", "! Остановка")):
        return "L"
    return None


def is_banner(line: str) -> bool:
    return line.startswith(BANNER_PREFIX)


def is_warning(line: str) -> bool:
    return line.startswith("!")


# ---------- Команды ----------


def _num(x: float) -> str:
    """Число без лишних нулей: 90.0 -> 90, 0.25 -> 0.25."""
    text = f"{x:.2f}".rstrip("0").rstrip(".")
    return "0" if text == "-0" else text


def cmd_move(deg: float) -> str:
    return f"m {_num(deg)}"


def cmd_rel(deg: float) -> str:
    return f"r {_num(deg)}"


def cmd_vel(dps: float) -> str:
    return f"v {_num(dps)}"


def cmd_cycle(deg: float, count: int) -> str:
    return f"c {_num(deg)} {int(count)}"


def cmd_stream(hz: float) -> str:
    return f"stream {_num(hz)}"


def cmd_settings(new: Settings, old: Settings | None) -> list[str]:
    """Команды, которые меняют только отличающиеся настройки."""
    pairs = [
        ("speed", new.speed_dps, old.speed_dps if old else None),
        ("accel", new.accel_dps2, old.accel_dps2 if old else None),
        ("kp", new.kp, old.kp if old else None),
        ("kd", new.kd, old.kd if old else None),
        ("maxt", new.maxt_nm, old.maxt_nm if old else None),
    ]
    return [
        f"{name} {_num(value)}"
        for name, value, prev in pairs
        if prev is None or abs(value - prev) > 1e-6
    ]
