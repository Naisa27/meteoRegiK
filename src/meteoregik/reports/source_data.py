"""Данные вкладки "Исходные данные" экрана "Дашборд" (корректировка
2026-10-10): что залито в базу, за какие периоды и где пропуски. В отличие
от reports/dashboard_data.py — по ВСЕЙ базе, без фильтра "станция + период":
сводка и таблица станций (SourceOverview), подробности — по одной станции,
выбранной в таблице (StationSourceDetail). Qt-независимо.

Пропущенные дни считаются по расчётной таблице computed_indicators
(суточная средняя T): между первым и последним днём станции — дни без
значения. Дни до первого и после последнего пропусками не считаются: это
"архив ещё не начался/уже закончился", а не дыра в нём.

Пропущенные сроки считаются по rp5_readings. Срок есть, если на него есть
строка с непустой температурой (решение пользователя 2026-10-10: строка с
пустой T — тоже пропуск, для расчёта суточной средней её всё равно что
нет). В расчёт идут только дни, где есть хотя бы один срок: день без единого
срока — это пропущенный день, а не восемь пропущенных сроков.

Сетка сроков — 8 в сутки через 3 часа, но не обязательно 0, 3, …, 21:
rp5.ru отдаёт местное время станции, и у станции в поясе UTC+4 сроки будут
1, 4, …, 22. Поэтому сдвиг сетки (0, 1 или 2 часа) определяется по самим
данным — берётся тот, на который приходится больше всего наблюдений
(obs_grid_offset). Наблюдения вне сетки (например, в 13:30) в счёт сроков
не идут. Архив с четырьмя сроками в сутки даёт четыре "пропуска" в день —
это видно на графике распределения по срокам (StationSourceDetail.hour_counts).
"""

from __future__ import annotations

from calendar import monthrange
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import Integer, Table, case, cast, distinct, func, select
from sqlalchemy.engine import Engine

from meteoregik.db.schema import RP5_PARAMETER_COLUMNS, computed_indicators, gg_readings, rp5_readings
from meteoregik.stations.choices import load_stations_in_table, load_stations_with_computed_indicators

PARAMETER = "T"
OBS_PER_DAY = 8
OBS_STEP_HOURS = 3
FORMAT_RP5 = "rp5"
FORMAT_GG = "ГГ"

_DAYS_PER_YEAR = 365.25
_DAYS_PER_MONTH = _DAYS_PER_YEAR / 12

Period = tuple[date, date]


def grid_hours(offset: int) -> tuple[int, ...]:
    """Часы сроков для сдвига сетки: 0 -> (0, 3, …, 21), 1 -> (1, 4, …, 22)."""
    return tuple(range(offset, 24, OBS_STEP_HOURS))


def obs_grid_offset(present_by_offset: Mapping[int, int]) -> int:
    """Сдвиг сетки сроков, на который приходится больше всего наблюдений;
    при равенстве (в т.ч. без наблюдений) — меньший, т.е. обычные 0, 3, …, 21."""
    return max(range(OBS_STEP_HOURS), key=lambda k: (present_by_offset.get(k, 0), -k))


def _merge(*periods: Period | None) -> Period | None:
    known = [p for p in periods if p is not None]
    if not known:
        return None
    return min(p[0] for p in known), max(p[1] for p in known)


@dataclass(frozen=True)
class StationSourceRow:
    """Строка таблицы станций: что залито по станции и сколько пропущено."""

    wmo_index: int
    label: str
    rp5_period: Period | None = None
    gg_period: Period | None = None
    computed_period: Period | None = None
    days_with_data: int = 0  # дни с суточной средней в расчётной таблице
    obs_days: int = 0  # дни rp5 хотя бы с одним сроком
    obs_present: int = 0  # сроки rp5 с температурой (на сетке сроков)

    @property
    def formats(self) -> tuple[str, ...]:
        return tuple(
            name for name, period in ((FORMAT_RP5, self.rp5_period), (FORMAT_GG, self.gg_period))
            if period is not None
        )

    @property
    def days_total(self) -> int:
        if self.computed_period is None:
            return 0
        first, last = self.computed_period
        return (last - first).days + 1

    @property
    def days_missing(self) -> int:
        return self.days_total - self.days_with_data

    @property
    def days_missing_per_year(self) -> float | None:
        return self.days_missing / (self.days_total / _DAYS_PER_YEAR) if self.days_total else None

    @property
    def days_missing_per_month(self) -> float | None:
        return self.days_missing / (self.days_total / _DAYS_PER_MONTH) if self.days_total else None

    @property
    def obs_missing(self) -> int:
        return self.obs_days * OBS_PER_DAY - self.obs_present

    @property
    def obs_missing_per_day(self) -> float | None:
        return self.obs_missing / self.obs_days if self.obs_days else None


