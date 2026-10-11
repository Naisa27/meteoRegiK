"""Расчёт сумм осадков (корректировка 2026-10-11): суточная сумма из сырых
данных и пакетный пересчёт всех сумм станции.

Суточная сумма.
- ГГ в приоритете над rp5, как у температуры (calculations/temperature.py):
  в файле ГГ уже суточная сумма. Пустое значение ГГ — **пропуск**, а не ноль
  (решение пользователя 2026-10-11): в файле Сухиничей 418 дней с
  температурой, но без осадков, почти все в 1975–1976 годах, — нулём они
  занизили бы суммы этих лет и норму. Если за такой день есть rp5 — берётся
  он.
- rp5 — сумма **основных сроков**, а не всех сроков суток. В выгрузках rp5
  осадки записаны четыре раза в сутки (например, в 06, 09, 18 и 21 ч), и
  каждое значение — сумма за 12 часов (столбец tR): это два ряда,
  перекрывающих друг друга во времени. Сумма по всем срокам даёт вдвое
  больше выпавшего — проверено на Сухиничах: 2016 год — 1516 мм по всем
  срокам, 781 мм по срокам 09 и 21, 782 мм в файле ГГ.
  Ряд — это сроки с одним остатком часа от деления на 12 (09 и 21 — ряд 9).
  Основной ряд станции определяется по её данным: тот, что заполнен чаще
  (main_series). Часы зашить нельзя: rp5 отдаёт местное время, и у станции
  другого часового пояса основные сроки другие.
  Если в сутках у основного ряда записей нет — берётся другой 12-часовой
  ряд этих суток; если 12-часовых записей нет вовсе (редкие сутки с tR = 24
  или 6) — сумма всех записей с осадками.
  Текст ("Осадков нет", "Следы осадков") = 0. Пустая ячейка в срок, когда
  осадки не измеряют (все сроки, кроме четырёх), ничего не значит и в сумму
  не входит. Если же за сутки нет ни одной записи об осадках — это пропуск:
  наблюдений осадков в этот день не было.
- За день нет значения ни в ГГ, ни в rp5 — суммы нет: день пропущен, а не
  сухой.

Декада, месяц, год — суммы суточных значений. Месяц через декады дал бы то
же число: декады делят месяц без остатка.
"""

from __future__ import annotations

from calendar import monthrange
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

from sqlalchemy import Integer, cast, delete, func, select
from sqlalchemy.engine import Connection, Engine

from meteoregik.calculations.temperature import decade_bounds
from meteoregik.db.schema import computed_indicators, gg_readings, rp5_readings

PARAMETER = "R"
STAT_SUM = "sum"
# Осадки измеряют с точностью 0.1 мм; округление убирает хвосты сложения
ROUND_DIGITS = 1
SERIES_PERIOD_HOURS = 12

Stats = dict[str, float | None]


@dataclass(frozen=True)
class Rp5Precipitation:
    """Запись rp5 об осадках: час срока (местное время), значение столбца
    RRR как в файле (число текстом или текст) и за сколько часов оно (tR)."""

    hour: int
    amount: str | float | None
    period_hours: float | None


def to_mm(amount: str | float | None) -> float:
    """Количество осадков числом; пустое и текст — 0."""
    if amount is None:
        return 0.0
    if isinstance(amount, (int, float)):
        return float(amount)
    try:
        return float(amount.strip().replace(",", "."))
    except ValueError:
        return 0.0


def _series(hour: int) -> int:
    return hour % SERIES_PERIOD_HOURS


def _is_twelve_hourly(record: Rp5Precipitation) -> bool:
    return record.amount is not None and record.period_hours == SERIES_PERIOD_HOURS


def pick_main_series(counts: dict[int, int]) -> int | None:
    """Основной ряд по числу записей каждого ряда; при равенстве — меньший
    номер (выбор должен быть один и тот же при каждом пересчёте)."""
    if not counts:
        return None
    return min(counts, key=lambda series: (-counts[series], series))


def rp5_daily_sum(records: Iterable[Rp5Precipitation], main: int | None) -> float | None:
    """Сумма осадков за сутки по записям rp5 этих суток (правила — в описании
    модуля). Записей с осадками нет — None: осадки в этот день не наблюдались."""
    filled = [record for record in records if record.amount is not None]
    if not filled:
        return None
    twelve_hourly = [record for record in filled if _is_twelve_hourly(record)]
    if not twelve_hourly:
        chosen = filled
    else:
        present = Counter(_series(record.hour) for record in twelve_hourly)
        series = main if main in present else pick_main_series(present)
        chosen = [record for record in twelve_hourly if _series(record.hour) == series]
    return round(sum(to_mm(record.amount) for record in chosen), ROUND_DIGITS)


def main_series(conn: Connection, wmo_index: int) -> int | None:
    """Основной ряд сроков станции по всем её данным rp5; None — 12-часовых
    записей об осадках у станции нет."""
    hour = cast(func.strftime("%H", rp5_readings.c.obs_time), Integer)
    rows = conn.execute(
        select(hour, func.count())
        .where(
            rp5_readings.c.wmo_index == wmo_index,
            rp5_readings.c.RRR_precipitation.is_not(None),
            rp5_readings.c.tR_precipitation_period == SERIES_PERIOD_HOURS,
        )
        .group_by(hour)
    ).all()
    counts: Counter[int] = Counter()
    for obs_hour, count in rows:
        counts[_series(int(obs_hour))] += count
    return pick_main_series(counts)


