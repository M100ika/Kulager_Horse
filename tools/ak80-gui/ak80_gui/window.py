"""Главное окно пульта AK80-64.

Слева команды и состояние мотора, справа графики телеметрии, журнал консоли
и таблица ходов. Все действия превращаются в текстовые команды консоли
ak80-mega-test, поэтому любую из них можно набрать и вручную в журнале.
"""

from __future__ import annotations

import html
import time
from datetime import datetime
from pathlib import Path

import pyqtgraph as pg
import serial
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QCloseEvent, QFont, QFontDatabase, QKeyEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from . import protocol as proto
from .data import CsvRecorder, TelemetryBuffer, save_moves, summarize
from .link import SerialLink, available_ports

LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
STREAM_RATES_HZ = [10, 20, 50, 100]
DEFAULT_RATE_HZ = 50
PLOT_WINDOWS_S = [5, 10, 30, 60]
DEFAULT_WINDOW_S = 10
INIT_FALLBACK_MS = 4000  # если приветствие консоли не пришло
TELEMETRY_STALE_S = 1.5
QUICK_STEPS_DEG = [-180, -90, -10, 10, 90, 180]

LOG_COLORS = {
    "cmd": "#3b82f6",
    "warn": "#dc2626",
    "move": "#16a34a",
    "sys": "#8b5cf6",
    "info": None,
}
MODE_COLORS = {"O": "#6b7280", "L": "#d97706", "H": "#16a34a", "M": "#16a34a", "V": "#16a34a"}


def _spin(lo: float, hi: float, value: float, decimals: int = 1,
          step: float = 1.0, suffix: str = "") -> QDoubleSpinBox:
    box = QDoubleSpinBox()
    box.setRange(lo, hi)
    box.setDecimals(decimals)
    box.setSingleStep(step)
    box.setValue(value)
    box.setSuffix(suffix)
    box.setAccelerated(True)
    return box


class CommandLine(QLineEdit):
    """Строка ручного ввода с историей по стрелкам вверх и вниз."""

    def __init__(self) -> None:
        super().__init__()
        self._history: list[str] = []
        self._pos = 0
        self.setPlaceholderText("Команда консоли, например m 90 или help. Enter — отправить")

    def take(self) -> str:
        text = self.text().strip()
        if text:
            self._history.append(text)
            self._pos = len(self._history)
        self.clear()
        return text

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Up and self._history:
            self._pos = max(0, self._pos - 1)
            self.setText(self._history[self._pos])
        elif event.key() == Qt.Key.Key_Down and self._history:
            self._pos = min(len(self._history), self._pos + 1)
            self.setText(self._history[self._pos] if self._pos < len(self._history) else "")
        else:
            super().keyPressEvent(event)


