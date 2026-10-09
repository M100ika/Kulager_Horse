"""Разбор вывода консоли. Строки взяты из прогона на стенде."""

import math

import pytest

from ak80_gui import protocol as proto
from ak80_gui.data import TelemetryBuffer, summarize


def test_telemetry_parsed():
    tm = proto.parse_telemetry("15070,V,12.41,5.89,38.6,3.20,24,0")
    assert tm == proto.Telemetry(15070, "V", 12.41, 5.89, 38.6, 3.20, 24, 0)


@pytest.mark.parametrize("line", [
    proto.TELEMETRY_HEADER,
    "Вал свободен",
    "15070,X,12.41,5.89,38.6,3.20,24,0",  # нет такого режима
    "15070,V,12.41,5.89,38.6,3.20,24",  # не хватает поля
    "15070,V,nan,5.89,38.6,3.20,24,oops",
])
def test_not_telemetry(line):
    assert proto.parse_telemetry(line) is None


def test_cycle_move_parsed():
    m = proto.parse_move(
        "Ход 3/4: 4.0° → 34.0° за 0.91 с, ошибка 0.84°, отставание до 6.6°, "
        "пик момента 3.48 Н·м")
    assert m == proto.MoveResult(3, 4, 4.0, 34.0, 0.91, 0.84, 6.6, 3.48)


def test_single_move_parsed():
    m = proto.parse_move(
        "Ход: -0.8° → 44.2° за 0.79 с, ошибка -1.38°, отставание до 9.4°, "
        "пик момента 4.75 Н·м")
    assert m is not None
    assert (m.leg, m.legs) == (None, None)
    assert (m.from_deg, m.to_deg, m.error_deg) == (-0.8, 44.2, -1.38)


def test_cycle_summary_is_not_a_move():
    line = ("Цикл завершён: ходов 4, ошибка в конце: средняя 0.36°, СКО 0.94°, "
            "наибольшая 1.45°; отставание до 7.1°, пик момента 3.76 Н·м")
    assert proto.parse_move(line) is None


def test_settings_parsed():
    s = proto.parse_settings(
        "Настройки: speed 90 °/с, accel 180 °/с², kp 30.0, kd 1.00, "
        "maxt 15.0 Н·м, предел позиции ±688°")
    assert s == proto.Settings(90, 180, 30.0, 1.0, 15.0)


@pytest.mark.parametrize("line, mode", [
    ("Режим: удержание, уставка 72.3°", "H"),
    ("Режим: не в режиме управления", "O"),
    ("Режим: вал свободен, СВЯЗИ НЕТ", "L"),
    ("AK80-64, консоль стенда. CAN ID 4, кварц MCP2515 8 МГц", "O"),
    ("Мотор вышел из режима управления", "O"),
    ("Мотор в режиме управления, вал свободен", "L"),
    ("Вал свободен", "L"),
    ("! Остановка, вал свободен: нет ответа от мотора", "L"),
    ("Ход: -0.8° → 44.2° за 0.79 с, ошибка -1.38°, отставание до 9.4°, "
     "пик момента 4.75 Н·м", None),
])
def test_mode_from_line(line, mode):
    assert proto.mode_from_line(line) == mode


def test_commands():
    assert proto.cmd_move(90.0) == "m 90"
    assert proto.cmd_rel(-45.5) == "r -45.5"
    assert proto.cmd_vel(0) == "v 0"
    assert proto.cmd_vel(-0.001) == "v 0"
    assert proto.cmd_cycle(180, 10) == "c 180 10"
    assert proto.cmd_stream(50) == "stream 50"


def test_settings_commands_only_changed():
    old = proto.Settings(90, 180, 30, 1, 15)
    new = proto.Settings(120, 180, 30, 0.5, 15)
    assert proto.cmd_settings(new, old) == ["speed 120", "kd 0.5"]
    assert proto.cmd_settings(old, old) == []
    assert len(proto.cmd_settings(new, None)) == 5


def _tm(t_ms, mode="H", pos=0.0):
    return proto.Telemetry(t_ms, mode, 1.0, pos, 0.0, 0.0, 24, 0)


def test_buffer_window_and_reset():
    buf = TelemetryBuffer()
    for t in range(0, 20001, 1000):
        buf.append(_tm(t))
    d = buf.window(5)
    assert list(d["t"]) == [-5, -4, -3, -2, -1, 0]

    buf.append(_tm(100))  # Mega перезагрузилась, время пошло заново
    assert len(buf) == 1


def test_buffer_hides_setpoint_without_position_control():
    buf = TelemetryBuffer()
    buf.append(_tm(0, "L"))
    buf.append(_tm(20, "H"))
    p_ref = buf.window(10)["p_ref"]
    assert math.isnan(p_ref[0]) and p_ref[1] == 1.0


def test_summarize():
    moves = [proto.MoveResult(i, 4, 0, 30, 0.9, e, lag, t)
             for i, (e, lag, t) in enumerate(
                 [(1.45, 6.9, 3.55), (-0.33, 7.1, 3.76), (0.84, 6.6, 3.48),
                  (-0.52, 6.5, 3.48)], start=1)]
    s = summarize(moves)
    # Сводка консоли по этому циклу: средняя 0.36°, СКО 0.94°.
    assert s.count == 4
    assert s.error_mean == pytest.approx(0.36, abs=0.005)
    assert s.error_std == pytest.approx(0.94, abs=0.005)
    assert s.error_abs_max == 1.45
    assert (s.lag_max, s.torque_peak) == (7.1, 3.76)
    assert summarize([]) is None
