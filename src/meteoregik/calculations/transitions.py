"""Даты устойчивого перехода средней суточной температуры воздуха через
0, 5, 10 и 15 °C весной и осенью — расчёт и хранение (корректировка
2026-10-25, правила согласованы с пользователем 2026-10-26; разбор — в
.claude/docs/task.md, "корректировки").

Метод Д. А. Педя (метод сумм отклонений) в формулировке Гидрометцентра:
устойчивый переход — день, после которого обратного перехода не было, а
если был, то накопленное до него тепло (весной) больше холода возврата.
По шагам, весна:
- отклонение дня — t − порог; дни подряд одного знака — период: тёплый
  (t > порог) или холодный (t ≤ порог — ровно на пороге значит "не выше");
- кандидат — первый день тёплого периода. От него копятся все
  положительные отклонения; перед каждым последующим холодным периодом окна
  накопленное должно быть строго больше суммы этого периода по модулю.
  Отрицательные отклонения прежних похолоданий из накопленного не вычитаются;
- дата перехода — первый кандидат, выдержавший все похолодания окна.
Осень — то же с обратными знаками: кандидат — первый день холодного
периода, копится холод (порог − t), сравнивается с каждым последующим
тёплым периодом.

Перехода не было ('none'), если сезон с самого начала окна уже по ту
сторону порога и там остался (кандидат, выдержавший всё, — первый период
окна: весной не было зимы ниже порога, осенью — лета выше), если по ту
сторону порога не было ни одного дня или если ни один кандидат не выдержал.

Окно поиска: весна года — 1 января – 31 июля, осень года — 1 июля – конец
февраля следующего года (на юге переход через 0 °C бывает в январе).
Дата считается, только если все месяцы окна полные по правилу ВМО "3/5"
(anomalies.month_is_complete — то же правило, что у норм); иначе
'no_data'. Дни без данных внутри полного месяца просто пропускаются:
периоды по обе стороны пропуска не рвутся.

Когда считается. Не при заливке: calculations/recompute.py только удаляет
строки transition_seasons у сезонов, в окно которых попали изменённые дни
(mark_stale). Нет строки сезона — сезон требует расчёта; так же выглядит и
база прошлой версии, где переходов ещё не было, поэтому миграция ничего не
пересчитывает. Считает ensure_fresh — при открытии окна "Переходы
температуры", только требующие расчёта сезоны.
"""

from __future__ import annotations

from calendar import monthrange
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from statistics import fmean

from sqlalchemy import and_, delete, func, or_, select, tuple_
from sqlalchemy.engine import Connection, Engine

from meteoregik.calculations.anomalies import (
    MIN_NORM_BASE_YEARS,
    NORM_BASE_FIRST_YEAR,
    NORM_BASE_LAST_YEAR,
    month_is_complete,
)
from meteoregik.db.schema import computed_indicators, temperature_transitions, transition_seasons, transition_state

THRESHOLDS: tuple[int, ...] = (0, 5, 10, 15)
SPRING = "spring"
AUTUMN = "autumn"
SEASONS: tuple[str, ...] = (SPRING, AUTUMN)

STATUS_DATE = "date"
STATUS_NONE = "none"
STATUS_NO_DATA = "no_data"

# Меняется при изменении правил: сезоны, посчитанные по старым, считаются
# требующими расчёта.
RULE_VERSION = 1

SeasonKey = tuple[int, str]  # (год, сезон)


# --- окно поиска ---


def season_window(year: int, season: str) -> tuple[date, date]:
    if season == SPRING:
        return date(year, 1, 1), date(year, 7, 31)
    return date(year, 7, 1), date(year + 1, 2, monthrange(year + 1, 2)[1])


def seasons_touching(start: date, end: date) -> list[SeasonKey]:
    """Сезоны, в окно поиска которых попадает хотя бы один день [start, end]."""
    result = []
    for year in range(start.year - 1, end.year + 1):
        for season in SEASONS:
            first, last = season_window(year, season)
            if first <= end and start <= last:
                result.append((year, season))
    return result


