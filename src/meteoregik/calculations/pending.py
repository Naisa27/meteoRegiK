"""Устойчивая очередь пересчёта и пакетная проверка наличия агрегатов без Qt."""
from __future__ import annotations

from calendar import monthrange
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import and_, case, delete, or_, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.engine import Connection, Engine

from meteoregik.calculations.precipitation import season_bounds
from meteoregik.calculations.temperature import decade_bounds
from meteoregik.db.schema import computed_indicators, gg_readings, pending_recomputations, rp5_readings


@dataclass(frozen=True)
class RecomputeRange:
    wmo_index: int
    start: date
    end: date
    periods_only: bool = False


@dataclass(frozen=True)
class PendingDay:
    day: date
    revision: int
    auto_accept: bool
    periods_only: bool
    missing_only: bool


def _invalidate(conn: Connection, wmo_index: int, days: list[date]) -> None:
    """Сбой до запуска расчёта не должен оставлять нормы ложно актуальными."""
    if days:
        from meteoregik.calculations.anomalies import mark_stale
        from meteoregik.calculations.transitions import mark_stale as mark_transitions_stale
        mark_stale(conn, wmo_index)
        mark_transitions_stale(conn, wmo_index, min(days), max(days))


def _days(start: date, end: date) -> Iterable[date]:
    for offset in range((end - start).days + 1):
        yield start + timedelta(days=offset)


def mark_pending(
    conn: Connection, wmo_index: int, days: Iterable[date], *,
    auto_accept: bool = False, periods_only: bool = False,
) -> None:
    """Вызывать в транзакции изменения наблюдений; последняя правка повышает ревизию."""
    rows = [{"wmo_index": wmo_index, "obs_date": day, "revision": 1,
             "auto_accept": auto_accept, "periods_only": periods_only, "missing_only": False}
            for day in sorted(set(days))]
    if not rows:
        return
    table = pending_recomputations
    stmt = insert(table)
    conn.execute(stmt.on_conflict_do_update(
        index_elements=[table.c.wmo_index, table.c.obs_date],
        set_={"revision": table.c.revision + 1,
              "auto_accept": case((stmt.excluded.periods_only, table.c.auto_accept),
                                  else_=stmt.excluded.auto_accept),
              "periods_only": table.c.periods_only & stmt.excluded.periods_only,
              "missing_only": case((stmt.excluded.periods_only, table.c.missing_only), else_=False)},
    ), rows)
    _invalidate(conn, wmo_index, [row["obs_date"] for row in rows])


def mark_range(
    conn: Connection, wmo_index: int, start: date, end: date, *,
    auto_accept: bool = False, periods_only: bool = False,
) -> None:
    mark_pending(conn, wmo_index, _days(start, end), auto_accept=auto_accept, periods_only=periods_only)


def _ensure_pending(
    conn: Connection, wmo_index: int, days: Iterable[date], *, periods_only: bool, missing_only: bool = False,
) -> None:
    """Проверка не является новой правкой: уже поставленные решения не сбрасываются."""
    rows = [{"wmo_index": wmo_index, "obs_date": day, "revision": 1,
             "auto_accept": False, "periods_only": periods_only, "missing_only": missing_only}
            for day in sorted(set(days))]
    if not rows:
        return
    table = pending_recomputations
    stmt = insert(table)
    if periods_only:
        conn.execute(stmt.on_conflict_do_nothing(index_elements=[table.c.wmo_index, table.c.obs_date]), rows)
    else:
        conn.execute(stmt.on_conflict_do_update(
            index_elements=[table.c.wmo_index, table.c.obs_date],
            set_={"revision": table.c.revision + 1, "periods_only": False, "missing_only": missing_only},
            where=table.c.periods_only.is_(True),
        ), rows)


def begin_recomputation(
    conn: Connection, wmo_index: int, start: date, end: date, *, periods_only: bool = False,
) -> tuple[PendingDay, ...]:
    """Снимок берётся до работы, а очищается лишь после всех производных периодов."""
    _ensure_pending(conn, wmo_index, _days(start, end), periods_only=periods_only)
    return tuple(PendingDay(row.obs_date, row.revision, row.auto_accept, row.periods_only, row.missing_only)
                 for row in conn.execute(select(pending_recomputations).where(
                     pending_recomputations.c.wmo_index == wmo_index,
                     pending_recomputations.c.obs_date.between(start, end),
                 )).all())


