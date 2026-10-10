"""Нормы станции, полнота периодов и отклонения от нормы — расчёт и
хранение (решение пользователя 2026-10-10, раздел "Расчёт показателей").

Раньше всё это считалось на лету при каждом открытии дашборда и карты
(reports/dashboard_data.py::compute_dashboard), и карта ради этого читала
суточные значения всех станций. Теперь результат сохраняется:
- station_norms — нормы станции (12 месячных, декадные, годовая);
- computed_indicators, stat_type='anomaly' — отклонения декад, месяцев, лет;
- period_completeness — признак полного месяца и года;
- anomaly_state — когда и по какой базе нормы посчитано.

Когда считается. Не при заливке: норма зависит от всего архива станции, и
при заливке нескольких файлов подряд отклонения за все годы
пересчитывались бы после каждого. calculations/recompute.py при любом
изменении суточных значений только помечает станцию устаревшей (mark_stale —
одна запись). Считают ensure_fresh / recalculate_station: по кнопке в
"Расчёте показателей" и автоматически перед показом дашборда и карты.

Правила (не менялись при переносе из dashboard_data.py). Норма —
стандартная норма ВМО 1991–2020 (WMO-No. 1203), если в базе не меньше
MIN_NORM_BASE_YEARS полных лет, иначе средняя по всему архиву станции. Месяц
полный по правилу ВМО "3/5", декада — пропущено не больше
MAX_MISSING_DECADE_DAYS дней, год — если полны все 12 месяцев. Нормы — по
полным периодам базы; отклонение сохраняется для любого периода со
значением и нормой (и неполного — признак полноты хранится рядом, решает
тот, кто показывает).

compute_station_norms — единственное место, где эти правила записаны:
сохранённое и посчитанное на лету не могут разойтись.
"""

from __future__ import annotations

from calendar import monthrange
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date, datetime
from statistics import fmean

from sqlalchemy import delete, func, select
from sqlalchemy.engine import Connection, Engine

from meteoregik.calculations.temperature import decade_bounds
from meteoregik.db.schema import anomaly_state, computed_indicators, period_completeness, station_norms

PARAMETER = "T"
STAT_ANOMALY = "anomaly"
# Меняется при изменении правил расчёта: сохранённое со старой версией
# считается устаревшим и пересчитывается.
RULE_VERSION = 1

# Правило ВМО "3/5" для месячной средней (WMO-No. 1203): не больше 5
# пропущенных дней всего и не больше 3 подряд.
MAX_MISSING_DAYS = 5
MAX_MISSING_RUN = 3

# Базовый период нормы ВМО и минимум полных лет в нём (80 % из 30), чтобы
# норму можно было считать по нему, а не по всему архиву.
NORM_BASE_FIRST_YEAR = 1991
NORM_BASE_LAST_YEAR = 2020
MIN_NORM_BASE_YEARS = 24

# Декада (10–11 дней) считается полной, если пропущено не больше 2 дней —
# примерно та же доля, что 5 дней из 30 в правиле "3/5" для месяца.
MAX_MISSING_DECADE_DAYS = 2

MonthKey = tuple[int, int]  # (год, месяц)
DecadeKey = tuple[int, int]  # (месяц, первый день декады: 1, 11 или 21)


@dataclass(frozen=True)
class DayTemps:
    avg: float | None = None
    min: float | None = None
    max: float | None = None


@dataclass(frozen=True)
class NormInfo:
    """Годовая норма и по каким годам она посчитана (для подписи на экране)."""

    value: float
    first_year: int
    last_year: int
    year_count: int
    standard: bool  # True — база ВМО 1991–2020, False — откат на весь архив


@dataclass(frozen=True)
class StationNorms:
    """Нормы станции и полнота её месяцев и лет."""

    standard: bool = True  # отдельно от annual: месячные нормы бывают и без единого полного года
    months: dict[int, float] = field(default_factory=dict)
    decades: dict[DecadeKey, float] = field(default_factory=dict)
    annual: NormInfo | None = None
    complete_months: frozenset[MonthKey] = frozenset()
    complete_years: frozenset[int] = frozenset()