@dataclass(frozen=True)
class LoadedFile:
    """Строка истории загрузок: один файл по одной станции."""

    source_file: str | None  # None — строки добавлены вручную в "Просмотре исходных данных"
    format: str
    wmo_index: int
    station_label: str
    rows: int
    period: Period
    loaded_at: datetime | None  # местное время


@dataclass(frozen=True)
class SourceOverview:
    stations: list[StationSourceRow] = field(default_factory=list)
    files: list[LoadedFile] = field(default_factory=list)

    @property
    def has_data(self) -> bool:
        return bool(self.stations)

    def stations_with(self, *formats: str) -> int:
        """Сколько станций залито во всех перечисленных форматах."""
        return sum(1 for s in self.stations if all(f in s.formats for f in formats))

    @property
    def computed_period(self) -> Period | None:
        return _merge(*(s.computed_period for s in self.stations))

    @property
    def rp5_period(self) -> Period | None:
        return _merge(*(s.rp5_period for s in self.stations))

    @property
    def gg_period(self) -> Period | None:
        return _merge(*(s.gg_period for s in self.stations))

    @property
    def days_missing(self) -> int:
        return sum(s.days_missing for s in self.stations)

    @property
    def obs_missing(self) -> int:
        return sum(s.obs_missing for s in self.stations)

    @property
    def stations_with_day_gaps(self) -> int:
        return sum(1 for s in self.stations if s.days_missing)

    @property
    def stations_with_obs_gaps(self) -> int:
        return sum(1 for s in self.stations if s.obs_missing)


@dataclass(frozen=True)
class DayGap:
    """Подряд идущие дни без суточной средней."""

    first: date
    last: date

    @property
    def days(self) -> int:
        return (self.last - self.first).days + 1


@dataclass(frozen=True)
class ObsGap:
    """Подряд идущие пропущенные сроки (first и last — сами пропущенные сроки)."""

    first: datetime
    last: datetime
    count: int


@dataclass(frozen=True)
class MonthFill:
    """Клетка календаря полноты: дни месяца с суточной средней."""

    year: int
    month: int
    days_with_data: int
    days_expected: int  # дни месяца внутри архива станции

    @property
    def share(self) -> float:
        return self.days_with_data / self.days_expected


@dataclass(frozen=True)
class ParameterFill:
    column: str
    filled: int
    total: int

    @property
    def share(self) -> float:
        return self.filled / self.total if self.total else 0.0


@dataclass(frozen=True)
class StationSourceDetail:
    wmo_index: int
    day_gaps: list[DayGap] = field(default_factory=list)
    obs_gaps: list[ObsGap] = field(default_factory=list)
    months: list[MonthFill] = field(default_factory=list)
    # Час -> число наблюдений с температурой (все часы, не только сетка сроков)
    hour_counts: dict[int, int] = field(default_factory=dict)
    obs_hours: tuple[int, ...] = grid_hours(0)
    obs_days: int = 0
    parameters: list[ParameterFill] = field(default_factory=list)

    @property
    def rp5_rows(self) -> int:
        return self.parameters[0].total if self.parameters else 0


# --- расчёт (без БД) ---


def format_share(share: float) -> str:
    """Доля в процентах. Неполное не округляется до "100 %", а непустое —
    до "0 %": иначе месяц с одним пропущенным днём выглядел бы полным."""
    percent = share * 100
    if 99.5 <= percent < 100:
        return "99.9 %" if percent >= 99.95 else f"{percent:.1f} %"
    if 0 < percent < 0.5:
        return "< 1 %"
    return f"{percent:.0f} %"


def day_gaps(days: Iterable[date]) -> list[DayGap]:
    """Пропуски между соседними днями с данными."""
    ordered = sorted(set(days))
    one_day = timedelta(days=1)
    return [
        DayGap(prev + one_day, cur - one_day)
        for prev, cur in zip(ordered, ordered[1:])
        if (cur - prev).days > 1
    ]