class MainWindow(QMainWindow):
    def __init__(self, port: str | None = None) -> None:
        super().__init__()
        self.setWindowTitle("AK80-64 — пульт стенда")
        self.resize(1400, 900)

        self.link = SerialLink()
        self.link.line_received.connect(self.on_line)
        self.link.lost.connect(self.on_link_lost)
        self.port = ""
        self.buffer = TelemetryBuffer()
        self.moves: list[proto.MoveResult] = []
        self.fw_settings: proto.Settings | None = None
        self.recorder: CsvRecorder | None = None
        self.last_tm_time = 0.0  # time.monotonic() последней телеметрии
        self.tm_count = 0  # строк телеметрии с прошлого обновления статуса
        self.tm_rate = 0

        self._build_ui()
        self._refresh_ports(prefer=port)
        self._set_connected(False)

        self.plot_timer = QTimer(self)
        self.plot_timer.setInterval(50)
        self.plot_timer.timeout.connect(self._update_plots)
        self.plot_timer.start()

        self.status_timer = QTimer(self)
        self.status_timer.setInterval(1000)
        self.status_timer.timeout.connect(self._update_status)
        self.status_timer.start()

        self.init_timer = QTimer(self)
        self.init_timer.setSingleShot(True)
        self.init_timer.setInterval(INIT_FALLBACK_MS)
        self.init_timer.timeout.connect(self._send_init)

        stop = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        stop.setContext(Qt.ShortcutContext.ApplicationShortcut)
        stop.activated.connect(self.stop)

        if port:
            QTimer.singleShot(0, self.toggle_connection)

    # ---------- Построение интерфейса ----------

    def _build_ui(self) -> None:
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.addWidget(self._build_connection())

        self.stop_btn = QPushButton("СТОП — освободить вал  (Esc)")
        self.stop_btn.setMinimumHeight(56)
        self.stop_btn.setStyleSheet(
            "QPushButton { background: #dc2626; color: white; font-size: 17px;"
            " font-weight: bold; border-radius: 6px; }"
            "QPushButton:hover { background: #b91c1c; }"
            "QPushButton:disabled { background: #9ca3af; }"
        )
        self.stop_btn.clicked.connect(self.stop)
        lv.addWidget(self.stop_btn)
        lv.addWidget(self._build_state())

        self.controls = QWidget()
        cv = QVBoxLayout(self.controls)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.addWidget(self._build_mode_group())
        cv.addWidget(self._build_position_group())
        cv.addWidget(self._build_velocity_group())
        cv.addWidget(self._build_cycle_group())
        cv.addWidget(self._build_settings_group())
        lv.addWidget(self.controls)
        lv.addStretch()

        scroll = QScrollArea()
        scroll.setWidget(left)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setMinimumWidth(
            left.minimumSizeHint().width() + scroll.verticalScrollBar().sizeHint().width() + 4)

        right = QSplitter(Qt.Orientation.Vertical)
        right.addWidget(self._build_plots())
        right.addWidget(self._build_tabs())
        right.setSizes([560, 320])

        main = QSplitter(Qt.Orientation.Horizontal)
        main.addWidget(scroll)
        main.addWidget(right)
        main.setStretchFactor(1, 1)
        main.setSizes([scroll.minimumWidth(), 1400 - scroll.minimumWidth()])
        self.setCentralWidget(main)

        self.conn_label = QLabel()
        self.statusBar().addPermanentWidget(self.conn_label)

    def _build_connection(self) -> QGroupBox:
        g = QGroupBox("Подключение")
        lay = QHBoxLayout(g)
        self.port_box = QComboBox()
        # Ширина не зависит от описаний портов, иначе левая панель обрежется.
        self.port_box.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.port_box.setMinimumContentsLength(18)
        refresh = QPushButton("↻")
        refresh.setToolTip("Обновить список портов")
        refresh.setFixedWidth(34)
        refresh.clicked.connect(lambda: self._refresh_ports())
        self.connect_btn = QPushButton("Подключить")
        self.connect_btn.clicked.connect(self.toggle_connection)
        lay.addWidget(self.port_box, 1)
        lay.addWidget(refresh)
        lay.addWidget(self.connect_btn)
        return g

    def _build_state(self) -> QGroupBox:
        g = QGroupBox("Состояние мотора")
        grid = QGridLayout(g)
        big = QFont()
        big.setPointSize(20)
        big.setBold(True)

        self.mode_label = QLabel("—")
        self.mode_label.setStyleSheet("font-weight: bold;")
        self.mode_label.setWordWrap(True)  # длинный режим не расширяет панель
        self.pos_label = QLabel("—")
        self.pos_label.setFont(big)
        self.ref_label = QLabel("—")
        self.vel_label = QLabel("—")
        self.torque_label = QLabel("—")
        self.temp_label = QLabel("—")
        self.err_label = QLabel("—")

        rows = [
            ("Режим", self.mode_label),
            ("Позиция", self.pos_label),
            ("Уставка", self.ref_label),
            ("Скорость", self.vel_label),
            ("Момент", self.torque_label),
            ("Температура", self.temp_label),
            ("Ошибки", self.err_label),
        ]
        for i, (name, label) in enumerate(rows):
            grid.addWidget(QLabel(name), i, 0)
            grid.addWidget(label, i, 1)
        grid.setColumnStretch(1, 1)
        return g

    def _build_mode_group(self) -> QGroupBox:
        g = QGroupBox("Режим управления")
        lay = QHBoxLayout(g)
        for text, cmd, tip in [
            ("Включить", "on", "Войти в режим управления, вал свободен (on)"),
            ("Держать", "hold", "Держать текущую позицию (hold)"),
            ("Выключить", "off", "Выйти из режима управления (off)"),
        ]:
            btn = QPushButton(text)
            btn.setToolTip(tip)
            btn.clicked.connect(lambda _=False, c=cmd: self.send(c))
            lay.addWidget(btn)
        return g

    def _build_position_group(self) -> QGroupBox:
        g = QGroupBox("Позиция")
        v = QVBoxLayout(g)
        limit = proto.POSITION_LIMIT_DEG

        row = QHBoxLayout()
        self.abs_spin = _spin(-limit, limit, 0.0, suffix="°")
        go = QPushButton("Перейти")
        go.setToolTip("Ход в абсолютную позицию (m)")
        go.clicked.connect(lambda: self.send(proto.cmd_move(self.abs_spin.value())))
        row.addWidget(QLabel("В позицию"))
        row.addWidget(self.abs_spin, 1)
        row.addWidget(go)
        v.addLayout(row)

        row = QHBoxLayout()
        self.rel_spin = _spin(-2 * limit, 2 * limit, 45.0, suffix="°")
        turn = QPushButton("Повернуть")
        turn.setToolTip("Поворот от текущей уставки (r)")
        turn.clicked.connect(lambda: self.send(proto.cmd_rel(self.rel_spin.value())))
        row.addWidget(QLabel("На угол"))
        row.addWidget(self.rel_spin, 1)
        row.addWidget(turn)
        v.addLayout(row)

        quick = QGridLayout()
        for i, step in enumerate(QUICK_STEPS_DEG):
            btn = QPushButton(f"{step:+d}°")
            btn.clicked.connect(lambda _=False, s=step: self.send(proto.cmd_rel(s)))
            quick.addWidget(btn, i // 3, i % 3)
        v.addLayout(quick)
        return g

    def _build_velocity_group(self) -> QGroupBox:
        g = QGroupBox("Вращение")
        lay = QHBoxLayout(g)
        limit = proto.SPEED_LIMIT_DPS
        self.vel_spin = _spin(-limit, limit, 30.0, suffix=" °/с")
        run = QPushButton("Вращать")
        run.clicked.connect(lambda: self.send(proto.cmd_vel(self.vel_spin.value())))
        halt = QPushButton("Плавно стоп")
        halt.setToolTip("Плавно снизить скорость до нуля и держать (v 0)")
        halt.clicked.connect(lambda: self.send(proto.cmd_vel(0)))
        lay.addWidget(self.vel_spin, 1)
        lay.addWidget(run)
        lay.addWidget(halt)
        return g

    def _build_cycle_group(self) -> QGroupBox:
        g = QGroupBox("Цикл туда и обратно")
        v = QVBoxLayout(g)
        row = QHBoxLayout()
        limit = proto.POSITION_LIMIT_DEG
        self.cycle_angle = _spin(-2 * limit, 2 * limit, 90.0, suffix="°")
        self.cycle_count = QSpinBox()
        self.cycle_count.setRange(1, 1000)
        self.cycle_count.setValue(5)
        self.cycle_count.setSuffix(" раз")
        start = QPushButton("Запустить")
        start.setToolTip("N раз на угол от текущей позиции и обратно (c)")
        start.clicked.connect(self._start_cycle)
        row.addWidget(self.cycle_angle, 1)
        row.addWidget(self.cycle_count, 1)
        row.addWidget(start)
        v.addLayout(row)
        self.cycle_bar = QProgressBar()
        self.cycle_bar.setFormat("ход %v из %m")
        self.cycle_bar.setRange(0, 1)
        self.cycle_bar.setValue(0)
        v.addWidget(self.cycle_bar)
        return g

    def _build_settings_group(self) -> QGroupBox:
        g = QGroupBox("Настройки движения")
        v = QVBoxLayout(g)
        form = QFormLayout()
        self.speed_spin = _spin(1, proto.SPEED_LIMIT_DPS, 90, 0, 10, " °/с")
        self.accel_spin = _spin(10, proto.ACCEL_LIMIT_DPS2, 180, 0, 10, " °/с²")
        self.kp_spin = _spin(1, 200, 30, 1, 1, " Н·м/рад")
        self.kd_spin = _spin(0, 5, 1, 2, 0.1, " Н·м·с/рад")
        self.maxt_spin = _spin(1, 60, 15, 1, 1, " Н·м")
        form.addRow("Наибольшая скорость", self.speed_spin)
        form.addRow("Наибольшее ускорение", self.accel_spin)
        form.addRow("Жёсткость kp", self.kp_spin)
        form.addRow("Демпфирование kd", self.kd_spin)
        form.addRow("Предел момента", self.maxt_spin)
        v.addLayout(form)

        row = QHBoxLayout()
        apply = QPushButton("Применить")
        apply.clicked.connect(self._apply_settings)
        zero = QPushButton("Задать ноль…")
        zero.setToolTip("Текущая позиция становится нулём (zero)")
        zero.clicked.connect(self._set_zero)
        row.addWidget(apply)
        row.addWidget(zero)
        v.addLayout(row)
        return g

    def _build_plots(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        bar = QHBoxLayout()
        self.window_box = QComboBox()
        for s in PLOT_WINDOWS_S:
            self.window_box.addItem(f"{s} с", s)
        self.window_box.setCurrentIndex(PLOT_WINDOWS_S.index(DEFAULT_WINDOW_S))
        self.rate_box = QComboBox()
        for hz in STREAM_RATES_HZ:
            self.rate_box.addItem(f"{hz} Гц", hz)
        self.rate_box.setCurrentIndex(STREAM_RATES_HZ.index(DEFAULT_RATE_HZ))
        self.rate_box.currentIndexChanged.connect(self._send_stream_rate)
        self.pause_check = QCheckBox("Пауза графиков")
        self.record_check = QCheckBox("Записывать телеметрию в CSV")
        self.record_check.toggled.connect(self._toggle_recording)
        bar.addWidget(QLabel("Окно"))
        bar.addWidget(self.window_box)
        bar.addWidget(QLabel("Телеметрия"))
        bar.addWidget(self.rate_box)
        bar.addWidget(self.pause_check)
        bar.addStretch()
        bar.addWidget(self.record_check)
        v.addLayout(bar)

        pg.setConfigOptions(antialias=True)
        self.plots = pg.GraphicsLayoutWidget()
        self.p_pos = self.plots.addPlot(row=0, col=0)
        self.p_vel = self.plots.addPlot(row=1, col=0)
        self.p_torque = self.plots.addPlot(row=2, col=0)
        for plot, label in [
            (self.p_pos, "Позиция, °"),
            (self.p_vel, "Скорость, °/с"),
            (self.p_torque, "Момент, Н·м"),
        ]:
            plot.setLabel("left", label)
            plot.getAxis("left").enableAutoSIPrefix(False)
            plot.showGrid(x=True, y=True, alpha=0.3)
            plot.setMouseEnabled(x=False, y=True)
        self.p_vel.setXLink(self.p_pos)
        self.p_torque.setXLink(self.p_pos)
        self.p_torque.setLabel("bottom", "время, с (0 — сейчас)")
        self.p_pos.addLegend(offset=(10, 10))
        self.c_ref = self.p_pos.plot(
            pen=pg.mkPen((120, 180, 255), width=1, style=Qt.PenStyle.DashLine),
            name="уставка", connect="finite")
        self.c_pos = self.p_pos.plot(pen=pg.mkPen((255, 200, 60), width=2), name="позиция")
        self.c_vel = self.p_vel.plot(pen=pg.mkPen((80, 220, 140), width=2))
        self.c_torque = self.p_torque.plot(pen=pg.mkPen((240, 110, 110), width=2))
        v.addWidget(self.plots, 1)
        return w

    def _build_tabs(self) -> QTabWidget:
        self.tabs = QTabWidget()

        log_w = QWidget()
        v = QVBoxLayout(log_w)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(5000)
        self.log_view.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        row = QHBoxLayout()
        self.cmd_line = CommandLine()
        self.cmd_line.returnPressed.connect(self._send_cmd_line)
        send = QPushButton("Отправить")
        send.clicked.connect(self._send_cmd_line)
        row.addWidget(self.cmd_line, 1)
        row.addWidget(send)
        v.addWidget(self.log_view, 1)
        v.addLayout(row)
        self.tabs.addTab(log_w, "Журнал")

        moves_w = QWidget()
        v = QVBoxLayout(moves_w)
        headers = ["№", "Из, °", "В, °", "Время, с", "Ошибка, °", "Отставание, °", "Пик момента, Н·м"]
        self.moves_table = QTableWidget(0, len(headers))
        self.moves_table.setHorizontalHeaderLabels(headers)
        self.moves_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.moves_table.verticalHeader().setVisible(False)
        self.moves_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.summary_label = QLabel("Ходов пока нет")
        self.summary_label.setWordWrap(True)
        row = QHBoxLayout()
        fw_stats = QPushButton("Статистика контроллера")
        fw_stats.setToolTip("Запросить stats: связь, ходы, наибольшие значения")
        fw_stats.clicked.connect(self._request_stats)
        reset = QPushButton("Очистить")
        reset.setToolTip("Очистить таблицу и сбросить статистику контроллера (stats reset)")
        reset.clicked.connect(self._reset_moves)
        save = QPushButton("Сохранить CSV…")
        save.clicked.connect(self._save_moves)
        row.addWidget(fw_stats)
        row.addWidget(reset)
        row.addStretch()
        row.addWidget(save)
        v.addWidget(self.moves_table, 1)
        v.addWidget(self.summary_label)
        v.addLayout(row)
        self.tabs.addTab(moves_w, "Ходы")
        return self.tabs

    # ---------- Подключение ----------

    def _refresh_ports(self, prefer: str | None = None) -> None:
        current = prefer or self.port_box.currentData()
        self.port_box.clear()
        for device, desc in available_ports():
            self.port_box.addItem(f"{device} — {desc}" if desc else device, device)
        if prefer and self.port_box.findData(prefer) < 0:
            self.port_box.addItem(prefer, prefer)
        index = self.port_box.findData(current)
        if index >= 0:
            self.port_box.setCurrentIndex(index)

    def toggle_connection(self) -> None:
        if self.link.is_open:
            self._disconnect()
            return
        device = self.port_box.currentData()
        if not device:
            QMessageBox.warning(self, "Нет порта", "Подключите Arduino и нажмите ↻.")
            return
        try:
            self.link.open(device)
        except serial.SerialException as exc:
            QMessageBox.warning(self, "Порт не открылся", f"{device}: {exc}")
            return
        self.port = device
        self.buffer.clear()
        self.last_tm_time = time.monotonic()
        self._set_connected(True)
        self.log(f"Подключено к {device}. Mega перезагружается, ждём консоль…", "sys")
        self.init_timer.start()

    def _disconnect(self) -> None:
        if self.link.is_open:
            # Оставить мотор в безопасном состоянии: вал свободен, вне режима.
            self.link.send("x")
            self.link.send("off")
        self.link.close()
        self.init_timer.stop()
        self._stop_recording()
        self._set_connected(False)
        self.log("Отключено", "sys")

    def on_link_lost(self, reason: str) -> None:
        self.init_timer.stop()
        self._stop_recording()
        self._set_connected(False)
        self.log(f"Порт пропал: {reason}", "warn")

    def _set_connected(self, on: bool) -> None:
        self.controls.setEnabled(on)
        self.stop_btn.setEnabled(on)
        self.cmd_line.setEnabled(on)
        self.record_check.setEnabled(on)
        self.port_box.setEnabled(not on)
        self.connect_btn.setText("Отключить" if on else "Подключить")
        if not on:
            self.mode_label.setText("нет подключения")
            self.mode_label.setStyleSheet("font-weight: bold; color: #6b7280;")
        self._update_status()

    def _send_init(self) -> None:
        """После перезагрузки консоли: узнать настройки и включить телеметрию."""
        self.init_timer.stop()
        if self.link.is_open:
            self.send("s")
            self._send_stream_rate()

    def _send_stream_rate(self) -> None:
        if self.link.is_open:
            self.send(proto.cmd_stream(self.rate_box.currentData()))

    # ---------- Команды ----------

    def send(self, cmd: str) -> bool:
        if not self.link.send(cmd):
            self.statusBar().showMessage("Нет подключения", 3000)
            return False
        self.log(f"> {cmd}", "cmd")
        return True

    def stop(self) -> None:
        if self.send("x"):
            self.cycle_bar.setValue(0)

    def _send_cmd_line(self) -> None:
        text = self.cmd_line.take()
        if text:
            self.send(text)

    def _start_cycle(self) -> None:
        count = self.cycle_count.value()
        if self.send(proto.cmd_cycle(self.cycle_angle.value(), count)):
            self.cycle_bar.setRange(0, 2 * count)
            self.cycle_bar.setValue(0)

    def _apply_settings(self) -> None:
        new = proto.Settings(
            self.speed_spin.value(), self.accel_spin.value(), self.kp_spin.value(),
            self.kd_spin.value(), self.maxt_spin.value())
        commands = proto.cmd_settings(new, self.fw_settings)
        if not commands:
            self.statusBar().showMessage("Настройки не изменились", 3000)
        for cmd in commands:
            self.send(cmd)

    def _set_zero(self) -> None:
        answer = QMessageBox.question(
            self, "Задать ноль",
            "Текущая позиция вала станет нулём в самом моторе.\n"
            "Вал при этом должен быть свободен: сначала СТОП.\n\nПродолжить?")
        if answer == QMessageBox.StandardButton.Yes:
            self.send("zero")

    def _request_stats(self) -> None:
        if self.send("stats"):
            self.tabs.setCurrentIndex(0)

    def _reset_moves(self) -> None:
        self.send("stats reset")
        self.moves.clear()
        self.moves_table.setRowCount(0)
        self._update_summary()

    def _save_moves(self) -> None:
        if not self.moves:
            self.statusBar().showMessage("Ходов пока нет", 3000)
            return
        LOG_DIR.mkdir(exist_ok=True)
        default = LOG_DIR / f"moves-{datetime.now():%Y%m%d-%H%M%S}.csv"
        path, _ = QFileDialog.getSaveFileName(self, "Сохранить ходы", str(default), "CSV (*.csv)")
        if path:
            save_moves(Path(path), self.moves)
            self.log(f"Ходы сохранены: {path}", "sys")

    # ---------- Запись телеметрии ----------

    def _toggle_recording(self, on: bool) -> None:
        if not on:
            self._stop_recording()
            return
        LOG_DIR.mkdir(exist_ok=True)
        path = LOG_DIR / f"telemetry-{datetime.now():%Y%m%d-%H%M%S}.csv"
        self.recorder = CsvRecorder(path)
        self.log(f"Запись телеметрии: {path}", "sys")

    def _stop_recording(self) -> None:
        if self.recorder is not None:
            self.recorder.close()
            self.log(f"Записано {self.recorder.rows} строк: {self.recorder.path}", "sys")
            self.recorder = None
        self.record_check.blockSignals(True)
        self.record_check.setChecked(False)
        self.record_check.blockSignals(False)

    # ---------- Входящие строки ----------

    def on_line(self, line: str) -> None:
        tm = proto.parse_telemetry(line)
        if tm is not None:
            self._on_telemetry(tm)
            return
        if line == proto.TELEMETRY_HEADER:
            return

        move = proto.parse_move(line)
        if proto.is_warning(line):
            self.log(line, "warn")
            self.statusBar().showMessage(line, 10000)
        elif move is not None or line.startswith("Цикл завершён"):
            self.log(line, "move")
        else:
            self.log(line)

        if proto.is_banner(line):
            self.buffer.clear()
            QTimer.singleShot(300, self._send_init)
        if move is not None:
            self._add_move(move)
        settings = proto.parse_settings(line)
        if settings is not None:
            self._show_fw_settings(settings)
        mode = proto.mode_from_line(line)
        if mode is not None:
            self._show_mode(mode)

    def _on_telemetry(self, tm: proto.Telemetry) -> None:
        self.buffer.append(tm)
        self.tm_count += 1
        self.last_tm_time = time.monotonic()
        if self.recorder is not None:
            self.recorder.write(tm)
        self._show_mode(tm.mode)
        self.pos_label.setText(f"{tm.pos_deg:.1f}°")
        self.ref_label.setText(f"{tm.p_ref_deg:.1f}°" if tm.mode in "HMV" else "—")
        self.vel_label.setText(f"{tm.vel_dps:.1f} °/с")
        self.torque_label.setText(f"{tm.torque_nm:.2f} Н·м")
        self.temp_label.setText(f"{tm.temp_c} °C")
        self.err_label.setText(proto.error_name(tm.error))
        self.err_label.setStyleSheet("color: #dc2626; font-weight: bold;" if tm.error else "")

    def _show_mode(self, code: str) -> None:
        text = proto.MODE_NAMES.get(code, code)
        if code == "O":
            text += " (значения последние известные)"
        self.mode_label.setText(text)
        self.mode_label.setStyleSheet(f"font-weight: bold; color: {MODE_COLORS.get(code, '')};")

    def _show_fw_settings(self, s: proto.Settings) -> None:
        self.fw_settings = s
        for box, value in [
            (self.speed_spin, s.speed_dps),
            (self.accel_spin, s.accel_dps2),
            (self.kp_spin, s.kp),
            (self.kd_spin, s.kd),
            (self.maxt_spin, s.maxt_nm),
        ]:
            box.setValue(value)

    def _add_move(self, m: proto.MoveResult) -> None:
        self.moves.append(m)
        row = self.moves_table.rowCount()
        self.moves_table.insertRow(row)
        number = f"{m.leg}/{m.legs}" if m.leg is not None else str(len(self.moves))
        values = [
            number, f"{m.from_deg:.1f}", f"{m.to_deg:.1f}", f"{m.duration_s:.2f}",
            f"{m.error_deg:+.2f}", f"{m.lag_deg:.1f}", f"{m.torque_peak_nm:.2f}",
        ]
        for col, text in enumerate(values):
            item = QTableWidgetItem(text)
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.moves_table.setItem(row, col, item)
        self.moves_table.scrollToBottom()
        if m.legs is not None:
            self.cycle_bar.setRange(0, m.legs)
            self.cycle_bar.setValue(m.leg or 0)
        self._update_summary()

    def _update_summary(self) -> None:
        s = summarize(self.moves)
        if s is None:
            self.summary_label.setText("Ходов пока нет")
            return
        self.summary_label.setText(
            f"Ходов {s.count}. Ошибка в конце хода: средняя {s.error_mean:+.2f}°, "
            f"СКО {s.error_std:.2f}°, наибольшая {s.error_abs_max:.2f}°. "
            f"Отставание до {s.lag_max:.1f}°, пик момента {s.torque_peak:.2f} Н·м."
        )

    # ---------- Журнал, графики, статус ----------

    def log(self, text: str, kind: str = "info") -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        body = html.escape(text).replace("  ", "&nbsp;&nbsp;")
        color = LOG_COLORS[kind]
        if color:
            body = f'<span style="color:{color}">{body}</span>'
        self.log_view.appendHtml(f'<span style="color:#9ca3af">{stamp}</span>&nbsp;{body}')

    def _update_plots(self) -> None:
        if self.pause_check.isChecked():
            return
        window = self.window_box.currentData()
        d = self.buffer.window(window)
        self.c_ref.setData(d["t"], d["p_ref"])
        self.c_pos.setData(d["t"], d["pos"])
        self.c_vel.setData(d["t"], d["vel"])
        self.c_torque.setData(d["t"], d["torque"])
        self.p_pos.setXRange(-window, 0, padding=0)

    def _update_status(self) -> None:
        self.tm_rate, self.tm_count = self.tm_count, 0
        if not self.link.is_open:
            self.conn_label.setText("Не подключено")
            return
        text = f"{self.port}: "
        if time.monotonic() - self.last_tm_time > TELEMETRY_STALE_S:
            text += "нет телеметрии"
            self.mode_label.setText("нет телеметрии")
            self.mode_label.setStyleSheet("font-weight: bold; color: #6b7280;")
        else:
            text += f"телеметрия {self.tm_rate} Гц"
        if self.recorder is not None:
            text += f", записано {self.recorder.rows} строк"
        self.conn_label.setText(text)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.link.is_open:
            self._disconnect()
        event.accept()