# --- расчёт (без БД) ---


def month_is_complete(days_with_data: set[int], days_in_month: int) -> bool:
    """Правило "3/5" по номерам дней месяца, за которые есть суточная средняя."""
    missing = 0
    run = 0
    for day in range(1, days_in_month + 1):
        if day in days_with_data:
            run = 0
            continue
        missing += 1
        run += 1
        if missing > MAX_MISSING_DAYS or run > MAX_MISSING_RUN:
            return False
    return True


def days_with_avg_by_month(days: dict[date, DayTemps]) -> dict[MonthKey, set[int]]:
    result: dict[MonthKey, set[int]] = {}
    for day, temps in days.items():
        if temps.avg is not None:
            result.setdefault((day.year, day.month), set()).add(day.day)
    return result


def decade_averages(days: dict[date, DayTemps]) -> dict[date, tuple[float, int, int]]:
    """Средняя каждой декады по суточным средним (как decade в
    calculations/temperature.py, с тем же округлением), число дней с данными
    и длина декады."""
    by_decade: dict[date, list[float]] = {}
    for day, temps in days.items():
        if temps.avg is not None:
            by_decade.setdefault(decade_bounds(day)[0], []).append(temps.avg)
    return {
        start: (round(fmean(values), 2), len(values), (decade_bounds(start)[1] - start).days + 1)
        for start, values in by_decade.items()
    }


def decade_is_complete(days_with_data: int, length: int) -> bool:
    return length - days_with_data <= MAX_MISSING_DECADE_DAYS


def _norm_base(
    complete_months: set[MonthKey], complete_years: set[int]
) -> tuple[bool, set[MonthKey], list[int]]:
    """По каким полным месяцам и годам считать норму: база ВМО 1991–2020,
    если в ней не меньше MIN_NORM_BASE_YEARS полных лет, иначе весь архив.
    Решение одно на станцию — месячные и годовая нормы из одной базы, иначе
    отклонения на соседних карточках отсчитывались бы от разного."""
    base_years = sorted(y for y in complete_years if NORM_BASE_FIRST_YEAR <= y <= NORM_BASE_LAST_YEAR)
    if len(base_years) >= MIN_NORM_BASE_YEARS:
        base_months = {key for key in complete_months if NORM_BASE_FIRST_YEAR <= key[0] <= NORM_BASE_LAST_YEAR}
        return True, base_months, base_years
    return False, complete_months, sorted(complete_years)


def compute_station_norms(
    days: dict[date, DayTemps], months: dict[MonthKey, float], years: dict[int, float]
) -> StationNorms:
    """Нормы и полнота по всей истории станции: суточные значения, месячные
    и годовые средние из computed_indicators."""
    present = days_with_avg_by_month(days)
    complete_months = {
        key for key in months if month_is_complete(present.get(key, set()), monthrange(*key)[1])
    }
    complete_years = {
        year for year in years if all((year, month) in complete_months for month in range(1, 13))
    }

    standard, norm_months, norm_years = _norm_base(complete_months, complete_years)
    month_norms: dict[int, float] = {}
    for month in range(1, 13):
        values = [months[key] for key in norm_months if key[1] == month]
        if values:
            month_norms[month] = fmean(values)

    annual = (
        NormInfo(fmean(years[y] for y in norm_years), norm_years[0], norm_years[-1], len(norm_years), standard)
        if norm_years
        else None
    )

    # Норма декады — по полным декадам тех же месяцев, что вошли в базу нормы
    decade_values: dict[DecadeKey, list[float]] = {}
    for start, (value, count, length) in decade_averages(days).items():
        if decade_is_complete(count, length) and (start.year, start.month) in norm_months:
            decade_values.setdefault((start.month, start.day), []).append(value)
    decade_norms = {key: fmean(values) for key, values in decade_values.items()}

    return StationNorms(
        standard, month_norms, decade_norms, annual, frozenset(complete_months), frozenset(complete_years)
    )