def finish_recomputation(conn: Connection, wmo_index: int, snapshot: Iterable[PendingDay]) -> None:
    """Более позднюю ревизию завершение старого расчёта не подтверждает."""
    table = pending_recomputations
    for item in snapshot:
        conn.execute(delete(table).where(table.c.wmo_index == wmo_index,
                                        table.c.obs_date == item.day, table.c.revision == item.revision))


DailyKey = tuple[date, str, str]
PeriodKey = tuple[str, date, date, str, str]


def _raw_daily_keys(conn: Connection, wmo_index: int) -> set[DailyKey]:
    """Нужность значения определяется наличием измерений, а не нулевым заполнением."""
    result: set[DailyKey] = set()
    gg = {row.obs_date: row for row in conn.execute(select(
        gg_readings.c.obs_date, gg_readings.c.t_avg, gg_readings.c.t_min,
        gg_readings.c.t_max, gg_readings.c.rrr_precipitation,
    ).where(gg_readings.c.wmo_index == wmo_index)).all()}
    for day, row in gg.items():
        for stat, value in (("avg", row.t_avg), ("min", row.t_min), ("max", row.t_max)):
            if value is not None:
                result.add((day, "T", stat))
        if row.rrr_precipitation is not None:
            result.add((day, "R", "sum"))
    for row in conn.execute(select(rp5_readings.c.obs_date, rp5_readings.c.T_temperature,
                                  rp5_readings.c.RRR_precipitation).where(
                                      rp5_readings.c.wmo_index == wmo_index)).all():
        if row.obs_date not in gg and row.T_temperature is not None:
            result.update((row.obs_date, "T", stat) for stat in ("avg", "min", "max"))
        if row.RRR_precipitation is not None:
            result.add((row.obs_date, "R", "sum"))
    return result


def _ancestor_keys(day: date, parameter: str, stat: str) -> Iterable[PeriodKey]:
    yield ("decade", *decade_bounds(day), parameter, stat)
    yield ("month", date(day.year, day.month, 1), date(day.year, day.month, monthrange(day.year, day.month)[1]), parameter, stat)
    yield ("year", date(day.year, 1, 1), date(day.year, 12, 31), parameter, stat)
    if parameter == "R":
        yield ("season", *season_bounds(day), parameter, stat)


def _find_missing(conn: Connection, wmo_index: int) -> tuple[set[date], set[date]]:
    table = computed_indicators
    rows = conn.execute(select(table.c.period_type, table.c.period_start, table.c.period_end,
                               table.c.parameter, table.c.stat_type).where(
        table.c.wmo_index == wmo_index,
        or_(and_(table.c.parameter == "T", table.c.stat_type.in_(("avg", "min", "max"))),
            and_(table.c.parameter == "R", table.c.stat_type == "sum")),
        table.c.value.is_not(None),
    )).all()
    present = set(rows)
    raw = _raw_daily_keys(conn, wmo_index)
    daily = {(start, param, stat) for period, start, end, param, stat in present
             if period == "day" and start == end}
    missing_days = {day for day, param, stat in raw if (day, param, stat) not in daily}
    ancestors: dict[PeriodKey, date] = {}
    for day, param, stat in sorted(raw | daily):
        for key in _ancestor_keys(day, param, stat):
            ancestors.setdefault(key, day)
    missing_periods = {anchor for key, anchor in ancestors.items() if key not in present}
    return missing_days, missing_periods


def plan_recomputation(engine: Engine, wmo_indexes: Iterable[int]) -> tuple[RecomputeRange, ...]:
    """Пакетно проверяет весь архив станции; здоровые данные не пересчитываются."""
    result: list[RecomputeRange] = []
    with engine.begin() as conn:
        for wmo_index in sorted(set(wmo_indexes)):
            missing_days, missing_periods = _find_missing(conn, wmo_index)
            _ensure_pending(conn, wmo_index, missing_days, periods_only=False, missing_only=True)
            _ensure_pending(conn, wmo_index, missing_periods, periods_only=True)
            rows = conn.execute(select(pending_recomputations.c.obs_date,
                                       pending_recomputations.c.periods_only).where(
                pending_recomputations.c.wmo_index == wmo_index,
            ).order_by(pending_recomputations.c.obs_date)).all()
            for day, periods_only in rows:
                if (result and result[-1].wmo_index == wmo_index
                        and result[-1].periods_only == periods_only
                        and day == result[-1].end + timedelta(days=1)):
                    previous = result[-1]
                    result[-1] = RecomputeRange(wmo_index, previous.start, day, periods_only)
                else:
                    result.append(RecomputeRange(wmo_index, day, day, periods_only))
    return tuple(result)
