"""Данные экрана "Карта" (2026-10-10): станции справочника с координатами,
что по ним залито (reports/source_data.py) и значения температурных
показателей по станциям — за период и по шагам времени (годы, месяцы) для
ползунка. Qt-независимо; цвета, подписи и всплывающие карточки маркеров —
в reports/map_render.py.

Нормы, полнота и отклонения берутся сохранённые (calculations/anomalies.py,
решение пользователя 2026-10-10): перед чтением fetch_map_data досчитывает
станции, чьи данные менялись после последнего расчёта. Суточные значения в
память не читаются — раньше карта держала их по всем станциям и считала
всё сама, что на десятках станций стало бы заметной паузой:
- ряды по годам и месяцам — готовые строки month/year из
  computed_indicators (avg / min / max / anomaly) и period_completeness;
- значение за произвольный период — агрегаты SQL по суточным строкам;
  отклонение — среднее (суточная − норма её месяца), то же определение, что
  у карточки дашборда, поэтому числа на карте и в дашборде совпадают.

Полнота. Средняя за год и её отклонение от нормы показываются только для
полных лет (как рекорды и аномалии дашборда): "год" из одного января дал бы
на карте ложно холодную станцию. Месяцы — только полные (правило ВМО "3/5").
Абсолютные максимум и минимум — по тем дням, что есть: рекорд не становится
неверным оттого, что в году есть пропуски.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import Integer, and_, cast, func, select
from sqlalchemy.engine import Engine

from meteoregik.calculations.anomalies import PARAMETER, STAT_ANOMALY, ensure_fresh
from meteoregik.db.schema import computed_indicators, period_completeness, ru_regions, station_norms, stations
from meteoregik.reports.dashboard_data import MONTHS_NOMINATIVE
from meteoregik.reports.source_data import SourceOverview, StationSourceRow, fetch_source_overview
from meteoregik.stations.choices import _REGION_JOIN, StationChoice

MEAN = "mean"
ANOMALY = "anomaly"
MAXIMUM = "max"
MINIMUM = "min"
INDICATOR_LABELS: dict[str, str] = {
    MEAN: "Средняя температура",
    ANOMALY: "Отклонение от нормы",
    MAXIMUM: "Абсолютный максимум",
    MINIMUM: "Абсолютный минимум",
}

STEP_YEAR = "year"
STEP_MONTH = "month"

NOTE_INCOMPLETE_YEAR = "неполный год"
NOTE_INCOMPLETE_MONTH = "неполный месяц"
NOTE_NO_DATA = "нет данных"
NOTE_NO_NORM = "норма не рассчитана"


@dataclass(frozen=True)
class MapStation:
    wmo_index: int
    label: str
    lat: float
    lon: float


_StepKey = tuple[int, int]  # (год, месяц); у годового шага месяц = 0


@dataclass
class PeriodStats:
    """Сохранённые значения станции за один месяц или год."""

    avg: float | None = None
    min: float | None = None
    max: float | None = None
    anomaly: float | None = None
    complete: bool = False


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
    # Месячные и годовые значения станций с посчитанными данными
    periods: dict[int, dict[_StepKey, PeriodStats]] = field(default_factory=dict)

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
        станции ничего нет — корректировка 2026-10-10)."""
        located = self.located
        return [wmo for wmo in self.periods if wmo in located]


def _fetch_periods(engine: Engine) -> dict[int, dict[_StepKey, PeriodStats]]:
    values = select(
        computed_indicators.c.wmo_index,
        computed_indicators.c.period_type,
        computed_indicators.c.period_start,
        computed_indicators.c.stat_type,
        computed_indicators.c.value,
    ).where(
        computed_indicators.c.parameter == PARAMETER,
        computed_indicators.c.period_type.in_(("month", "year")),
        computed_indicators.c.stat_type.in_(("avg", "min", "max", STAT_ANOMALY)),
        computed_indicators.c.value.is_not(None),
    )
    complete = select(
        period_completeness.c.wmo_index, period_completeness.c.period_type, period_completeness.c.period_start
    ).where(period_completeness.c.parameter == PARAMETER, period_completeness.c.complete.is_(True))

    def key(period_type: str, start: date) -> _StepKey:
        return (start.year, start.month if period_type == "month" else 0)

    result: dict[int, dict[_StepKey, PeriodStats]] = {}
    with engine.connect() as conn:
        for wmo, period_type, start, stat_type, value in conn.execute(values):
            stats = result.setdefault(wmo, {}).setdefault(key(period_type, start), PeriodStats())
            setattr(stats, stat_type, value)
        for wmo, period_type, start in conn.execute(complete):
            stats = result.get(wmo, {}).get(key(period_type, start))
            if stats is not None:
                stats.complete = True
    return result


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
    return MapData(located, {row.wmo_index: row for row in overview.stations}, _fetch_periods(engine))


