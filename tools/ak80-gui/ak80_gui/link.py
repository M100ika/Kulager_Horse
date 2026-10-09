"""Связь с консолью ak80-mega-test по USB-Serial.

Чтение идёт в отдельном потоке, строки приходят в окно сигналом Qt.
При открытии порта Mega перезагружается (линия DTR) и выводит мотор из
режима управления, поэтому подключение всегда начинается с безопасного
состояния.
"""

from __future__ import annotations

import serial
from PySide6.QtCore import QObject, QThread, Signal
from serial.tools import list_ports

from .protocol import BAUD


def available_ports() -> list[tuple[str, str]]:
    """(устройство, описание); Arduino и ttyACM первыми."""
    ports = [(p.device, p.description or "") for p in list_ports.comports()]

    def rank(item: tuple[str, str]) -> tuple[int, str]:
        device, desc = item
        likely = "arduino" in desc.lower() or "ACM" in device
        return (0 if likely else 1, device)

    return sorted(ports, key=rank)


class _Reader(QThread):
    line = Signal(str)
    failed = Signal(str)

    def __init__(self, port: serial.Serial) -> None:
        super().__init__()
        self._port = port
        self._running = True

    def run(self) -> None:
        buf = b""
        while self._running:
            try:
                chunk = self._port.read(max(1, self._port.in_waiting))
            except (serial.SerialException, OSError) as exc:
                if self._running:
                    self.failed.emit(str(exc))
                return
            if not chunk:
                continue
            buf += chunk
            *lines, buf = buf.split(b"\n")
            for raw in lines:
                # Отступы слева сохраняются: ими выровнена справка консоли.
                text = raw.decode("utf-8", "replace").rstrip("\r ")
                if text.strip():
                    self.line.emit(text)

    def stop(self) -> None:
        self._running = False
        self.wait(1000)


class SerialLink(QObject):
    line_received = Signal(str)
    lost = Signal(str)  # порт пропал: кабель, перезагрузка и т. п.

    def __init__(self) -> None:
        super().__init__()
        self._port: serial.Serial | None = None
        self._reader: _Reader | None = None

    @property
    def is_open(self) -> bool:
        return self._port is not None

    def open(self, device: str) -> None:
        """Открывает порт; при ошибке бросает serial.SerialException."""
        self.close()
        self._port = serial.Serial(device, BAUD, timeout=0.1)
        self._reader = _Reader(self._port)
        self._reader.line.connect(self.line_received)
        self._reader.failed.connect(self._on_failed)
        self._reader.start()

    def send(self, text: str) -> bool:
        if self._port is None:
            return False
        try:
            self._port.write((text + "\n").encode("utf-8"))
        except (serial.SerialException, OSError) as exc:
            self._on_failed(str(exc))
            return False
        return True

    def close(self) -> None:
        if self._reader is not None:
            self._reader.stop()
            self._reader = None
        if self._port is not None:
            try:
                self._port.close()
            except (serial.SerialException, OSError):
                pass
            self._port = None

    def _on_failed(self, reason: str) -> None:
        self.close()
        self.lost.emit(reason)
