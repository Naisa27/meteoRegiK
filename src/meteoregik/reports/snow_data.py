"""Сводки снежного покрова поверх сохранённых суточных значений, без Qt."""

from __future__ import annotations

from calendar import monthrange
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from datetime import date, timedelta
from statistics import fmean

from sqlalchemy import func, select
from sqlalchemy.engine import Engine

from meteoregik.db.schema import computed_indicators, rp5_readings, station_norms, period_completeness
from meteoregik.calculations import anomalies, snow_norms


@dataclass(frozen=True)
class SnowHeatCell:
    start: date
    end: date
    value: int
    norm: float | None
    anomaly: float | None
    complete: bool
    base_text: str


@dataclass(frozen=True)
class SnowPeriod:
    start: date
    end: date
    available_start: date
    available_end: date
    snow_days: int
    max_depth: float
    maximum_dates: tuple[date, ...]
    frost_days: int | None
    temperature_days: int

    @property
    def partial(self) -> bool:
        return self.start != self.available_start or self.end != self.available_end

    @property
    def available_days(self) -> int:
        return (self.available_end - self.available_start).days + 1


@dataclass(frozen=True)
class SnowMaximumMean:
    value: float
    first_year: int
    last_year: int
    count: int
    standard: bool

    @property
    def base_text(self) -> str:
        base = "1991–2020" if self.standard else f"архив {self.first_year}–{self.last_year}"
        return f"{base}, полных периодов: {self.count}"


@dataclass(frozen=True)
class SnowData:
    total: SnowPeriod | None
    months: tuple[SnowPeriod, ...] = ()
    seasons: tuple[SnowPeriod, ...] = ()
    heat_months: tuple[SnowHeatCell, ...] = ()
    heat_decades: tuple[SnowHeatCell, ...] = ()
    maximum_month_means: tuple[SnowMaximumMean | None, ...] = (None,) * 12
    maximum_season_mean: SnowMaximumMean | None = None
    season_snow_norm: float | None = None
    season_snow_base: str = ""

    @property
    def has_data(self) -> bool:
        return self.total is not None


def station_day_bounds(engine: Engine, wmo_index: int) -> tuple[date, date] | None:
    """Границы только rp5: данные ГГ снеговой архив не расширяют."""
    with engine.connect() as conn:
        first, last = conn.execute(
            select(func.min(rp5_readings.c.obs_date), func.max(rp5_readings.c.obs_date))
            .where(rp5_readings.c.wmo_index == wmo_index)
        ).one()
    return (first, last) if first is not None and last is not None else None


def format_maximum_dates(days: tuple[date, ...]) -> str:
    """Объединить только соседние даты; отдельные серии остаются отдельными."""
    if not days:
        return "—"
    ordered = sorted(set(days))
    runs: list[tuple[date, date]] = []
    first = last = ordered[0]
    for day in ordered[1:]:
        if day == last + timedelta(days=1):
            last = day
        else:
            runs.append((first, last))
            first = last = day
    runs.append((first, last))
    labels: list[str] = []
    for first, last in runs:
        if first == last:
            labels.append(first.strftime("%d.%m.%Y"))
        elif (first.year, first.month) == (last.year, last.month):
            labels.append(f"{first.day:02d}–{last:%d.%m.%Y}")
        else:
            labels.append(f"{first:%d.%m.%Y}–{last:%d.%m.%Y}")
    return ", ".join(labels)


