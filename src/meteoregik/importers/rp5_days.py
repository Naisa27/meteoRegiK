"""Сравнение полных суточных наборов rp5 до записи в рабочую базу."""
from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import date, time
from typing import Literal, cast

from sqlalchemy import select
from sqlalchemy.engine import Connection

from meteoregik.calculations import precipitation
from meteoregik.db.schema import computed_indicators, gg_readings, pending_recomputations, rp5_readings
from meteoregik.importers.base import Rp5DayChoiceRequiredError, Rp5DayConflict

DayChoice = Literal['old', 'new']
DayResolver = Callable[[Rp5DayConflict], DayChoice]
Row = Mapping[str, object]
Stats = dict[tuple[str, str], float | None]
STAT_KEYS = (('T', 'avg'), ('T', 'min'), ('T', 'max'), ('R', 'sum'))


@dataclass(frozen=True)
class DayImportPlan:
    """Решения собраны до изменения raw; в план входят только выбранные сутки."""

    rows: list[dict[str, object]]
    replace_dates: tuple[date, ...]
    accepted_dates: tuple[date, ...]
    replaced_rows: int
    old_main: int | None
    new_main: int | None
    archive_start: date | None
    archive_end: date | None


def _series_counts(rows: Iterable[Row]) -> Counter[int]:
    return Counter(
        cast(time, row['obs_time']).hour % precipitation.SERIES_PERIOD_HOURS
        for row in rows
        if row.get('RRR_precipitation') is not None
        and row.get('tR_precipitation_period') == precipitation.SERIES_PERIOD_HOURS
    )


def _main(counts: Counter[int]) -> int | None:
    return precipitation.pick_main_series({series: count for series, count in counts.items() if count > 0})


def _values(rows: list[Row], fields: tuple[str, ...], text_fields: frozenset[str]) -> dict[time, tuple[object, ...]]:
    """Игнорируем имя файла: равные наблюдения не заменяются ради provenance."""
    def normalized(value: object, field: str) -> object:
        if value is None:
            return None
        return str(value) if field in text_fields else float(value)
    return {
        cast(time, row['obs_time']): tuple(normalized(row.get(field), field) for field in fields)
        for row in rows
    }


def _filled_times(rows: list[Row]) -> tuple[time, ...]:
    """Как в полноте исходных данных: пустая температура не даёт полного срока."""
    return tuple(sorted(cast(time, row['obs_time']) for row in rows if row.get('T_temperature') is not None))


def candidate_stats(rows: list[Row], gg: Row | None, main: int | None) -> Stats:
    """Кандидат по тем же суточным правилам, что temperature/precipitation."""
    result: Stats = {}
    if gg is not None:
        for stat, field in (('avg', 't_avg'), ('min', 't_min'), ('max', 't_max')):
            value = cast(float | None, gg[field])
            result[('T', stat)] = round(value, 2) if value is not None else None
    else:
        temps = [cast(float, row['T_temperature']) for row in rows if row.get('T_temperature') is not None]
        result.update({
            ('T', 'avg'): round(sum(temps) / len(temps), 2) if temps else None,
            ('T', 'min'): round(min(temps), 2) if temps else None,
            ('T', 'max'): round(max(temps), 2) if temps else None,
        })
    gg_rain = cast(float | None, gg['rrr_precipitation']) if gg is not None else None
    if gg_rain is not None:
        result[('R', 'sum')] = round(gg_rain, precipitation.ROUND_DIGITS)
    else:
        result[('R', 'sum')] = precipitation.rp5_daily_sum([
            precipitation.Rp5Precipitation(
                cast(time, row['obs_time']).hour,
                cast(str | float | None, row.get('RRR_precipitation')),
                cast(float | None, row.get('tR_precipitation_period')),
            ) for row in rows
        ], main)
    return result