def window_months(year: int, season: str) -> list[tuple[int, int]]:
    first, last = season_window(year, season)
    months = []
    current = (first.year, first.month)
    while current <= (last.year, last.month):
        months.append(current)
        current = (current[0] + 1, 1) if current[1] == 12 else (current[0], current[1] + 1)
    return months


def window_is_complete(year: int, season: str, days: dict[date, float]) -> bool:
    """Все месяцы окна полные по правилу "3/5"."""
    for y, m in window_months(year, season):
        present = {day for day in range(1, monthrange(y, m)[1] + 1) if date(y, m, day) in days}
        if not month_is_complete(present, monthrange(y, m)[1]):
            return False
    return True


# --- метод сумм отклонений ---


@dataclass(frozen=True)
class _Run:
    """Период: дни подряд по одну сторону порога."""

    candidate: bool  # сторона, куда идёт переход (весной — тёплая, осенью — холодная)
    start: date
    weight: float  # сумма отклонений от порога, по модулю


def _runs(values: list[tuple[date, float]], threshold: float, season: str) -> list[_Run]:
    runs: list[_Run] = []
    for day, value in values:
        warm = value > threshold
        candidate = warm if season == SPRING else not warm
        weight = abs(value - threshold)
        if runs and runs[-1].candidate == candidate:
            last = runs[-1]
            runs[-1] = _Run(candidate, last.start, last.weight + weight)
        else:
            runs.append(_Run(candidate, day, weight))
    return runs


def find_transition(values: list[tuple[date, float]], threshold: float, season: str) -> date | None:
    """Дата устойчивого перехода по суточным значениям окна (по порядку дат,
    без пропусков в списке); None — перехода не было."""
    runs = _runs(values, threshold, season)
    for index, run in enumerate(runs):
        if not run.candidate:
            continue
        accumulated = 0.0
        stable = True
        for later in runs[index:]:
            if later.candidate:
                accumulated += later.weight
            elif accumulated <= later.weight:
                stable = False
                break
        if stable:
            # Кандидат с самого начала окна — сезон уже был по ту сторону
            # порога: весной не было зимы ниже него, осенью — лета выше
            return None if index == 0 else run.start
    return None


@dataclass(frozen=True)
class Transition:
    status: str
    day: date | None = None


def season_transitions(year: int, season: str, days: dict[date, float]) -> dict[int, Transition]:
    """Переходы сезона через все пороги по суточным средним станции."""
    if not window_is_complete(year, season, days):
        return {threshold: Transition(STATUS_NO_DATA) for threshold in THRESHOLDS}
    first, last = season_window(year, season)
    values = sorted((day, value) for day, value in days.items() if first <= day <= last)
    result = {}
    for threshold in THRESHOLDS:
        day = find_transition(values, threshold, season)
        result[threshold] = Transition(STATUS_DATE, day) if day else Transition(STATUS_NONE)
    return result


# --- чтение ---


def _day_avg_filter() -> object:
    return and_(
        computed_indicators.c.parameter == "T",
        computed_indicators.c.period_type == "day",
        computed_indicators.c.stat_type == "avg",
        computed_indicators.c.value.is_not(None),
    )


def fetch_daily_means(engine: Engine, wmo_index: int) -> dict[date, float]:
    stmt = select(computed_indicators.c.period_start, computed_indicators.c.value).where(
        computed_indicators.c.wmo_index == wmo_index, _day_avg_filter()
    )
    with engine.connect() as conn:
        return {day: value for day, value in conn.execute(stmt)}


def _station_periods(conn: Connection) -> dict[int, tuple[date, date]]:
    """Станции со средней суточной температурой и границы её архива."""
    stmt = (
        select(
            computed_indicators.c.wmo_index,
            func.min(computed_indicators.c.period_start),
            func.max(computed_indicators.c.period_start),
        )
        .where(_day_avg_filter())
        .group_by(computed_indicators.c.wmo_index)
    )
    return {wmo: (first, last) for wmo, first, last in conn.execute(stmt)}