# --- чтение исходного для расчёта ---


def fetch_station_temperatures(
    engine: Engine, wmo_index: int
) -> tuple[dict[date, DayTemps], dict[MonthKey, float], dict[int, float]]:
    """Вся история станции: суточные avg/min/max, месячные и годовые
    средние. Вся — потому что норма считается по всей истории; для одной
    станции это десятки тысяч строк, не проблема для одного запроса."""
    stmt = select(
        computed_indicators.c.period_type,
        computed_indicators.c.period_start,
        computed_indicators.c.stat_type,
        computed_indicators.c.value,
    ).where(
        computed_indicators.c.wmo_index == wmo_index,
        computed_indicators.c.parameter == PARAMETER,
        computed_indicators.c.period_type.in_(("day", "month", "year")),
    )
    with engine.connect() as conn:
        rows = conn.execute(stmt).all()

    day_parts: dict[date, dict[str, float | None]] = {}
    months: dict[MonthKey, float] = {}
    years: dict[int, float] = {}
    for period_type, start, stat_type, value in rows:
        if period_type == "day":
            day_parts.setdefault(start, {})[stat_type] = value
        elif value is None or stat_type != "avg":
            continue
        elif period_type == "month":
            months[(start.year, start.month)] = value
        else:
            years[start.year] = value

    days = {day: DayTemps(parts.get("avg"), parts.get("min"), parts.get("max")) for day, parts in day_parts.items()}
    return days, months, years


# --- хранение ---


def mark_stale(conn: Connection, wmo_index: int) -> None:
    """Пометить станцию требующей расчёта: её суточные значения изменились.
    Сами нормы и отклонения остаются до пересчёта — читать их, пока станция
    помечена, нельзя (load_station_norms вернёт None)."""
    conn.execute(delete(anomaly_state).where(anomaly_state.c.wmo_index == wmo_index))


def is_stale(engine: Engine, wmo_index: int) -> bool:
    with engine.connect() as conn:
        version = conn.execute(
            select(anomaly_state.c.rule_version).where(anomaly_state.c.wmo_index == wmo_index)
        ).scalar_one_or_none()
    return version != RULE_VERSION


def _stations_with_days(conn: Connection) -> dict[int, tuple[date, date]]:
    """Станции с суточной средней и границы их архива."""
    stmt = (
        select(
            computed_indicators.c.wmo_index,
            func.min(computed_indicators.c.period_start),
            func.max(computed_indicators.c.period_start),
        )
        .where(
            computed_indicators.c.period_type == "day",
            computed_indicators.c.parameter == PARAMETER,
            computed_indicators.c.stat_type == "avg",
            computed_indicators.c.value.is_not(None),
        )
        .group_by(computed_indicators.c.wmo_index)
    )
    return {wmo: (first, last) for wmo, first, last in conn.execute(stmt)}


def _purge(conn: Connection, wmo_index: int) -> None:
    conn.execute(delete(anomaly_state).where(anomaly_state.c.wmo_index == wmo_index))
    conn.execute(
        delete(station_norms).where(station_norms.c.wmo_index == wmo_index, station_norms.c.parameter == PARAMETER)
    )
    conn.execute(
        delete(period_completeness).where(
            period_completeness.c.wmo_index == wmo_index, period_completeness.c.parameter == PARAMETER
        )
    )
    conn.execute(
        delete(computed_indicators).where(
            computed_indicators.c.wmo_index == wmo_index,
            computed_indicators.c.parameter == PARAMETER,
            computed_indicators.c.stat_type == STAT_ANOMALY,
        )
    )


def _month_bounds(year: int, month: int) -> tuple[date, date]:
    return date(year, month, 1), date(year, month, monthrange(year, month)[1])