# --- показатель за период ---


def _format_days(days: list[date]) -> str:
    text = f"{days[0]:%d.%m.%Y}"
    return f"{text} и ещё {len(days) - 1} дн." if len(days) > 1 else text


def _day_filter(stat_type: str, wmo_indexes: list[int], date_from: date | None, date_to: date | None) -> list:
    days = computed_indicators.c
    conditions = [
        days.period_type == "day",
        days.parameter == PARAMETER,
        days.stat_type == stat_type,
        days.value.is_not(None),
        days.wmo_index.in_(wmo_indexes),
    ]
    if date_from is not None and date_to is not None:
        conditions.append(days.period_start.between(date_from, date_to))
    return conditions


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
    days = computed_indicators.c
    with engine.connect() as conn:
        if indicator in (MEAN, ANOMALY):
            where = _day_filter("avg", shown, date_from, date_to)
            means = dict(
                conn.execute(select(days.wmo_index, func.avg(days.value)).where(*where).group_by(days.wmo_index)).all()
            )
            if indicator == MEAN:
                result.update({wmo: StationValue(value) for wmo, value in means.items()})
                return result
            norm_of_month = and_(
                station_norms.c.wmo_index == days.wmo_index,
                station_norms.c.parameter == PARAMETER,
                station_norms.c.period_type == "month",
                # period_start хранится в SQLite текстом "ГГГГ-ММ-ДД"
                station_norms.c.month == cast(func.strftime("%m", days.period_start), Integer),
            )
            anomalies = dict(
                conn.execute(
                    select(days.wmo_index, func.avg(days.value - station_norms.c.value))
                    .select_from(computed_indicators.join(station_norms, norm_of_month))
                    .where(*where)
                    .group_by(days.wmo_index)
                ).all()
            )
            for wmo in means:
                anomaly = anomalies.get(wmo)
                result[wmo] = StationValue(anomaly, "" if anomaly is not None else NOTE_NO_NORM)
            return result

        stat_type = "max" if indicator == MAXIMUM else "min"
        where = _day_filter(stat_type, shown, date_from, date_to)
        best = func.max(days.value) if indicator == MAXIMUM else func.min(days.value)
        records = (
            select(days.wmo_index.label("wmo_index"), best.label("value"))
            .where(*where)
            .group_by(days.wmo_index)
            .subquery()
        )
        rows = conn.execute(
            select(days.wmo_index, days.value, days.period_start)
            .select_from(
                computed_indicators.join(
                    records, and_(records.c.wmo_index == days.wmo_index, records.c.value == days.value)
                )
            )
            .where(*where)
            .order_by(days.wmo_index, days.period_start)
        ).all()
    dates: dict[int, list[date]] = {}
    values: dict[int, float] = {}
    for wmo, value, day in rows:
        values[wmo] = value
        dates.setdefault(wmo, []).append(day)
    result.update({wmo: StationValue(values[wmo], _format_days(dates[wmo])) for wmo in values})
    return result


# --- показатель по шагам времени ---

def _station_steps(data: MapData, wmo_index: int, indicator: str, step: str) -> dict[_StepKey, StationValue]:
    monthly = step == STEP_MONTH
    incomplete = NOTE_INCOMPLETE_MONTH if monthly else NOTE_INCOMPLETE_YEAR
    result: dict[_StepKey, StationValue] = {}
    for key, stats in data.periods[wmo_index].items():
        if bool(key[1]) != monthly:
            continue
        if indicator in (MAXIMUM, MINIMUM):
            value = stats.max if indicator == MAXIMUM else stats.min
            if value is not None:
                result[key] = StationValue(value)
        elif stats.avg is None:
            continue
        elif not stats.complete:
            result[key] = StationValue(None, incomplete)
        elif indicator == MEAN:
            result[key] = StationValue(stats.avg)
        else:
            result[key] = StationValue(stats.anomaly, "" if stats.anomaly is not None else NOTE_NO_NORM)
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
