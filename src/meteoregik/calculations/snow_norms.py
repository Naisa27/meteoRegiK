"""Норма числа снежных дней при проектном допущении «пропуск высоты = 0».

Счётные месячные значения переводятся в долю дней, усредняются по годам,
затем умножаются на расчётную длину месяца (для февраля 28.25). Декады —
проектное расширение того же подхода. Полнота означает охват периода rp5,
а не документально подтверждённое наблюдение снега в каждый день.
"""

from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import date
from statistics import fmean

from sqlalchemy import select
from sqlalchemy.engine import Connection

from meteoregik.calculations.snow import archive_bounds, period_bounds
from meteoregik.db.schema import (
    computed_indicators, norm_bases, period_completeness, snow_norm_bases, station_norms,
)

PARAMETER = "S"
BASE_FIRST = 1991
BASE_LAST = 2020
MIN_YEARS = 24
ASSUMPTION = "По архиву rp5; пропуски высоты приняты за отсутствие снега."


@dataclass(frozen=True)
class SnowNormBase:
    standard: bool
    first_year: int
    last_year: int
    years: int

    @property
    def text(self) -> str:
        period = "1991–2020" if self.standard else f"по архиву {self.first_year}–{self.last_year}"
        return f"{period}, {self.years} лет. {ASSUMPTION}"


def _key(kind: str, start: date) -> tuple[str, int, int]:
    return kind, start.month, (start.day - 1) // 10 + 1 if kind == "decade" else 0


def _length(kind: str, month: int, decade: int) -> float:
    length = 28.25 if month == 2 else float(monthrange(2001, month)[1])
    return (10.0 if decade < 3 else length - 20) if kind == "decade" else length


def rebuild(conn: Connection, wmo_index: int) -> bool:
    """Сохранить S-нормы после общей очистки anomalies в той же транзакции."""
    bounds = archive_bounds(conn, wmo_index)
    if bounds is None:
        return False
    first, last = bounds
    days = dict(conn.execute(select(computed_indicators.c.period_start, computed_indicators.c.value).where(
        computed_indicators.c.wmo_index == wmo_index,
        computed_indicators.c.parameter == PARAMETER,
        computed_indicators.c.stat_type == "sum",
        computed_indicators.c.period_type == "day",
        computed_indicators.c.period_start.between(first, last),
        computed_indicators.c.value.is_not(None),
    )).all())
    if len(days) != (last - first).days + 1:
        raise RuntimeError(
            "Расчёт снежного покрова отсутствует или неполон во всём архиве станции. "
            "Повторно импортируйте файл этой станции для автоматического восстановления расчётов."
        )
    periods: dict[tuple[str, date, date], tuple[float, int]] = {}
    for day, value in days.items():
        for period in period_bounds(day):
            total, count = periods.get(period, (0.0, 0))
            periods[period] = total + value, count + 1
    samples: dict[tuple[str, int, int], list[tuple[int, float]]] = {}
    complete: dict[tuple[str, date, date], bool] = {}
    for (kind, start, end), (total, count) in periods.items():
        length = (end - start).days + 1
        full = first <= start and last >= end and count == length
        complete[kind, start, end] = full
        if full and kind in {"month", "decade"}:
            samples.setdefault(_key(kind, start), []).append((start.year, total / length))
    norms: dict[tuple[str, int, int], float] = {}
    bases: dict[tuple[str, int, int], SnowNormBase] = {}
    for key, available in samples.items():
        standard_samples = [(year, value) for year, value in available if BASE_FIRST <= year <= BASE_LAST]
        standard = len(standard_samples) >= MIN_YEARS
        selected = standard_samples if standard else available
        norms[key] = fmean(value for _, value in selected) * _length(*key)
        years = [year for year, _ in selected]
        bases[key] = SnowNormBase(standard, min(years), max(years), len(years))
    # Годовая и зимняя сезонная нормы — суммы месячных, а не средние сумм.
    if all(("month", month, 0) in norms for month in range(1, 13)):
        annual = sum(norms["month", month, 0] for month in range(1, 13))
        norms["year", 0, 0] = annual
        norms["winter_season", 7, 0] = annual
    key_values = {"wmo_index": wmo_index, "parameter": PARAMETER}
    if norms:
        conn.execute(station_norms.insert(), [dict(**key_values, period_type=kind, month=month,
            decade=decade, value=value) for (kind, month, decade), value in norms.items()])
    if bases:
        conn.execute(snow_norm_bases.insert(), [dict(wmo_index=wmo_index, period_type=kind,
            month=month, decade=decade, standard=base.standard, first_year=base.first_year,
            last_year=base.last_year, years=base.years) for (kind, month, decade), base in bases.items()])
    anomaly_rows = []
    completeness_rows = []
    for (kind, start, end), (total, count) in periods.items():
        norm_key = (kind, 0, 0) if kind == "year" else _key(kind, start)
        normal = norms.get(norm_key)
        if normal is not None:
            anomaly_rows.append(dict(**key_values, period_type=kind, period_start=start,
                period_end=end, stat_type="anomaly", value=round(total - normal, 6)))
        completeness_rows.append(dict(**key_values, period_type=kind, period_start=start,
            days_with_data=count, complete=complete[kind, start, end]))
    if anomaly_rows:
        conn.execute(computed_indicators.insert(), anomaly_rows)
    conn.execute(period_completeness.insert(), completeness_rows)
    all_bases = list(bases.values())
    conn.execute(norm_bases.insert().values(**key_values,
        standard=bool(all_bases) and all(base.standard for base in all_bases),
        first_year=min((base.first_year for base in all_bases), default=None),
        last_year=max((base.last_year for base in all_bases), default=None),
        years=min((base.years for base in all_bases), default=0)))
    return True


def load_bases(conn: Connection, wmo_index: int) -> dict[tuple[str, int, int], SnowNormBase]:
    return {(row.period_type, row.month, row.decade): SnowNormBase(
        row.standard, row.first_year, row.last_year, row.years)
        for row in conn.execute(select(snow_norm_bases).where(snow_norm_bases.c.wmo_index == wmo_index))}