def expected_seasons(period: tuple[date, date]) -> list[SeasonKey]:
    """Сезоны, которые показывает таблица станции: весна и осень каждого года
    архива (осень последнего года — тоже, пусть и "недостаточно данных")."""
    return [(year, season) for year in range(period[0].year, period[1].year + 1) for season in SEASONS]


# --- хранение ---


def mark_stale(conn: Connection, wmo_index: int, start: date, end: date) -> None:
    """Пометить требующими расчёта сезоны, в окно которых попали дни
    [start, end]: их суточные значения изменились."""
    keys = seasons_touching(start, end)
    conn.execute(
        delete(transition_seasons).where(
            transition_seasons.c.wmo_index == wmo_index,
            tuple_(transition_seasons.c.year, transition_seasons.c.season).in_(keys),
        )
    )


def _fresh_seasons(conn: Connection) -> dict[int, set[SeasonKey]]:
    stmt = select(transition_seasons.c.wmo_index, transition_seasons.c.year, transition_seasons.c.season).where(
        transition_seasons.c.rule_version == RULE_VERSION
    )
    result: dict[int, set[SeasonKey]] = {}
    for wmo, year, season in conn.execute(stmt):
        result.setdefault(wmo, set()).add((year, season))
    return result


@dataclass(frozen=True)
class StaleStation:
    wmo_index: int
    seasons: list[SeasonKey]


def stale_stations(engine: Engine, wmo_indexes: Iterable[int] | None = None) -> list[StaleStation]:
    """Станции с сезонами, требующими расчёта, и сами сезоны."""
    with engine.connect() as conn:
        periods = _station_periods(conn)
        fresh = _fresh_seasons(conn)
    wanted = set(wmo_indexes) if wmo_indexes is not None else None
    result = []
    for wmo, period in sorted(periods.items()):
        if wanted is not None and wmo not in wanted:
            continue
        done = fresh.get(wmo, set())
        todo = [key for key in expected_seasons(period) if key not in done]
        if todo:
            result.append(StaleStation(wmo, todo))
    return result


def _seasons_word(count: int) -> str:
    tail = count % 100
    if 11 <= tail <= 14 or tail % 10 in (0, 5, 6, 7, 8, 9):
        return "сезонов"
    return "сезон" if tail % 10 == 1 else "сезона"


def _season_text(seasons: list[SeasonKey]) -> str:
    """Что пересчитано, словами: "весна и осень 2016, весна 2017"; много
    сезонов — числом и границами лет."""
    if len(seasons) > 6:
        years = [year for year, _ in seasons]
        return f"{len(seasons)} {_seasons_word(len(seasons))}, {min(years)}–{max(years)}"
    by_year: dict[int, list[str]] = {}
    for year, season in sorted(seasons, key=lambda key: (key[0], SEASONS.index(key[1]))):
        by_year.setdefault(year, []).append("весна" if season == SPRING else "осень")
    return ", ".join(f"{' и '.join(names)} {year}" for year, names in by_year.items())


def recalculate_seasons(engine: Engine, wmo_index: int, seasons: list[SeasonKey]) -> None:
    """Пересчитать и сохранить переходы сезонов станции одной транзакцией."""
    days = fetch_daily_means(engine, wmo_index)
    rows = []
    for year, season in seasons:
        for threshold, transition in season_transitions(year, season, days).items():
            rows.append({
                "wmo_index": wmo_index, "year": year, "season": season, "threshold": threshold,
                "status": transition.status, "transition_date": transition.day,
            })
    now = datetime.now()
    keys = tuple_(temperature_transitions.c.year, temperature_transitions.c.season).in_(seasons)
    with engine.begin() as conn:
        conn.execute(delete(temperature_transitions).where(temperature_transitions.c.wmo_index == wmo_index, keys))
        conn.execute(
            delete(transition_seasons).where(
                transition_seasons.c.wmo_index == wmo_index,
                tuple_(transition_seasons.c.year, transition_seasons.c.season).in_(seasons),
            )
        )
        if rows:
            conn.execute(temperature_transitions.insert(), rows)
        conn.execute(
            transition_seasons.insert(),
            [{"wmo_index": wmo_index, "year": year, "season": season, "calculated_at": now,
              "rule_version": RULE_VERSION} for year, season in seasons],
        )
        conn.execute(delete(transition_state).where(transition_state.c.wmo_index == wmo_index))
        conn.execute(
            transition_state.insert().values(wmo_index=wmo_index, calculated_at=now, recalculated=_season_text(seasons))
        )