def recalculate_station(engine: Engine, wmo_index: int) -> StationNorms | None:
    """Пересчитать и сохранить нормы, полноту и отклонения станции — целиком
    и одной транзакцией: норма общая на все годы, поэтому изменение одного
    дня может сдвинуть отклонения за весь архив. Нет суточных значений —
    сохранённое по станции удаляется, возвращается None."""
    days, months, years = fetch_station_temperatures(engine, wmo_index)
    present = days_with_avg_by_month(days)
    if not present:
        with engine.begin() as conn:
            _purge(conn, wmo_index)
        return None
    norms = compute_station_norms(days, months, years)

    norm_rows = [
        {"period_type": "month", "month": month, "decade": 0, "value": value}
        for month, value in norms.months.items()
    ]
    norm_rows += [
        {"period_type": "decade", "month": month, "decade": (first_day - 1) // 10 + 1, "value": value}
        for (month, first_day), value in norms.decades.items()
    ]
    if norms.annual is not None:
        norm_rows.append({"period_type": "year", "month": 0, "decade": 0, "value": norms.annual.value})

    anomaly_rows: list[dict[str, object]] = []

    def add_anomaly(period_type: str, start: date, end: date, value: float) -> None:
        anomaly_rows.append({"period_type": period_type, "period_start": start, "period_end": end, "value": value})

    for (year, month), value in months.items():
        if month in norms.months:
            add_anomaly("month", *_month_bounds(year, month), value - norms.months[month])
    if norms.annual is not None:
        for year, value in years.items():
            add_anomaly("year", date(year, 1, 1), date(year, 12, 31), value - norms.annual.value)
    for start, (value, _, _) in decade_averages(days).items():
        norm = norms.decades.get((start.month, start.day))
        if norm is not None:
            add_anomaly("decade", start, decade_bounds(start)[1], value - norm)

    days_by_year: dict[int, int] = {}
    for (year, _), month_days in present.items():
        days_by_year[year] = days_by_year.get(year, 0) + len(month_days)
    completeness_rows = [
        {"period_type": "month", "period_start": date(year, month, 1), "days_with_data": len(month_days),
         "complete": (year, month) in norms.complete_months}
        for (year, month), month_days in present.items()
    ]
    completeness_rows += [
        {"period_type": "year", "period_start": date(year, 1, 1), "days_with_data": count,
         "complete": year in norms.complete_years}
        for year, count in days_by_year.items()
    ]

    key = {"wmo_index": wmo_index, "parameter": PARAMETER}
    with engine.begin() as conn:
        _purge(conn, wmo_index)
        if norm_rows:
            conn.execute(station_norms.insert(), [{**key, **row} for row in norm_rows])
        if anomaly_rows:
            conn.execute(
                computed_indicators.insert(),
                [{**key, "stat_type": STAT_ANOMALY, "source_file": None, **row} for row in anomaly_rows],
            )
        conn.execute(period_completeness.insert(), [{**key, **row} for row in completeness_rows])
        conn.execute(
            anomaly_state.insert().values(
                wmo_index=wmo_index,
                calculated_at=datetime.now(),
                rule_version=RULE_VERSION,
                norm_standard=norms.standard,
                norm_first_year=norms.annual.first_year if norms.annual else None,
                norm_last_year=norms.annual.last_year if norms.annual else None,
                norm_years=norms.annual.year_count if norms.annual else 0,
            )
        )
    return norms


@dataclass(frozen=True)
class CalcStatus:
    """Состояние расчёта станции — строка окна "Отклонения от нормы"."""

    wmo_index: int
    period: tuple[date, date]
    calculated_at: datetime | None  # None — требуется расчёт
    norm_standard: bool = False
    norm_first_year: int | None = None
    norm_last_year: int | None = None
    norm_years: int = 0

    @property
    def stale(self) -> bool:
        return self.calculated_at is None


def calculation_status(engine: Engine) -> list[CalcStatus]:
    """Все станции с суточными данными и состояние их расчёта."""
    with engine.connect() as conn:
        with_days = _stations_with_days(conn)
        states = {row.wmo_index: row for row in conn.execute(select(anomaly_state))}
    result = []
    for wmo, period in sorted(with_days.items()):
        state = states.get(wmo)
        if state is None or state.rule_version != RULE_VERSION:
            result.append(CalcStatus(wmo, period, None))
        else:
            result.append(CalcStatus(
                wmo, period, state.calculated_at, state.norm_standard,
                state.norm_first_year, state.norm_last_year, state.norm_years,
            ))
    return result


def _orphans(engine: Engine, with_days: Iterable[int]) -> set[int]:
    """Станции, по которым что-то сохранено, а суточных значений уже нет
    (данные удалили) — их нормы и отклонения больше ни к чему не относятся."""
    stored: set[int] = set()
    with engine.connect() as conn:
        stored.update(conn.execute(select(anomaly_state.c.wmo_index)).scalars())
        stored.update(conn.execute(select(station_norms.c.wmo_index).distinct()).scalars())
        stored.update(conn.execute(select(period_completeness.c.wmo_index).distinct()).scalars())
    return stored - set(with_days)


def ensure_fresh(
    engine: Engine,
    wmo_indexes: Iterable[int] | None = None,
    progress: Callable[[int, int, int], None] | None = None,
    force: bool = False,
) -> list[int]:
    """Досчитать станции, требующие расчёта (force — все), и вернуть их
    индексы. wmo_indexes ограничивает круг станций; None — все станции с
    данными, и тогда же убирается сохранённое по станциям без данных.
    progress(номер, всего, wmo_index) вызывается перед расчётом каждой."""
    if wmo_indexes is not None:
        # Без обхода всей расчётной таблицы: для одной станции (дашборд)
        # достаточно посмотреть её строку состояния
        todo = [wmo for wmo in wmo_indexes if force or is_stale(engine, wmo)]
    else:
        statuses = calculation_status(engine)
        for wmo in _orphans(engine, (status.wmo_index for status in statuses)):
            with engine.begin() as conn:
                _purge(conn, wmo)
        todo = [status.wmo_index for status in statuses if force or status.stale]
    for number, wmo in enumerate(todo, start=1):
        if progress is not None:
            progress(number, len(todo), wmo)
        recalculate_station(engine, wmo)
    return todo


def load_station_norms(engine: Engine, wmo_index: int) -> StationNorms | None:
    """Сохранённые нормы и полнота станции; None — расчёта нет или он устарел."""
    with engine.connect() as conn:
        state = conn.execute(
            select(anomaly_state).where(anomaly_state.c.wmo_index == wmo_index)
        ).one_or_none()
        if state is None or state.rule_version != RULE_VERSION:
            return None
        norm_rows = conn.execute(
            select(station_norms.c.period_type, station_norms.c.month, station_norms.c.decade, station_norms.c.value)
            .where(station_norms.c.wmo_index == wmo_index, station_norms.c.parameter == PARAMETER)
        ).all()
        complete_rows = conn.execute(
            select(period_completeness.c.period_type, period_completeness.c.period_start).where(
                period_completeness.c.wmo_index == wmo_index,
                period_completeness.c.parameter == PARAMETER,
                period_completeness.c.complete.is_(True),
            )
        ).all()

    months: dict[int, float] = {}
    decades: dict[DecadeKey, float] = {}
    annual_value: float | None = None
    for period_type, month, decade, value in norm_rows:
        if period_type == "month":
            months[month] = value
        elif period_type == "decade":
            decades[(month, (decade - 1) * 10 + 1)] = value
        else:
            annual_value = value
    annual = (
        NormInfo(annual_value, state.norm_first_year, state.norm_last_year, state.norm_years, state.norm_standard)
        if annual_value is not None
        else None
    )
    return StationNorms(
        state.norm_standard,
        months,
        decades,
        annual,
        frozenset((start.year, start.month) for kind, start in complete_rows if kind == "month"),
        frozenset(start.year for kind, start in complete_rows if kind == "year"),
    )