def build_snow(engine: Engine, wmo_index: int, start: date, end: date) -> SnowData:
    """Собрать месяцы и зимние сезоны в пределах выбранной части rp5-архива.

    Нули снега уже достроены расчётом. Отсутствие температуры отличаем от
    подтверждённого отсутствия морозов: число оценённых дней хранится явно.
    """
    from meteoregik.calculations.freshness import ensure_computed_fresh
    ensure_computed_fresh(engine, [wmo_index])
    bounds = station_day_bounds(engine, wmo_index)
    if bounds is None or start > end:
        return SnowData(None)
    first, last = max(start, bounds[0]), min(end, bounds[1])
    if first > last:
        return SnowData(None)
    with engine.connect() as conn:
        rows = conn.execute(
            select(computed_indicators.c.period_start, computed_indicators.c.parameter,
                   computed_indicators.c.stat_type, computed_indicators.c.value).where(
                computed_indicators.c.wmo_index == wmo_index,
                computed_indicators.c.period_type == "day",
                computed_indicators.c.period_start.between(first, last),
                computed_indicators.c.parameter.in_(("S", "SNOW_DEPTH", "FROST_LOW_SNOW")),
                computed_indicators.c.stat_type.in_(("sum", "max")),
                computed_indicators.c.value.is_not(None),
            )
        ).all()
        temperature_dates = set(conn.execute(
            select(computed_indicators.c.period_start).where(
                computed_indicators.c.wmo_index == wmo_index,
                computed_indicators.c.period_type == "day",
                computed_indicators.c.period_start.between(first, last),
                computed_indicators.c.parameter == "T",
                computed_indicators.c.stat_type == "avg",
                computed_indicators.c.value.is_not(None),
            )
        ).scalars())
    values: dict[str, dict[date, float]] = {"S": {}, "SNOW_DEPTH": {}, "FROST_LOW_SNOW": {}}
    for day, parameter, stat_type, value in rows:
        if (parameter, stat_type) in (("S", "sum"), ("SNOW_DEPTH", "max"), ("FROST_LOW_SNOW", "sum")):
            values[parameter][day] = value

    expected_days = (last - first).days + 1
    if len(values["S"]) != expected_days or len(values["SNOW_DEPTH"]) != expected_days:
        raise RuntimeError(
            "Расчёт снежного покрова отсутствует или неполон. "
            "Повторно импортируйте файл этой станции для автоматического восстановления расчётов."
        )
    if set(values["FROST_LOW_SNOW"]) != temperature_dates:
        raise RuntimeError(
            "Расчёт сильных морозов при малоснежье отсутствует или неполон. "
            "Повторно импортируйте файл этой станции для автоматического восстановления расчётов."
        )
    # Сортировка один раз: каждый месяц читает только свои дни. Повторный
    # обход всего архива для каждой строки делал длинные архивы медленными.
    days = sorted(values["SNOW_DEPTH"])
    snow_prefix = [0.0]
    for day in days:
        snow_prefix.append(snow_prefix[-1] + values["S"][day])
    frost_dates = sorted(values["FROST_LOW_SNOW"])
    frost_prefix = [0.0]
    for day in frost_dates:
        frost_prefix.append(frost_prefix[-1] + values["FROST_LOW_SNOW"][day])

    def period(period_start: date, period_end: date) -> SnowPeriod:
        available_start, available_end = max(first, period_start), min(last, period_end)
        left, right = bisect_left(days, available_start), bisect_right(days, available_end)
        depths = {day: values["SNOW_DEPTH"][day] for day in days[left:right]}
        maximum = max(depths.values(), default=0.0)
        frost_left = bisect_left(frost_dates, available_start)
        frost_right = bisect_right(frost_dates, available_end)
        temperature_days = frost_right - frost_left
        return SnowPeriod(
            period_start, period_end, available_start, available_end,
            int(snow_prefix[right] - snow_prefix[left]),
            maximum,
            tuple(sorted(day for day, value in depths.items() if value == maximum and maximum > 0)),
            int(frost_prefix[frost_right] - frost_prefix[frost_left]) if temperature_days else None,
            temperature_days,
        )

    months: list[SnowPeriod] = []
    year, month = first.year, first.month
    while (year, month) <= (last.year, last.month):
        months.append(period(date(year, month, 1), date(year, month, monthrange(year, month)[1])))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    first_season = first.year if first.month >= 7 else first.year - 1
    last_season = last.year if last.month >= 7 else last.year - 1
    seasons = tuple(period(date(year, 7, 1), date(year + 1, 6, 30))
                    for year in range(first_season, last_season + 1))
    anomalies.ensure_fresh(engine, [wmo_index])
    heat_months, heat_decades = _heat_cells(engine, wmo_index, first, last)
    month_means, season_mean = maximum_means(engine, wmo_index, bounds)
    with engine.connect() as conn:
        season_norm = conn.execute(select(station_norms.c.value).where(
            station_norms.c.wmo_index == wmo_index, station_norms.c.parameter == "S",
            station_norms.c.period_type == "winter_season", station_norms.c.month == 7,
            station_norms.c.decade == 0)).scalar_one_or_none()
        bases = snow_norms.load_bases(conn, wmo_index)
    base_text = "; ".join(f"{month:02d}: {bases['month', month, 0].text}"
                          for month in range(1, 13) if ("month", month, 0) in bases)
    return SnowData(period(start, end), tuple(months), seasons, heat_months, heat_decades,
                    month_means, season_mean, season_norm, base_text)