def daily_sum(engine: Engine, wmo_index: int, day: date, main: int | None) -> tuple[float | None, str | None]:
    """Сумма осадков за сутки и файл, из которого она взята; (None, None) —
    данных об осадках за день нет. main — основной ряд сроков станции
    (main_series): он один на весь пересчёт, поэтому считается вызывающим
    кодом, а не на каждый день.

    Файл возвращается вместе с суммой, а не берётся общим для дня: при
    пустых осадках в ГГ температура дня — из ГГ, а осадки — из rp5."""
    with engine.connect() as conn:
        gg_row = conn.execute(
            select(gg_readings.c.rrr_precipitation, gg_readings.c.source_file).where(
                gg_readings.c.wmo_index == wmo_index, gg_readings.c.obs_date == day
            )
        ).first()
        if gg_row is not None and gg_row[0] is not None:
            return round(gg_row[0], ROUND_DIGITS), gg_row[1]
        rows = conn.execute(
            select(
                rp5_readings.c.obs_time,
                rp5_readings.c.RRR_precipitation,
                rp5_readings.c.tR_precipitation_period,
                rp5_readings.c.source_file,
            ).where(rp5_readings.c.wmo_index == wmo_index, rp5_readings.c.obs_date == day)
        ).all()
    records = [Rp5Precipitation(obs_time.hour, amount, period) for obs_time, amount, period, _ in rows]
    value = rp5_daily_sum(records, main)
    return (value, rows[0][3]) if value is not None else (None, None)


def aggregate(day_values: Iterable[float]) -> float | None:
    """Сумма за декаду, месяц или год по суточным суммам; дней нет — None."""
    values = list(day_values)
    return round(sum(values), ROUND_DIGITS) if values else None


def _period_rows(days: dict[date, float]) -> list[dict[str, object]]:
    """Строки декад, месяцев и лет по суточным суммам (без дат вне days)."""
    totals: dict[tuple[str, date, date], list[float]] = {}
    for day, value in days.items():
        month_end = date(day.year, day.month, monthrange(day.year, day.month)[1])
        for key in (
            ("decade", *decade_bounds(day)),
            ("month", date(day.year, day.month, 1), month_end),
            ("year", date(day.year, 1, 1), date(day.year, 12, 31)),
        ):
            totals.setdefault(key, []).append(value)
    return [
        {"period_type": period_type, "period_start": start, "period_end": end, "value": aggregate(values),
         "source_file": None}
        for (period_type, start, end), values in totals.items()
    ]


def rebuild_station(conn: Connection, wmo_index: int) -> int:
    """Пересчитать и сохранить все суммы осадков станции одним проходом по
    её сырым данным; возвращает число дней с суммой.

    Для данных, залитых до появления параметра (миграция базы): пересчёт
    диапазона (calculations/recompute.py) идёт по дням отдельными запросами и
    на архиве в десятки лет занял бы минуты на станцию. Правила суточной
    суммы — те же функции, что и там."""
    days: dict[date, float] = {}
    sources: dict[date, str | None] = {}
    rp5_sources: dict[date, str | None] = {}

    rp5_rows = conn.execute(
        select(
            rp5_readings.c.obs_date,
            rp5_readings.c.obs_time,
            rp5_readings.c.RRR_precipitation,
            rp5_readings.c.tR_precipitation_period,
            rp5_readings.c.source_file,
        ).where(rp5_readings.c.wmo_index == wmo_index)
    ).all()
    main = main_series(conn, wmo_index)
    by_day: dict[date, list[Rp5Precipitation]] = {}
    for obs_date, obs_time, amount, period, source_file in rp5_rows:
        by_day.setdefault(obs_date, []).append(Rp5Precipitation(obs_time.hour, amount, period))
        rp5_sources.setdefault(obs_date, source_file)
    for day, records in by_day.items():
        value = rp5_daily_sum(records, main)
        if value is not None:
            days[day] = value
            sources[day] = rp5_sources[day]

    # ГГ — после rp5: у дня, который есть в обоих источниках, побеждает ГГ
    gg_rows = conn.execute(
        select(gg_readings.c.obs_date, gg_readings.c.rrr_precipitation, gg_readings.c.source_file).where(
            gg_readings.c.wmo_index == wmo_index
        )
    ).all()
    for obs_date, amount, source_file in gg_rows:
        if amount is None:
            continue  # пропуск в ГГ: остаётся сумма rp5, если она есть
        days[obs_date] = round(amount, ROUND_DIGITS)
        sources[obs_date] = source_file

    rows: list[dict[str, object]] = [
        {"period_type": "day", "period_start": day, "period_end": day, "value": value, "source_file": sources[day]}
        for day, value in days.items()
    ]
    rows += _period_rows(days)

    conn.execute(
        delete(computed_indicators).where(
            computed_indicators.c.wmo_index == wmo_index,
            computed_indicators.c.parameter == PARAMETER,
            computed_indicators.c.stat_type == STAT_SUM,
        )
    )
    if rows:
        key = {"wmo_index": wmo_index, "parameter": PARAMETER, "stat_type": STAT_SUM}
        conn.execute(computed_indicators.insert(), [{**key, **row} for row in rows])
    return len(days)


def stations_with_readings(conn: Connection) -> list[int]:
    """Станции, по которым есть сырые данные хотя бы одного формата."""
    found: set[int] = set()
    for table in (rp5_readings, gg_readings):
        found.update(
            wmo for wmo in conn.execute(select(table.c.wmo_index).distinct()).scalars() if wmo is not None
        )
    return sorted(found)
