"""Буфер телеметрии для графиков, сводка по ходам, запись в CSV."""

from __future__ import annotations

import csv
import math
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np

from .protocol import MoveResult, Telemetry


class TelemetryBuffer:
    """Последние maxlen строк телеметрии (100 Гц * 60 с по умолчанию)."""

    def __init__(self, maxlen: int = 6000) -> None:
        self._rows: deque[Telemetry] = deque(maxlen=maxlen)

    def __len__(self) -> int:
        return len(self._rows)

    def append(self, tm: Telemetry) -> None:
        # Mega перезагрузилась: время пошло заново, старые точки не нужны.
        if self._rows and tm.t_ms < self._rows[-1].t_ms:
            self._rows.clear()
        self._rows.append(tm)

    def clear(self) -> None:
        self._rows.clear()

    @property
    def last(self) -> Telemetry | None:
        return self._rows[-1] if self._rows else None

    def window(self, seconds: float) -> dict[str, np.ndarray]:
        """Точки за последние seconds; t в секундах, 0 — последняя точка."""
        if not self._rows:
            empty = np.empty(0)
            return {k: empty for k in ("t", "p_ref", "pos", "vel", "torque")}
        t_last = self._rows[-1].t_ms
        rows = [r for r in self._rows if t_last - r.t_ms <= seconds * 1000]
        return {
            "t": np.array([(r.t_ms - t_last) / 1000 for r in rows]),
            # Уставка имеет смысл только при управлении по позиции (H, M, V).
            "p_ref": np.array([r.p_ref_deg if r.mode in "HMV" else np.nan
                               for r in rows]),
            "pos": np.array([r.pos_deg for r in rows]),
            "vel": np.array([r.vel_dps for r in rows]),
            "torque": np.array([r.torque_nm for r in rows]),
        }


@dataclass(frozen=True)
class MoveSummary:
    count: int
    error_mean: float
    error_std: float  # СКО, 0 при одном ходе
    error_abs_max: float
    lag_max: float
    torque_peak: float


def summarize(moves: list[MoveResult]) -> MoveSummary | None:
    if not moves:
        return None
    errors = [m.error_deg for m in moves]
    mean = sum(errors) / len(errors)
    std = (
        math.sqrt(sum((e - mean) ** 2 for e in errors) / (len(errors) - 1))
        if len(errors) > 1
        else 0.0
    )
    return MoveSummary(
        count=len(moves),
        error_mean=mean,
        error_std=std,
        error_abs_max=max(abs(e) for e in errors),
        lag_max=max(m.lag_deg for m in moves),
        torque_peak=max(m.torque_peak_nm for m in moves),
    )


MOVE_COLUMNS = [
    "leg", "legs", "from_deg", "to_deg", "duration_s",
    "error_deg", "lag_deg", "torque_peak_nm",
]


def save_moves(path: Path, moves: list[MoveResult]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(MOVE_COLUMNS)
        for m in moves:
            w.writerow([getattr(m, c) if getattr(m, c) is not None else ""
                        for c in MOVE_COLUMNS])


class CsvRecorder:
    """Пишет телеметрию в CSV; pc_time — время ПК в ISO 8601."""

    COLUMNS = [
        "pc_time", "t_ms", "mode", "p_ref_deg", "pos_deg", "vel_dps",
        "torque_nm", "temp_c", "error",
    ]

    def __init__(self, path: Path) -> None:
        self.path = path
        self.rows = 0
        self._file = path.open("w", newline="", encoding="utf-8")
        self._writer = csv.writer(self._file)
        self._writer.writerow(self.COLUMNS)

    def write(self, tm: Telemetry) -> None:
        self._writer.writerow([
            datetime.now().isoformat(timespec="milliseconds"), tm.t_ms,
            tm.mode, tm.p_ref_deg, tm.pos_deg, tm.vel_dps, tm.torque_nm,
            tm.temp_c, tm.error,
        ])
        self.rows += 1

    def close(self) -> None:
        self._file.close()