def _purge_outdated(engine: Engine) -> None:
    """Убрать сохранённое по сезонам вне архива станции (данные удалили) и
    по станциям, у которых данных не осталось."""
    with engine.begin() as conn:
        periods = _station_periods(conn)
        conn.execute(delete(transition_state).where(transition_state.c.wmo_index.not_in(list(periods))))
        for table in (temperature_transitions, transition_seasons):
            conn.execute(delete(table).where(table.c.wmo_index.not_in(list(periods))))
            for wmo, (first, last) in periods.items():
                conn.execute(
                    delete(table).where(
                        table.c.wmo_index == wmo, or_(table.c.year < first.year, table.c.year > last.year)
                    )
                )


def ensure_fresh(
    engine: Engine,
    wmo_indexes: Iterable[int] | None = None,
    progress: Callable[[int, int, int], None] | None = None,
) -> list[StaleStation]:
    """Досчитать сезоны, требующие расчёта, и вернуть, что посчитано.
    wmo_indexes ограничивает круг станций; None — все, и тогда же убирается
    сохранённое по удалённым данным. progress(номер, всего, wmo_index) —
    перед расчётом каждой станции."""
    if wmo_indexes is None:
        _purge_outdated(engine)
    todo = stale_stations(engine, wmo_indexes)
    for number, station in enumerate(todo, start=1):
        if progress is not None:
            progress(number, len(todo), station.wmo_index)
        recalculate_seasons(engine, station.wmo_index, station.seasons)
    return todo


# --- результат для экранов ---


@dataclass(frozen=True)
class StationTransitions:
    """Сохранённые переходы станции: (год, сезон, порог) -> переход."""

    years: list[int]
    transitions: dict[tuple[int, str, int], Transition]

    def get(self, year: int, season: str, threshold: int) -> Transition | None:
        return self.transitions.get((year, season, threshold))

    def duration(self, year: int, threshold: int) -> int | None:
        """Длительность периода выше порога, дни: осенняя дата − весенняя."""
        spring, autumn = self.get(year, SPRING, threshold), self.get(year, AUTUMN, threshold)
        if spring is None or autumn is None or spring.day is None or autumn.day is None:
            return None
        return (autumn.day - spring.day).days


def load_station_transitions(engine: Engine, wmo_index: int) -> StationTransitions:
    stmt = select(
        temperature_transitions.c.year,
        temperature_transitions.c.season,
        temperature_transitions.c.threshold,
        temperature_transitions.c.status,
        temperature_transitions.c.transition_date,
    ).where(temperature_transitions.c.wmo_index == wmo_index)
    with engine.connect() as conn:
        rows = conn.execute(stmt).all()
    transitions = {(year, season, threshold): Transition(status, day) for year, season, threshold, status, day in rows}
    return StationTransitions(sorted({year for year, _, _ in transitions}), transitions)


# --- средняя многолетняя дата ---

# Календарь без 29 февраля для перевода дат в порядковые дни: средняя по
# годам не должна сдвигаться на день из-за високосных лет.
_SPRING_ORIGIN = date(2001, 1, 1)
_AUTUMN_ORIGIN = date(2001, 7, 1)


def _offset(day: date, season: str) -> int:
    day_of_month = min(day.day, 28) if day.month == 2 else day.day
    if season == SPRING:
        return (date(2001, day.month, day_of_month) - _SPRING_ORIGIN).days
    year = 2001 if day.month >= 7 else 2002
    return (date(year, day.month, day_of_month) - _AUTUMN_ORIGIN).days


def _from_offset(offset: int, season: str) -> date:
    """Дата средней в календаре 2001 года (у осени — 2001/2002): нужны только
    день и месяц."""
    return (_SPRING_ORIGIN if season == SPRING else _AUTUMN_ORIGIN) + timedelta(days=offset)