def plan_days(
    conn: Connection,
    wmo_index: int,
    station_name: str,
    incoming: list[dict[str, object]],
    value_fields: tuple[str, ...],
    text_fields: frozenset[str],
    confirm: DayResolver | None,
) -> DayImportPlan:
    """Bulk-снимок исключает запрос на каждую дату больших повторных файлов."""
    new_by_day: dict[date, list[Row]] = defaultdict(list)
    for row in incoming:
        new_by_day[cast(date, row['obs_date'])].append(row)
    first, last = min(new_by_day), max(new_by_day)
    # Основной осадочный ряд определяется по всему архиву, не только по файлу.
    existing_rows = conn.execute(select(rp5_readings).where(rp5_readings.c.wmo_index == wmo_index)).mappings().all()
    old_by_day: dict[date, list[Row]] = defaultdict(list)
    for row in existing_rows:
        old_by_day[row['obs_date']].append(row)
    gg_by_day = {
        row['obs_date']: row for row in conn.execute(select(gg_readings).where(
            gg_readings.c.wmo_index == wmo_index,
            gg_readings.c.obs_date.between(first, last),
        )).mappings()
    }
    saved: dict[date, Stats] = defaultdict(dict)
    for row in conn.execute(select(
        computed_indicators.c.period_start, computed_indicators.c.parameter,
        computed_indicators.c.stat_type, computed_indicators.c.value,
    ).where(
        computed_indicators.c.wmo_index == wmo_index,
        computed_indicators.c.period_type == 'day',
        computed_indicators.c.period_start.between(first, last),
        computed_indicators.c.parameter.in_(('T', 'R')),
        computed_indicators.c.stat_type.in_(('avg', 'min', 'max', 'sum')),
    )).mappings():
        key = (row['parameter'], row['stat_type'])
        if key in STAT_KEYS:
            saved[row['period_start']][key] = row['value']
    accepted_before = set(conn.scalars(select(pending_recomputations.c.obs_date).where(
        pending_recomputations.c.wmo_index == wmo_index,
        pending_recomputations.c.obs_date.between(first, last),
        pending_recomputations.c.auto_accept.is_(True),
    )))
    counts = _series_counts(existing_rows)
    old_main = _main(counts)
    # Кандидатный основной ряд предполагает замены всего файла. При отказах
    # возвращаем вклад сохранённого дня; конечный ряд определяет пересчёт.
    for day, rows in new_by_day.items():
        counts.subtract(_series_counts(old_by_day.get(day, [])))
        counts.update(_series_counts(rows))
    write_rows: list[dict[str, object]] = []
    replace_dates: list[date] = []
    accepted_dates: list[date] = []
    replaced_rows = 0
    for day, new_rows in sorted(new_by_day.items()):
        old_rows = old_by_day.get(day, [])
        if not old_rows:
            write_rows.extend(cast(list[dict[str, object]], new_rows))
            accepted_dates.append(day)
            continue
        old_times, new_times = _filled_times(old_rows), _filled_times(new_rows)
        same_raw = _values(old_rows, value_fields, text_fields) == _values(new_rows, value_fields, text_fields)
        new_stats = candidate_stats(new_rows, gg_by_day.get(day), _main(counts))
        old_saved = saved.get(day, {})
        # Отсутствующий кэш достраивается автоматически: это не конфликт.
        differs = any(
            old_value is not None and (
                new_stats[key] is None or abs(old_value - cast(float, new_stats[key])) > 1e-6
            ) for key, old_value in old_saved.items()
        )
        if same_raw and (not differs or day in accepted_before):
            continue
        if len(new_times) > len(old_times):
            choice = 'new'
        else:
            old_stats = (
                {key: old_saved.get(key) for key in STAT_KEYS} if old_saved
                else candidate_stats(old_rows, gg_by_day.get(day), old_main)
            )
            conflict = Rp5DayConflict(wmo_index, station_name, day, old_times, new_times, old_stats, new_stats)
            if confirm is None:
                raise Rp5DayChoiceRequiredError(conflict)
            choice = confirm(conflict)
            if choice not in ('old', 'new'):
                raise ValueError('Недопустимый выбор набора сроков rp5')
        if choice == 'old':
            counts.subtract(_series_counts(new_rows))
            counts.update(_series_counts(old_rows))
            continue
        accepted_dates.append(day)
        if not same_raw:
            replace_dates.append(day)
            replaced_rows += len(old_rows)
            write_rows.extend(cast(list[dict[str, object]], new_rows))
    all_days = set(old_by_day) | set(new_by_day)
    return DayImportPlan(write_rows, tuple(replace_dates), tuple(accepted_dates), replaced_rows,
                         old_main, _main(counts), min(all_days) if all_days else None,
                         max(all_days) if all_days else None)