def month_fill(days: Iterable[date]) -> list[MonthFill]:
    """Полнота по месяцам от первого до последнего дня с данными. У крайних
    месяцев ожидаются только дни внутри архива: архив с 15 июля не должен
    показывать июль заполненным наполовину."""
    ordered = sorted(set(days))
    if not ordered:
        return []
    first, last = ordered[0], ordered[-1]
    counts = Counter((day.year, day.month) for day in ordered)
    result = []
    year, month = first.year, first.month
    while (year, month) <= (last.year, last.month):
        month_first = max(first, date(year, month, 1))
        month_last = min(last, date(year, month, monthrange(year, month)[1]))
        result.append(MonthFill(year, month, counts.get((year, month), 0), (month_last - month_first).days + 1))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return result


def obs_gaps(present: Iterable[tuple[date, int]], hours: tuple[int, ...]) -> list[ObsGap]:
    """Пропущенные сроки подряд. present — (день, час) наблюдений с
    температурой на сетке hours. Дни без единого срока в расчёт не идут и
    серию пропусков разрывают: "с 21:00 3-го по 00:00 9-го" через пять
    пустых дней было бы уже не про сроки."""
    by_day: dict[date, set[int]] = {}
    for day, hour in present:
        by_day.setdefault(day, set()).add(hour)

    gaps: list[ObsGap] = []
    run: list[datetime] = []
    previous: date | None = None

    def close() -> None:
        if run:
            gaps.append(ObsGap(run[0], run[-1], len(run)))
            run.clear()

    for day in sorted(by_day):
        if previous is not None and (day - previous).days > 1:
            close()
        for hour in hours:
            if hour in by_day[day]:
                close()
            else:
                run.append(datetime.combine(day, time(hour)))
        previous = day
    close()
    return gaps


# --- выборка ---


def _hour(column) -> object:
    # obs_time хранится в SQLite текстом "ЧЧ:ММ:СС.ffffff"
    return cast(func.strftime("%H", column), Integer)


def _on_grid(offset: int) -> object:
    obs_time = rp5_readings.c.obs_time
    return (
        rp5_readings.c.T_temperature.is_not(None)
        & (func.strftime("%M:%S", obs_time) == "00:00")
        & (_hour(obs_time) % OBS_STEP_HOURS == offset)
    )


def _station_labels(engine: Engine) -> dict[int, str]:
    labels: dict[int, str] = {}
    choices = [
        *load_stations_with_computed_indicators(engine),
        *load_stations_in_table(engine, gg_readings),
        *load_stations_in_table(engine, rp5_readings),
    ]
    for choice in choices:
        # Имя из справочника есть у всех трёх выборок; у станции не из
        # справочника его знают только таблицы rp5/ГГ (кэш station_name)
        if choice.wmo_index not in labels or choice.name:
            labels[choice.wmo_index] = choice.label
    return labels


def _table_periods(conn, table: Table) -> dict[int, Period]:
    stmt = (
        select(table.c.wmo_index, func.min(table.c.obs_date), func.max(table.c.obs_date))
        .where(table.c.wmo_index.is_not(None))
        .group_by(table.c.wmo_index)
    )
    return {wmo: (first, last) for wmo, first, last in conn.execute(stmt)}


def _loaded_files(conn, table: Table, fmt: str, labels: Mapping[int, str]) -> list[LoadedFile]:
    stmt = (
        select(
            table.c.source_file,
            table.c.wmo_index,
            func.count(),
            func.min(table.c.obs_date),
            func.max(table.c.obs_date),
            func.min(table.c.created_at),
        )
        .where(table.c.wmo_index.is_not(None))
        .group_by(table.c.source_file, table.c.wmo_index)
    )
    return [
        LoadedFile(
            source_file, fmt, wmo, labels.get(wmo, str(wmo)), rows, (first, last),
            # created_at — CURRENT_TIMESTAMP SQLite, т.е. UTC
            created.replace(tzinfo=UTC).astimezone().replace(tzinfo=None) if created else None,
        )
        for source_file, wmo, rows, first, last, created in conn.execute(stmt)
    ]


def has_loaded_readings(engine: Engine) -> bool:
    """Есть ли в базе хоть одна строка исходных данных (rp5 или ГГ). Дешёвая
    проверка для подсказки "в базе пока нет данных" — без сбора всей сводки."""
    with engine.connect() as conn:
        return any(
            conn.execute(select(table.c.id).limit(1)).first() is not None
            for table in (rp5_readings, gg_readings)
        )