@dataclass(frozen=True)
class MeanValue:
    """Средняя многолетняя дата (день и месяц в day) или длительность и по
    каким годам она посчитана."""

    standard: bool  # True — годы 1991–2020, False — весь архив
    years: int
    first_year: int
    last_year: int
    none_years: int  # лет "перехода не было" в той же базе — в среднюю не входят
    day: date | None = None
    days: float | None = None


def _base_years(years: list[int]) -> tuple[bool, list[int]]:
    """То же правило, что у норм: годы 1991–2020, если их не меньше 24,
    иначе все годы архива."""
    standard = [year for year in years if NORM_BASE_FIRST_YEAR <= year <= NORM_BASE_LAST_YEAR]
    if len(standard) >= MIN_NORM_BASE_YEARS:
        return True, standard
    return False, years


def _none_years(standard: bool, none_years: list[int]) -> int:
    if not standard:
        return len(none_years)
    return sum(NORM_BASE_FIRST_YEAR <= year <= NORM_BASE_LAST_YEAR for year in none_years)


def mean_date(data: StationTransitions, season: str, threshold: int) -> MeanValue | None:
    dated = {}
    none_years = []
    for year in data.years:
        transition = data.get(year, season, threshold)
        if transition is None:
            continue
        if transition.day is not None:
            dated[year] = transition.day
        elif transition.status == STATUS_NONE:
            none_years.append(year)
    if not dated:
        return None
    standard, years = _base_years(sorted(dated))
    offset = round(fmean(_offset(dated[year], season) for year in years))
    return MeanValue(
        standard, len(years), years[0], years[-1], _none_years(standard, none_years), day=_from_offset(offset, season)
    )


def mean_duration(data: StationTransitions, threshold: int) -> MeanValue | None:
    durations = {year: value for year in data.years if (value := data.duration(year, threshold)) is not None}
    if not durations:
        return None
    none_years = [
        year for year in data.years
        if any((t := data.get(year, season, threshold)) is not None and t.status == STATUS_NONE for season in SEASONS)
    ]
    standard, years = _base_years(sorted(durations))
    return MeanValue(
        standard, len(years), years[0], years[-1], _none_years(standard, none_years),
        days=fmean(durations[year] for year in years),
    )


# --- состояние для вкладки "Состояние расчёта" ---


@dataclass(frozen=True)
class StationState:
    wmo_index: int
    period: tuple[date, date]
    calculated_at: datetime | None
    recalculated: str
    years_with_dates: int  # лет, где есть хотя бы одна дата перехода
    no_data_seasons: dict[str, int]  # сезонов "недостаточно данных" по виду сезона


def station_states(engine: Engine) -> list[StationState]:
    with engine.connect() as conn:
        periods = _station_periods(conn)
        states = {row.wmo_index: row for row in conn.execute(select(transition_state))}
        dated = dict(
            conn.execute(
                select(temperature_transitions.c.wmo_index, func.count(temperature_transitions.c.year.distinct()))
                .where(temperature_transitions.c.status == STATUS_DATE)
                .group_by(temperature_transitions.c.wmo_index)
            ).all()
        )
        no_data: dict[int, dict[str, int]] = {}
        stmt = (
            select(
                temperature_transitions.c.wmo_index,
                temperature_transitions.c.season,
                func.count(temperature_transitions.c.year.distinct()),
            )
            .where(temperature_transitions.c.status == STATUS_NO_DATA)
            .group_by(temperature_transitions.c.wmo_index, temperature_transitions.c.season)
        )
        for wmo, season, count in conn.execute(stmt):
            no_data.setdefault(wmo, {})[season] = count
    result = []
    for wmo, period in sorted(periods.items()):
        state = states.get(wmo)
        result.append(StationState(
            wmo, period,
            state.calculated_at if state else None,
            state.recalculated if state else "",
            dated.get(wmo, 0),
            no_data.get(wmo, {}),
        ))
    return result
