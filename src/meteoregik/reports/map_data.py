"""Данные экрана "Карта" (2026-10-10): станции справочника с координатами,
что по ним залито (reports/source_data.py) и значения показателей
температуры и осадков по станциям — за период и по шагам времени (годы,
месяцы) для ползунка. Qt-независимо; цвета, подписи и всплывающие карточки
маркеров — в reports/map_render.py.

Нормы, полнота и отклонения берутся сохранённые (calculations/anomalies.py,
решение пользователя 2026-10-10): перед чтением fetch_map_data досчитывает
станции, чьи данные менялись после последнего расчёта. Суточные значения в
память не читаются — раньше карта держала их по всем станциям и считала
всё сама, что на десятках станций стало бы заметной паузой:
- ряды по годам и месяцам — готовые строки month/year из
  computed_indicators (avg / min / max / sum / anomaly) и period_completeness;
- значение за произвольный период — агрегаты SQL по суточным строкам;
  отклонение — среднее (суточная − норма её месяца), то же определение, что
  у карточки дашборда, поэтому числа на карте и в дашборде совпадают.

Полнота. Средняя за год и её отклонение от нормы показываются только для
полных лет (как рекорды и аномалии дашборда): "год" из одного января дал бы
на карте ложно холодную станцию. Месяцы — только полные (правило ВМО "3/5").
Абсолютные максимум и минимум — по тем дням, что есть: рекорд не становится
неверным оттого, что в году есть пропуски.

Осадки (корректировка 2026-10-11, этап 4) — три показателя:
- сумма осадков: за заданный период — сумма суточных сумм; по годам и
  месяцам — сохранённые суммы полных лет и месяцев. Без периода ("весь архив
  каждой станции") — средняя годовая сумма по полным годам, а не сумма за
  весь архив: архивы станций разной длины, и сумма за 60 лет рядом с суммой
  за 20 показала бы на карте длину архива, а не осадки;
- осадки в процентах нормы: за период — сумма дней ПОЛНЫХ месяцев периода к
  норме тех же дней (сумма суточных долей месячных норм), то же определение,
  что у карточки вкладки "Осадки" дашборда (решение пользователя 2026-10-17:
  неполные месяцы ранних лет rp5 без записанных сухих дней завышали процент);
  без периода — средний процент по полным годам архива, как и сумма; по
  годам и месяцам — сумма к норме года (месяца), только полные;
- суточный максимум — по тем дням, что есть, как рекорды температуры.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from statistics import fmean

from sqlalchemy import Integer, and_, cast, func, select
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.sql.elements import ColumnElement

from meteoregik.calculations.anomalies import PRECIPITATION, STAT_ANOMALY, TEMPERATURE, ParameterRules, ensure_fresh
from meteoregik.db.schema import computed_indicators, period_completeness, ru_regions, station_norms, stations
from meteoregik.reports.dashboard_data import MONTHS_NOMINATIVE
from meteoregik.reports.precipitation_data import percent_of_norm
from meteoregik.reports.source_data import SourceOverview, StationSourceRow, fetch_source_overview
from meteoregik.stations.choices import _REGION_JOIN, StationChoice

MEAN = "mean"
ANOMALY = "anomaly"
MAXIMUM = "max"
MINIMUM = "min"
PRECIP_SUM = "precip_sum"
PRECIP_PERCENT = "precip_percent"
PRECIP_DAY_MAX = "precip_day_max"


@dataclass(frozen=True)
class Indicator:
    """Показатель карты: подпись в списке и легенде, параметр и вид числа."""

    label: str
    rules: ParameterRules
    unit: str
    signed: bool = False  # температура пишется со знаком: "+5.4"
    digits: int = 1


# Порядок — порядок в списке "Показатель": сначала температура, затем осадки
INDICATORS: dict[str, Indicator] = {
    MEAN: Indicator("Средняя температура", TEMPERATURE, "°C", signed=True),
    ANOMALY: Indicator("Отклонение температуры от нормы", TEMPERATURE, "°C", signed=True),
    MAXIMUM: Indicator("Абсолютный максимум температуры", TEMPERATURE, "°C", signed=True),
    MINIMUM: Indicator("Абсолютный минимум температуры", TEMPERATURE, "°C", signed=True),
    PRECIP_SUM: Indicator("Сумма осадков", PRECIPITATION, "мм"),
    PRECIP_PERCENT: Indicator("Осадки, % нормы", PRECIPITATION, "%", digits=0),
    PRECIP_DAY_MAX: Indicator("Суточный максимум осадков", PRECIPITATION, "мм"),
}

STEP_YEAR = "year"
STEP_MONTH = "month"

NOTE_INCOMPLETE_YEAR = "неполный год"
NOTE_INCOMPLETE_MONTH = "неполный месяц"
NOTE_NO_DATA = "нет данных"
NOTE_NO_NORM = "норма не рассчитана"
NOTE_NO_COMPLETE_YEARS = "нет полных лет"
NOTE_NO_COMPLETE_MONTHS = "нет полных месяцев"


@dataclass(frozen=True)
class MapStation:
    wmo_index: int
    label: str
    lat: float
    lon: float


_StepKey = tuple[int, int]  # (год, месяц); у годового шага месяц = 0


@dataclass
class PeriodStats:
    """Сохранённые значения одного параметра станции за месяц или год. У
    температуры — avg / min / max, у осадков — sum и max (наибольшая суточная
    сумма периода)."""

    avg: float | None = None
    min: float | None = None
    max: float | None = None
    sum: float | None = None
    anomaly: float | None = None
    complete: bool = False


_Periods = dict[int, dict[_StepKey, PeriodStats]]


@dataclass(frozen=True)
class StationValue:
    """Значение показателя станции; value=None — показать нечего, почему — в note."""

    value: float | None
    note: str = ""


@dataclass(frozen=True)
class TimeStep:
    label: str  # "2014" или "Март 2014"
    values: dict[int, StationValue]


@dataclass
class MapData:
    stations: list[MapStation] = field(default_factory=list)  # весь справочник с координатами
    sources: dict[int, StationSourceRow] = field(default_factory=dict)  # станции с залитыми данными
    # Месячные и годовые значения станций с посчитанными данными: температура и осадки
    periods: _Periods = field(default_factory=dict)
    precipitation: _Periods = field(default_factory=dict)

    @property
    def located(self) -> dict[int, MapStation]:
        return {station.wmo_index: station for station in self.stations}

    @property
    def without_coordinates(self) -> list[StationSourceRow]:
        """Станции с данными, которых на карте не будет: нет координат в справочнике."""
        located = self.located
        return [row for wmo, row in self.sources.items() if wmo not in located]

    @property
    def shown(self) -> list[int]:
        """Станции с посчитанными данными, которые есть на карте. Станция
        без координат на карту не попадает, и её данные не должны влиять на
        то, что на карте: ни на шкалу цвета, ни на диапазон ползунка времени
        (иначе ползунок доходил бы до дат, за которые ни у одной видимой
        станции ничего нет — корректировка 2026-10-10). Список один для всех
        показателей: станция с температурой, но без осадков остаётся на
        карте осадков серым кружком "нет данных", а не исчезает."""
        located = self.located
        return [wmo for wmo in {**self.periods, **self.precipitation} if wmo in located]

    def periods_of(self, rules: ParameterRules) -> _Periods:
        return self.precipitation if rules is PRECIPITATION else self.periods


def _fetch_periods(engine: Engine, rules: ParameterRules, stat_types: tuple[str, ...]) -> _Periods:
    values = select(
        computed_indicators.c.wmo_index,
        computed_indicators.c.period_type,
        computed_indicators.c.period_start,
        computed_indicators.c.stat_type,
        computed_indicators.c.value,
    ).where(
        computed_indicators.c.parameter == rules.parameter,
        computed_indicators.c.period_type.in_(("month", "year")),
        computed_indicators.c.stat_type.in_((*stat_types, STAT_ANOMALY)),
        computed_indicators.c.value.is_not(None),
    )
    complete = select(
        period_completeness.c.wmo_index, period_completeness.c.period_type, period_completeness.c.period_start
    ).where(
        period_completeness.c.parameter == rules.parameter,
        period_completeness.c.period_type.in_(("month", "year")),
        period_completeness.c.complete.is_(True),
    )

    def key(period_type: str, start: date) -> _StepKey:
        return (start.year, start.month if period_type == "month" else 0)

    result: _Periods = {}
    with engine.connect() as conn:
        for wmo, period_type, start, stat_type, value in conn.execute(values):
            stats = result.setdefault(wmo, {}).setdefault(key(period_type, start), PeriodStats())
            setattr(stats, stat_type, value)
        for wmo, period_type, start in conn.execute(complete):
            stats = result.get(wmo, {}).get(key(period_type, start))
            if stats is not None:
                stats.complete = True
    return result


def _add_daily_maxima(engine: Engine, periods: _Periods) -> None:
    """Наибольшая суточная сумма осадков каждого месяца и года. В отличие
    от температуры, у осадков максимум периода не хранится (у месяца и года —
    только сумма), поэтому он берётся агрегатом по суточным строкам."""
    days = computed_indicators.c
    year = cast(func.strftime("%Y", days.period_start), Integer)
    month = cast(func.strftime("%m", days.period_start), Integer)
    stmt = (
        select(days.wmo_index, year, month, func.max(days.value))
        .where(
            days.period_type == "day",
            days.parameter == PRECIPITATION.parameter,
            days.stat_type == PRECIPITATION.stat_type,
            days.value.is_not(None),
        )
        .group_by(days.wmo_index, year, month)
    )
    with engine.connect() as conn:
        rows = conn.execute(stmt).all()
    for wmo, year_number, month_number, value in rows:
        station = periods.setdefault(wmo, {})
        station.setdefault((year_number, month_number), PeriodStats()).max = value
        yearly = station.setdefault((year_number, 0), PeriodStats())
        yearly.max = value if yearly.max is None else max(yearly.max, value)


def fetch_map_data(engine: Engine, overview: SourceOverview | None = None) -> MapData:
    ensure_fresh(engine)  # нормы и отклонения — по текущим данным
    stmt = (
        select(stations.c.wmo_index, stations.c.name, ru_regions.c.region, stations.c.lat, stations.c.lon)
        .select_from(stations.outerjoin(ru_regions, _REGION_JOIN))
        .where(stations.c.wmo_index.is_not(None), stations.c.lat.is_not(None), stations.c.lon.is_not(None))
    )
    with engine.connect() as conn:
        located = [
            MapStation(wmo, StationChoice(wmo, name, region).label, lat, lon)
            for wmo, name, region, lat, lon in conn.execute(stmt)
        ]
    located.sort(key=lambda station: station.label.casefold())
    overview = overview or fetch_source_overview(engine)
    precipitation = _fetch_periods(engine, PRECIPITATION, (PRECIPITATION.stat_type,))
    _add_daily_maxima(engine, precipitation)
    return MapData(
        located,
        {row.wmo_index: row for row in overview.stations},
        _fetch_periods(engine, TEMPERATURE, ("avg", "min", "max")),
        precipitation,
    )


# --- показатель за период ---


def _format_days(days: list[date]) -> str:
    text = f"{days[0]:%d.%m.%Y}"
    return f"{text} и ещё {len(days) - 1} дн." if len(days) > 1 else text


def _day_filter(
    rules: ParameterRules, stat_type: str, wmo_indexes: list[int], date_from: date | None, date_to: date | None
) -> list[ColumnElement[bool]]:
    days = computed_indicators.c
    conditions = [
        days.period_type == "day",
        days.parameter == rules.parameter,
        days.stat_type == stat_type,
        days.value.is_not(None),
        days.wmo_index.in_(wmo_indexes),
    ]
    if date_from is not None and date_to is not None:
        conditions.append(days.period_start.between(date_from, date_to))
    return conditions


def _norm_of_month(rules: ParameterRules) -> ColumnElement[bool]:
    """Условие соединения суточной строки с нормой её календарного месяца."""
    days = computed_indicators.c
    return and_(
        station_norms.c.wmo_index == days.wmo_index,
        station_norms.c.parameter == rules.parameter,
        station_norms.c.period_type == "month",
        # period_start хранится в SQLite текстом "ГГГГ-ММ-ДД"
        station_norms.c.month == cast(func.strftime("%m", days.period_start), Integer),
    )


def _temperature_means(
    conn: Connection, where: list[ColumnElement[bool]], with_anomaly: bool
) -> dict[int, StationValue]:
    days = computed_indicators.c
    means = dict(conn.execute(select(days.wmo_index, func.avg(days.value)).where(*where).group_by(days.wmo_index)).all())
    if not with_anomaly:
        return {wmo: StationValue(value) for wmo, value in means.items()}
    anomalies = dict(
        conn.execute(
            select(days.wmo_index, func.avg(days.value - station_norms.c.value))
            .select_from(computed_indicators.join(station_norms, _norm_of_month(TEMPERATURE)))
            .where(*where)
            .group_by(days.wmo_index)
        ).all()
    )
    return {
        wmo: StationValue(anomalies.get(wmo), "" if anomalies.get(wmo) is not None else NOTE_NO_NORM) for wmo in means
    }


def _precipitation_sums(conn: Connection, where: list[ColumnElement[bool]]) -> dict[int, StationValue]:
    """Сумма осадков за период — по всем дням с данными."""
    days = computed_indicators.c
    rows = conn.execute(select(days.wmo_index, func.sum(days.value)).where(*where).group_by(days.wmo_index)).all()
    return {wmo: StationValue(round(total, PRECIPITATION.digits)) for wmo, total in rows}


def _precipitation_percents(conn: Connection, where: list[ColumnElement[bool]]) -> dict[int, StationValue]:
    """Осадки за период в процентах нормы — по дням полных месяцев: их сумма к
    сумме их суточных долей месячных норм (норма месяца / число дней в нём).
    Станция с данными, но без полных месяцев в периоде, получает пометку."""
    days = computed_indicators.c
    months = period_completeness.c
    month_start = func.date(days.period_start, "start of month")
    days_in_month = func.julianday(func.date(days.period_start, "start of month", "+1 month")) - func.julianday(
        month_start
    )
    complete_month = and_(
        months.wmo_index == days.wmo_index,
        months.parameter == PRECIPITATION.parameter,
        months.period_type == "month",
        months.period_start == month_start,
        months.complete.is_(True),
    )
    rows = conn.execute(
        select(days.wmo_index, func.sum(days.value), func.sum(station_norms.c.value / days_in_month))
        .select_from(
            computed_indicators.join(period_completeness, complete_month).join(
                station_norms, _norm_of_month(PRECIPITATION)
            )
        )
        .where(*where)
        .group_by(days.wmo_index)
    ).all()
    present = conn.execute(select(days.wmo_index).where(*where).distinct()).scalars().all()
    norms = set(conn.execute(
        select(station_norms.c.wmo_index).where(
            station_norms.c.parameter == PRECIPITATION.parameter, station_norms.c.period_type == "month"
        ).distinct()
    ).scalars())
    result = {
        wmo: StationValue(None, NOTE_NO_COMPLETE_MONTHS if wmo in norms else NOTE_NO_NORM) for wmo in present
    }
    for wmo, total, norm in rows:
        percent = percent_of_norm(round(total, PRECIPITATION.digits), norm)
        result[wmo] = StationValue(percent, "" if percent is not None else NOTE_NO_NORM)
    return result


def _complete_years(data: MapData, wmo: int) -> list[PeriodStats]:
    return [
        stats
        for (_, month), stats in data.precipitation.get(wmo, {}).items()
        if not month and stats.complete and stats.sum is not None
    ]


def _years_note(count: int) -> str:
    return f"полных лет — {count}"


def _mean_annual_sums(data: MapData, wmo_indexes: list[int]) -> dict[int, StationValue]:
    """Средняя годовая сумма осадков по полным годам архива станции."""
    result: dict[int, StationValue] = {}
    for wmo in wmo_indexes:
        if wmo not in data.precipitation:
            continue
        sums = [stats.sum for stats in _complete_years(data, wmo) if stats.sum is not None]
        if sums:
            result[wmo] = StationValue(fmean(sums), _years_note(len(sums)))
        else:
            result[wmo] = StationValue(None, NOTE_NO_COMPLETE_YEARS)
    return result


def _mean_annual_percents(data: MapData, wmo_indexes: list[int]) -> dict[int, StationValue]:
    """Средний по полным годам архива процент годовой нормы. Отклонение
    осадков хранится в мм: норма года — сумма минус отклонение."""
    result: dict[int, StationValue] = {}
    for wmo in wmo_indexes:
        if wmo not in data.precipitation:
            continue
        years = _complete_years(data, wmo)
        if not years:
            result[wmo] = StationValue(None, NOTE_NO_COMPLETE_YEARS)
            continue
        percents = [
            percent
            for stats in years
            if stats.sum is not None and stats.anomaly is not None
            and (percent := percent_of_norm(stats.sum, round(stats.sum - stats.anomaly, PRECIPITATION.digits)))
            is not None
        ]
        if percents:
            result[wmo] = StationValue(fmean(percents), _years_note(len(percents)))
        else:
            result[wmo] = StationValue(None, NOTE_NO_NORM)
    return result


def _extremes(
    conn: Connection, where: list[ColumnElement[bool]], highest: bool
) -> dict[int, StationValue]:
    """Наибольшее (наименьшее) суточное значение станции и дни, когда оно было."""
    days = computed_indicators.c
    best = func.max(days.value) if highest else func.min(days.value)
    records = (
        select(days.wmo_index.label("wmo_index"), best.label("value")).where(*where).group_by(days.wmo_index).subquery()
    )
    rows = conn.execute(
        select(days.wmo_index, days.value, days.period_start)
        .select_from(
            computed_indicators.join(records, and_(records.c.wmo_index == days.wmo_index, records.c.value == days.value))
        )
        .where(*where)
        .order_by(days.wmo_index, days.period_start)
    ).all()
    dates: dict[int, list[date]] = {}
    values: dict[int, float] = {}
    for wmo, value, day in rows:
        values[wmo] = value
        dates.setdefault(wmo, []).append(day)
    return {wmo: StationValue(values[wmo], _format_days(dates[wmo])) for wmo in values}


def period_values(
    engine: Engine,
    data: MapData,
    indicator: str,
    date_from: date | None = None,
    date_to: date | None = None,
) -> dict[int, StationValue]:
    """Значение показателя каждой станции на карте. Без периода — за весь
    архив станции (у каждой свой), с периодом — за один и тот же для всех."""
    shown = data.shown
    result = {wmo: StationValue(None, NOTE_NO_DATA) for wmo in shown}
    if not shown:
        return result
    rules = INDICATORS[indicator].rules
    if indicator in (PRECIP_SUM, PRECIP_PERCENT) and (date_from is None or date_to is None):
        # Без периода — в среднем за год: архивы станций разной длины
        mean_annual = _mean_annual_sums if indicator == PRECIP_SUM else _mean_annual_percents
        result.update(mean_annual(data, shown))
        return result
    with engine.connect() as conn:
        if indicator in (MEAN, ANOMALY):
            where = _day_filter(rules, "avg", shown, date_from, date_to)
            result.update(_temperature_means(conn, where, with_anomaly=indicator == ANOMALY))
        elif indicator in (PRECIP_SUM, PRECIP_PERCENT):
            where = _day_filter(rules, rules.stat_type, shown, date_from, date_to)
            result.update(
                _precipitation_sums(conn, where) if indicator == PRECIP_SUM else _precipitation_percents(conn, where)
            )
        else:
            stat_type = {MAXIMUM: "max", MINIMUM: "min", PRECIP_DAY_MAX: rules.stat_type}[indicator]
            where = _day_filter(rules, stat_type, shown, date_from, date_to)
            result.update(_extremes(conn, where, highest=indicator != MINIMUM))
    return result


# --- показатель по шагам времени ---


def _step_value(indicator: str, stats: PeriodStats, incomplete: str) -> StationValue | None:
    """Значение показателя за месяц или год; None — у станции за этот шаг
    ничего нет (на шкале времени шаг для неё пуст)."""
    if indicator in (MAXIMUM, MINIMUM, PRECIP_DAY_MAX):
        value = stats.min if indicator == MINIMUM else stats.max
        return StationValue(value) if value is not None else None
    base = stats.sum if INDICATORS[indicator].rules is PRECIPITATION else stats.avg
    if base is None:
        return None
    if not stats.complete:
        return StationValue(None, incomplete)
    if indicator in (MEAN, PRECIP_SUM):
        return StationValue(base)
    if stats.anomaly is None:
        return StationValue(None, NOTE_NO_NORM)
    if indicator == ANOMALY:
        return StationValue(stats.anomaly)
    # Отклонение осадков хранится в мм: норма периода — сумма минус отклонение
    percent = percent_of_norm(base, round(base - stats.anomaly, PRECIPITATION.digits))
    return StationValue(percent, "" if percent is not None else NOTE_NO_NORM)


def _station_steps(data: MapData, wmo_index: int, indicator: str, step: str) -> dict[_StepKey, StationValue]:
    monthly = step == STEP_MONTH
    incomplete = NOTE_INCOMPLETE_MONTH if monthly else NOTE_INCOMPLETE_YEAR
    result: dict[_StepKey, StationValue] = {}
    for key, stats in data.periods_of(INDICATORS[indicator].rules).get(wmo_index, {}).items():
        if bool(key[1]) != monthly:
            continue
        value = _step_value(indicator, stats, incomplete)
        if value is not None:
            result[key] = value
    return result


def _step_label(key: _StepKey) -> str:
    year, month = key
    return f"{MONTHS_NOMINATIVE[month - 1].capitalize()} {year}" if month else str(year)


def time_series(data: MapData, indicator: str, step: str) -> list[TimeStep]:
    """Шаги ползунка времени — подряд от самого раннего до самого позднего
    года (месяца), по которому есть данные хотя бы у одной станции на карте. Шаги без
    данных внутри диапазона не выбрасываются: ползунок должен идти по
    времени равномерно, а дыра в данных — быть видна как дыра."""
    by_station = {wmo: _station_steps(data, wmo, indicator, step) for wmo in data.shown}
    keys = sorted({key for steps in by_station.values() for key in steps})
    if not keys:
        return []
    first, last = keys[0], keys[-1]
    if step == STEP_MONTH:
        timeline = [
            (year, month)
            for year in range(first[0], last[0] + 1)
            for month in range(1, 13)
            if first <= (year, month) <= last
        ]
    else:
        timeline = [(year, 0) for year in range(first[0], last[0] + 1)]
    return [
        TimeStep(
            _step_label(key),
            {wmo: steps.get(key, StationValue(None, NOTE_NO_DATA)) for wmo, steps in by_station.items()},
        )
        for key in timeline
    ]