def maximum_means(
    engine: Engine, wmo_index: int, bounds: tuple[date, date],
) -> tuple[tuple[SnowMaximumMean | None, ...], SnowMaximumMean | None]:
    """Средние максимумов полных месяцев и сезонов всего архива, вне фильтра экрана."""
    first, last = bounds
    table = computed_indicators
    with engine.connect() as conn:
        rows = conn.execute(select(table.c.period_start, table.c.value).where(
            table.c.wmo_index == wmo_index, table.c.parameter == "SNOW_DEPTH",
            table.c.stat_type == "max", table.c.period_type == "day",
            table.c.period_start.between(first, last), table.c.value.is_not(None))).all()
    if len(rows) != (last - first).days + 1:
        raise RuntimeError("Расчёт высоты снега неполон во всём архиве станции.")
    months: dict[tuple[int, int], float] = {}
    seasons: dict[int, float] = {}
    for day, value in rows:
        key = day.year, day.month
        months[key] = max(months.get(key, 0.0), value)
        end_year = day.year + (day.month >= 7)
        seasons[end_year] = max(seasons.get(end_year, 0.0), value)

    def mean(samples: list[tuple[int, float]]) -> SnowMaximumMean | None:
        standard = [(year, value) for year, value in samples if 1991 <= year <= 2020]
        use_standard = len(standard) >= 24
        selected = standard if use_standard else samples
        if not selected:
            return None
        years, values = zip(*selected)
        return SnowMaximumMean(fmean(values), min(years), max(years), len(selected), use_standard)

    monthly = tuple(mean([(year, value) for (year, number), value in months.items()
                          if number == month and first <= date(year, month, 1)
                          and last >= date(year, month, monthrange(year, month)[1])])
                    for month in range(1, 13))
    seasonal = mean([(year, value) for year, value in seasons.items()
                     if first <= date(year - 1, 7, 1) and last >= date(year, 6, 30)])
    return monthly, seasonal


def _heat_cells(
    engine: Engine, wmo_index: int, first: date, last: date,
) -> tuple[tuple[SnowHeatCell, ...], tuple[SnowHeatCell, ...]]:
    table = computed_indicators
    with engine.connect() as conn:
        rows = conn.execute(select(table.c.period_type, table.c.period_start,
            table.c.period_end, table.c.value).where(table.c.wmo_index == wmo_index,
            table.c.parameter == "S", table.c.stat_type == "sum",
            table.c.period_type.in_(("month", "decade")), table.c.period_start <= last,
            table.c.period_end >= first).order_by(table.c.period_start)).all()
        norms = {(row.period_type, row.month, row.decade): row.value for row in conn.execute(
            select(station_norms).where(station_norms.c.wmo_index == wmo_index,
                                        station_norms.c.parameter == "S"))}
        completeness = {(row.period_type, row.period_start): row.complete for row in conn.execute(
            select(period_completeness).where(period_completeness.c.wmo_index == wmo_index,
                                              period_completeness.c.parameter == "S"))}
        bases = snow_norms.load_bases(conn, wmo_index)
    months: list[SnowHeatCell] = []
    decades: list[SnowHeatCell] = []
    for kind, start, end, value in rows:
        key = kind, start.month, (start.day - 1) // 10 + 1 if kind == "decade" else 0
        normal = norms.get(key)
        base = bases.get(key)
        cell = SnowHeatCell(start, end, int(value), normal,
            value - normal if normal is not None else None,
            completeness.get((kind, start), False),
            base.text if base else "Нет полных периодов для расчёта нормы. " + snow_norms.ASSUMPTION)
        (months if kind == "month" else decades).append(cell)
    return tuple(months), tuple(decades)