def fetch_source_overview(engine: Engine) -> SourceOverview:
    """Сводка по всей базе — по одному проходу на таблицу (GROUP BY станция)."""
    labels = _station_labels(engine)
    offsets = range(OBS_STEP_HOURS)
    obs_stmt = (
        select(
            rp5_readings.c.wmo_index,
            *(func.count(distinct(case((_on_grid(k), rp5_readings.c.obs_date)))) for k in offsets),
            *(func.count(case((_on_grid(k), 1))) for k in offsets),
        )
        .where(rp5_readings.c.wmo_index.is_not(None))
        .group_by(rp5_readings.c.wmo_index)
    )
    computed_stmt = (
        select(
            computed_indicators.c.wmo_index,
            func.min(computed_indicators.c.period_start),
            func.max(computed_indicators.c.period_start),
            func.count(),
        )
        .where(
            computed_indicators.c.period_type == "day",
            computed_indicators.c.parameter == PARAMETER,
            computed_indicators.c.stat_type == "avg",
            computed_indicators.c.value.is_not(None),
        )
        .group_by(computed_indicators.c.wmo_index)
    )
    with engine.connect() as conn:
        rp5_periods = _table_periods(conn, rp5_readings)
        gg_periods = _table_periods(conn, gg_readings)
        observations: dict[int, tuple[int, int]] = {}
        for wmo, *counts in conn.execute(obs_stmt):
            days, present = counts[:OBS_STEP_HOURS], counts[OBS_STEP_HOURS:]
            offset = obs_grid_offset(dict(enumerate(present)))
            observations[wmo] = (days[offset], present[offset])
        computed = {wmo: ((first, last), count) for wmo, first, last, count in conn.execute(computed_stmt)}
        files = [
            *_loaded_files(conn, rp5_readings, FORMAT_RP5, labels),
            *_loaded_files(conn, gg_readings, FORMAT_GG, labels),
        ]

    rows = []
    for wmo in sorted({*rp5_periods, *gg_periods, *computed}):
        computed_period, days_with_data = computed.get(wmo, (None, 0))
        obs_days, obs_present = observations.get(wmo, (0, 0))
        rows.append(
            StationSourceRow(
                wmo, labels.get(wmo, str(wmo)), rp5_periods.get(wmo), gg_periods.get(wmo),
                computed_period, days_with_data, obs_days, obs_present,
            )
        )
    rows.sort(key=lambda row: row.label.casefold())
    files.sort(key=lambda f: (f.loaded_at or datetime.min, f.station_label.casefold()), reverse=True)
    return SourceOverview(rows, files)


def fetch_station_detail(engine: Engine, wmo_index: int) -> StationSourceDetail:
    """Подробности одной станции: периоды пропусков, полнота по месяцам,
    распределение наблюдений по срокам, заполненность параметров rp5."""
    days_stmt = select(computed_indicators.c.period_start).where(
        computed_indicators.c.wmo_index == wmo_index,
        computed_indicators.c.period_type == "day",
        computed_indicators.c.parameter == PARAMETER,
        computed_indicators.c.stat_type == "avg",
        computed_indicators.c.value.is_not(None),
    )
    obs_stmt = select(rp5_readings.c.obs_date, rp5_readings.c.obs_time).where(
        rp5_readings.c.wmo_index == wmo_index,
        rp5_readings.c.T_temperature.is_not(None),
    )
    # Пустая строка — тоже "не заполнено" (текстовые колонки rp5)
    fill_stmt = select(
        func.count(),
        *(func.count(func.nullif(rp5_readings.c[name], "")) for name, _ in RP5_PARAMETER_COLUMNS),
    ).where(rp5_readings.c.wmo_index == wmo_index)
    with engine.connect() as conn:
        days = [row[0] for row in conn.execute(days_stmt)]
        observations = conn.execute(obs_stmt).all()
        total, *filled = conn.execute(fill_stmt).one()

    hour_counts = Counter(obs_time.hour for _, obs_time in observations)
    on_grid = [(day, t.hour) for day, t in observations if (t.minute, t.second) == (0, 0)]
    offset = obs_grid_offset(Counter(hour % OBS_STEP_HOURS for _, hour in on_grid))
    hours = grid_hours(offset)
    present = [(day, hour) for day, hour in on_grid if hour in hours]
    return StationSourceDetail(
        wmo_index,
        day_gaps(days),
        obs_gaps(present, hours),
        month_fill(days),
        dict(sorted(hour_counts.items())),
        hours,
        len({day for day, _ in present}),
        [ParameterFill(name, count, total) for (name, _), count in zip(RP5_PARAMETER_COLUMNS, filled)] if total else [],
    )
