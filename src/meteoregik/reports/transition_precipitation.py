"""Суммы осадков за периоды между датами перехода температуры — блок
"Осадки по периодам переходов температуры" вкладки "Осадки" дашборда
(корректировка 2026-10-27, решения пользователя — в .claude/docs/task.md).
Qt-независимо, как reports/precipitation_data.py: вкладка только рисует
готовый TransitionPrecipitation.

Периоды берутся из сохранённых дат перехода (calculations/transitions.py),
по каждому прочтению метода отдельно — у прочтений даты разные, значит и
границы, и суммы:
- тёплый (выше 0 °C), вегетационный (5 °C), активной вегетации (10 °C),
  летний (15 °C) — от весенней даты перехода через порог включительно до
  дня перед осенней датой того же года. Число дней совпадает с
  длительностью в "Таблице периодов" окна переходов;
- холодный — от осенней даты перехода через 0 °C года Y до дня перед
  весенней датой года Y+1 (подпись "Y/Y+1"). Календарный год для него не
  подходит — разрезал бы одну зиму на две. Тёплый и холодный периоды
  вместе покрывают все дни подряд, без перекрытий.
Если хотя бы одной из двух дат нет ("не было" или "нет данных"), периода в
этом году нет — причина сохраняется (PeriodGap) для подсказки.

Пропуски осадков внутри периода (решение пользователя): сумма — по дням с
данными, число дней без данных показывается в подсказке; сумма такого
периода может быть занижена. Средняя многолетняя сумма — только по периодам
без пропусков (иначе ранние выгрузки rp5, где сухие дни не записаны, тянули
бы её вниз), по тому же правилу базы, что у норм: годы 1991–2020, если их
не меньше 24, иначе весь архив.

Период дашборда (с … по …) расширяется до целых периодов: показываются все
периоды, которые его задевают, с полной суммой (решение пользователя).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from statistics import fmean

from sqlalchemy.engine import Engine

from meteoregik.calculations import transitions
from meteoregik.calculations.precipitation import ROUND_DIGITS
from meteoregik.calculations.transitions import (
    AUTUMN,
    METHODS,
    SPRING,
    STATUS_DATE,
    STATUS_NONE,
    StationTransitions,
    Transition,
    _base_years,
)
from meteoregik.reports.dashboard_data import is_short_period

WARM = "warm"
COLD = "cold"
VEGETATION = "vegetation"
ACTIVE = "active"
SUMMER = "summer"
KINDS: tuple[str, ...] = (WARM, COLD, VEGETATION, ACTIVE, SUMMER)
# Вложенные периоды тёплого полугодия — от самого широкого к самому узкому
NESTED: tuple[str, ...] = (WARM, VEGETATION, ACTIVE, SUMMER)
THRESHOLD: dict[str, int] = {WARM: 0, COLD: 0, VEGETATION: 5, ACTIVE: 10, SUMMER: 15}
KIND_NAMES: dict[str, str] = {
    WARM: "тёплый период",
    COLD: "холодный период",
    VEGETATION: "вегетационный период",
    ACTIVE: "период активной вегетации",
    SUMMER: "летний период",
}
KIND_CONDITIONS: dict[str, str] = {
    WARM: "выше 0 °C",
    COLD: "ниже 0 °C",
    VEGETATION: "выше 5 °C",
    ACTIVE: "выше 10 °C",
    SUMMER: "выше 15 °C",
}


def kind_title(kind: str) -> str:
    """"Тёплый период (выше 0 °C)"."""
    name = KIND_NAMES[kind]
    return f"{name[:1].upper()}{name[1:]} ({KIND_CONDITIONS[kind]})"


def period_label(kind: str, year: int, short: bool = False) -> str:
    """Подпись года периода: "2015", у холодного — "2014/2015" ("2014/15")."""
    if kind != COLD:
        return str(year)
    return f"{year}/{(year + 1) % 100:02d}" if short else f"{year}/{year + 1}"


@dataclass(frozen=True)
class PeriodSum:
    """Сумма осадков за один период одного прочтения. year — год периода; у
    холодного — год осенней даты, с которой он начинается. end — последний
    день периода (включительно)."""

    kind: str
    method: str
    year: int
    start: date
    end: date
    value: float | None  # None — ни одного дня с данными об осадках
    days_with_data: int

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    @property
    def missing_days(self) -> int:
        return self.days - self.days_with_data

    @property
    def complete(self) -> bool:
        return self.missing_days == 0

    @property
    def label(self) -> str:
        return period_label(self.kind, self.year)

    @property
    def middle(self) -> date:
        return self.start + (self.end - self.start) / 2


@dataclass(frozen=True)
class PeriodGap:
    """Почему периода в этом году нет — для подсказки на месте пропуска."""

    kind: str
    method: str
    year: int
    reason: str


@dataclass(frozen=True)
class MeanSum:
    """Средняя многолетняя сумма периода и по каким годам она посчитана."""

    value: float
    standard: bool  # True — годы 1991–2020, False — весь архив
    years: int
    first_year: int
    last_year: int


@dataclass(frozen=True)
class TransitionPrecipitation:
    periods: tuple[PeriodSum, ...] = ()  # задевающие период дашборда, по порядку
    gaps: tuple[PeriodGap, ...] = ()
    means: dict[tuple[str, str], MeanSum] = field(default_factory=dict)  # (вид, прочтение)
    short_period: bool = False
    has_transitions: bool = False  # у станции вообще есть посчитанные даты

    def series(self, kind: str, method: str) -> list[PeriodSum]:
        return [p for p in self.periods if p.kind == kind and p.method == method]

    def gap(self, kind: str, method: str, year: int) -> PeriodGap | None:
        return next((g for g in self.gaps if (g.kind, g.method, g.year) == (kind, method, year)), None)

    def years(self, kind: str) -> list[int]:
        """Годы вида с периодом или известной причиной его отсутствия."""
        return sorted({p.year for p in self.periods if p.kind == kind} | {g.year for g in self.gaps if g.kind == kind})


# --- расчёт ---


def _status_word(transition: Transition | None) -> str:
    if transition is None:  # сезон за пределами архива
        return "нет данных"
    return "не было" if transition.status == STATUS_NONE else "нет данных"


def _bounds(
    data: StationTransitions, kind: str, method: str, year: int
) -> tuple[date, date] | str | None:
    """Границы периода или причина, почему периода нет; None — о годе
    ничего не известно (за пределами архива)."""
    threshold = THRESHOLD[kind]
    if kind == COLD:
        first, first_name = data.get(year, AUTUMN, threshold, method), "осенний"
        last, last_name = data.get(year + 1, SPRING, threshold, method), f"весенний {year + 1} года"
    else:
        first, first_name = data.get(year, SPRING, threshold, method), "весенний"
        last, last_name = data.get(year, AUTUMN, threshold, method), "осенний"
    if first is None and last is None:
        return None
    if first is not None and last is not None and first.day is not None and last.day is not None:
        if last.day > first.day:
            return first.day, last.day - timedelta(days=1)
    problems = [
        f"{name} переход через {threshold} °C — {_status_word(item)}"
        for name, item in ((first_name, first), (last_name, last))
        if item is None or item.status != STATUS_DATE
    ]
    return "; ".join(problems) if problems else None


def _sum(days: dict[date, float], start: date, end: date) -> tuple[float | None, int]:
    total, count = 0.0, 0
    day = start
    while day <= end:
        value = days.get(day)
        if value is not None:
            total += value
            count += 1
        day += timedelta(days=1)
    return (round(total, ROUND_DIGITS) if count else None), count


def all_periods(
    data: StationTransitions, days: dict[date, float]
) -> tuple[list[PeriodSum], list[PeriodGap]]:
    """Все периоды архива станции (по всем годам с датами) и причины пропусков."""
    periods: list[PeriodSum] = []
    gaps: list[PeriodGap] = []
    for kind in KINDS:
        for method in METHODS:
            for year in data.years:
                bounds = _bounds(data, kind, method, year)
                if bounds is None:
                    continue
                if isinstance(bounds, str):
                    gaps.append(PeriodGap(kind, method, year, bounds))
                    continue
                value, count = _sum(days, *bounds)
                periods.append(PeriodSum(kind, method, year, bounds[0], bounds[1], value, count))
    return periods, gaps


def mean_sums(periods: list[PeriodSum]) -> dict[tuple[str, str], MeanSum]:
    """Средняя многолетняя сумма каждого вида и прочтения — по полным периодам."""
    result = {}
    for kind in KINDS:
        for method in METHODS:
            values = {
                p.year: p.value for p in periods
                if p.kind == kind and p.method == method and p.complete and p.value is not None
            }
            if not values:
                continue
            standard, years = _base_years(sorted(values))
            result[(kind, method)] = MeanSum(fmean(values[y] for y in years), standard, len(years), years[0], years[-1])
    return result


def _gap_span(kind: str, year: int) -> tuple[date, date]:
    """Где мог бы лежать период, которого нет, — по окнам поиска дат."""
    if kind == COLD:
        return transitions.season_window(year, AUTUMN)[0], transitions.season_window(year + 1, SPRING)[1]
    return transitions.season_window(year, SPRING)[0], transitions.season_window(year, AUTUMN)[1]


def compute_transition_precipitation(
    data: StationTransitions, days: dict[date, float], date_from: date, date_to: date
) -> TransitionPrecipitation:
    """days — суточные суммы осадков всей истории станции (средние — по ней,
    а не по периоду дашборда)."""
    periods, gaps = all_periods(data, days)
    shown = sorted(
        (p for p in periods if p.start <= date_to and p.end >= date_from),
        key=lambda p: (p.start, KINDS.index(p.kind), METHODS.index(p.method)),
    )
    shown_gaps = [g for g in gaps if (span := _gap_span(g.kind, g.year))[0] <= date_to and span[1] >= date_from]
    return TransitionPrecipitation(
        periods=tuple(shown),
        gaps=tuple(shown_gaps),
        means=mean_sums(periods),
        short_period=is_short_period(date_from, date_to),
        has_transitions=bool(data.transitions),
    )


def build_transition_precipitation(
    engine: Engine, wmo_index: int, days: dict[date, float], date_from: date, date_to: date
) -> TransitionPrecipitation:
    """Даты переходов — сохранённые; сезоны, требующие расчёта, сначала
    досчитываются (как при открытии окна "Переходы температуры")."""
    transitions.ensure_fresh(engine, [wmo_index])
    data = transitions.load_station_transitions(engine, wmo_index)
    return compute_transition_precipitation(data, days, date_from, date_to)


# --- подписи ---


def format_period(period: PeriodSum) -> str:
    return f"{period.start:%d.%m.%Y}–{period.end:%d.%m.%Y}"
