"""Данные вкладки "Осадки" экрана "Дашборд" (корректировка 2026-10-11,
этап 3): карточки-сводки, теплокарта "год × месяц", годовые суммы
относительно нормы с трендом, суммы по сезонам и годовой ход. Qt-независимо,
как reports/dashboard_data.py: вкладка только рисует готовый
PrecipitationData.

Суммы, нормы и полнота берутся сохранённые (calculations/precipitation.py,
calculations/anomalies.py) — здесь ничего из сырых данных не считается,
кроме дней с пропущенным сроком наблюдения.

Как и у температуры, рекорды (самый влажный/сухой год и месяц) и ряд годовых
сумм — только по ПОЛНЫМ месяцам и годам, целиком лежащим внутри периода:
сумма неполного месяца занижена, и он оказался бы "самым сухим" из-за
пропусков, а не из-за погоды. Теплокарта и сезоны — по пересечению с
периодом, неполные помечаются.

Короткий период (меньше 24 месяцев, тот же порог, что у температуры —
dashboard_data.is_short_period; решение пользователя 2026-10-13): полных лет
в нём не больше одного, поэтому вместо самого влажного/сухого года — декады,
теплокарта — месяцы × декады, а график годовых сумм — по месяцам.

Сравнение суммы за период с нормой (процент нормы). Сравниваются только
дни периода, попавшие в ПОЛНЫЕ месяцы (правило "3/5"; решение пользователя
2026-10-17): сумма этих дней — к сумме их суточных долей месячных норм
(норма месяца / число дней в нём). Доли нужны, чтобы сравнение было
корректно для любого периода (лето сравнивается с летней нормой, полмесяца —
с половиной нормы месяца). Неполные месяцы не участвуют: в ранних выгрузках
rp5 сухие дни часто не записаны вовсе, в таком месяце остаются почти одни
дни с осадками, и сравнение "по дням с данными" давало завышенный процент
(Мосальск 2005–2013: 155 % нормы при обычных по сумме годах). Сама сумма за
период — по всем дням с данными, как и раньше.

День с осадками — сутки с суммой не меньше WET_DAY_MM (1 мм): порог ВМО для
"влажного дня", он отсекает следы осадков и росу (0.1–0.9 мм), от которых
число дней зависело бы от аккуратности наблюдателя. Период без осадков —
подряд идущие дни с суммой меньше того же порога; день без данных период
обрывает (что в него выпало — неизвестно).

Пропуски двух видов (решение пользователя 2026-10-12): пропущенный день —
суток нет в данных об осадках вовсе, суммы нет; пропущенный срок — день rp5,
где записан только один из двух основных сроков: сумма есть (по тому, что
записано), день считается полным, но может быть занижен. Оба числа
показывает карточка полноты.
"""

from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass, field
from datetime import date, timedelta
from statistics import fmean

from sqlalchemy import func, select
from sqlalchemy.engine import Engine

from meteoregik.calculations import precipitation
from meteoregik.calculations.anomalies import (
    PRECIPITATION,
    NormInfo,
    StationNorms,
    StationValues,
    compute_norms,
    days_by_month,
    decade_is_complete,
    decade_values,
    ensure_fresh,
    fetch_station_values,
    load_station_norms,
)
from meteoregik.calculations.temperature import decade_bounds
from meteoregik.db.schema import computed_indicators
from meteoregik.reports.dashboard_data import (
    DatedValue,
    Trend,
    _extreme,
    _month_bounds,
    _ties,
    is_short_period,
    linear_trend,
)

# Сутки с суммой не меньше этой — "день с осадками" (порог ВМО)
WET_DAY_MM = 1.0

# Цвета осадков — общие для теплокарты вкладки "Осадки" и экрана "Карта":
# сумма — один тон "светлый → тёмный", процент нормы — расходящаяся шкала
# "сухо (охра) ↔ влажно (сине-зелёный)" с нейтральной серединой в 100 %
SUM_STOPS = ("#f0efec", "#bfe0dc", "#5fb3aa", "#1f7f78", "#0b4a47")
PERCENT_STOPS = ("#5e3a08", "#b9772a", "#e6c995", "#f0efec", "#9fd3cc", "#2a8c82", "#0b4a47")
# Предел шкалы "% нормы": всё, что выше, — самым тёмным цветом
PERCENT_SCALE_MAX = 200.0


def percent_of_norm(value: float, norm: float | None) -> float | None:
    """Сумма в процентах нормы; None — нормы нет или она нулевая (в сухой
    месяц пустыни делить не на что)."""
    return value / norm * 100 if norm else None


@dataclass(frozen=True)
class MonthSum:
    """Ячейка теплокарты и рекорд месяца: сумма осадков, полнота и норма
    этого календарного месяца."""

    year: int
    month: int
    value: float
    days_with_data: int
    days_in_month: int
    complete: bool
    norm: float | None = None

    @property
    def anomaly(self) -> float | None:
        return self.value - self.norm if self.norm is not None else None

    @property
    def percent(self) -> float | None:
        return percent_of_norm(self.value, self.norm)


@dataclass(frozen=True)
class DecadeSum:
    """Декада (1–10, 11–20, 21–конец месяца): сумма, полнота и норма той же
    декады того же месяца — ячейка теплокарты и рекорд короткого периода."""

    start: date  # первый день декады
    value: float
    days_with_data: int
    days_in_decade: int
    complete: bool
    norm: float | None = None

    @property
    def number(self) -> int:
        return (self.start.day - 1) // 10 + 1

    @property
    def anomaly(self) -> float | None:
        return self.value - self.norm if self.norm is not None else None

    @property
    def percent(self) -> float | None:
        return percent_of_norm(self.value, self.norm)


@dataclass(frozen=True)
class YearSum:
    """Сумма полного года и годовая норма."""

    year: int
    value: float
    norm: float | None = None

    @property
    def anomaly(self) -> float | None:
        return self.value - self.norm if self.norm is not None else None

    @property
    def percent(self) -> float | None:
        return percent_of_norm(self.value, self.norm)


@dataclass(frozen=True)
class SeasonSum:
    """Сумма сезона. start — его первый день (1 декабря/марта/июня/сентября)."""

    start: date
    value: float
    complete: bool
    norm: float | None = None

    @property
    def year(self) -> int:
        """Год сезона — тот, в котором он заканчивается (зима 2020 = декабрь 2019 – февраль 2020)."""
        return precipitation.season_year(self.start)

    @property
    def name(self) -> str:
        return precipitation.SEASON_NAMES[self.start.month]

    @property
    def percent(self) -> float | None:
        return percent_of_norm(self.value, self.norm)


@dataclass(frozen=True)
class DryRun:
    """Подряд идущие дни без осадков (сумма меньше WET_DAY_MM)."""

    start: date
    end: date

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1


@dataclass(frozen=True)
class PrecipitationData:
    date_from: date
    date_to: date
    days_total: int
    days_with_data: int
    # Дни с суммой, в которые пропущен срок наблюдения rp5 (сумма может быть занижена)
    missed_term_days: int = 0
    period_sum: float | None = None
    # Сравнение с нормой — по дням полных месяцев периода (см. описание
    # модуля): их сумма, норма и число; None — таких дней нет или нет норм
    compared_sum: float | None = None
    period_norm: float | None = None
    compared_days: int = 0
    daily_max: DatedValue | None = None
    wet_days: int = 0  # дней с осадками в периоде
    # Среднее число дней с осадками за полный год периода и по скольким годам; None — полных лет нет
    wet_days_per_year: float | None = None
    wet_days_years: int = 0
    longest_dry_runs: tuple[DryRun, ...] = ()  # все самые длинные (при равной длине), по порядку
    # Рекорды — все с рекордным значением; пустой кортеж — полных лет/месяцев в периоде нет
    wettest_years: tuple[YearSum, ...] = ()
    driest_years: tuple[YearSum, ...] = ()
    wettest_months: tuple[MonthSum, ...] = ()
    driest_months: tuple[MonthSum, ...] = ()
    # Период короче 24 месяцев — карточки годов показывают декады
    short_period: bool = False
    wettest_decades: tuple[DecadeSum, ...] = ()
    driest_decades: tuple[DecadeSum, ...] = ()
    decade_cells: dict[date, DecadeSum] = field(default_factory=dict)  # декады, задетые периодом
    years: list[int] = field(default_factory=list)  # строки теплокарты
    month_cells: dict[tuple[int, int], MonthSum] = field(default_factory=dict)
    year_sums: list[YearSum] = field(default_factory=list)  # полные годы внутри периода
    trend: Trend | None = None  # по отклонениям годовых сумм от нормы, мм
    seasons: list[SeasonSum] = field(default_factory=list)  # сезоны, задетые периодом, по порядку
    month_norms: dict[int, float] = field(default_factory=dict)
    annual_norm: NormInfo | None = None
    norm_standard: bool = True

    @property
    def has_data(self) -> bool:
        return self.days_with_data > 0 or bool(self.month_cells)

    @property
    def period_percent(self) -> float | None:
        return percent_of_norm(self.compared_sum, self.period_norm) if self.compared_sum is not None else None


# --- выборка ---


def station_day_bounds(engine: Engine, wmo_index: int) -> tuple[date, date] | None:
    """Первый и последний день с суточной суммой осадков; None — сумм нет."""
    with engine.connect() as conn:
        first, last = conn.execute(
            select(func.min(computed_indicators.c.period_start), func.max(computed_indicators.c.period_start)).where(
                computed_indicators.c.wmo_index == wmo_index,
                computed_indicators.c.parameter == PRECIPITATION.parameter,
                computed_indicators.c.stat_type == PRECIPITATION.stat_type,
                computed_indicators.c.period_type == "day",
                computed_indicators.c.value.is_not(None),
            )
        ).one()
    if first is None or last is None:
        return None
    return first, last


def build_precipitation(engine: Engine, wmo_index: int, date_from: date, date_to: date) -> PrecipitationData:
    """Нормы и полнота — сохранённые; если данные станции менялись после
    последнего расчёта, сначала досчитываются (как build_dashboard)."""
    ensure_fresh(engine, [wmo_index])
    values = fetch_station_values(engine, wmo_index, PRECIPITATION)
    with engine.connect() as conn:
        missed_terms = precipitation.days_with_missed_term(conn, wmo_index)
    return compute_precipitation(
        values, date_from, date_to, load_station_norms(engine, wmo_index, PRECIPITATION.parameter), missed_terms
    )


# --- расчёт ---


def longest_dry_runs(days: dict[date, float], date_from: date, date_to: date) -> tuple[DryRun, ...]:
    """Самые длинные периоды без осадков внутри [date_from, date_to]; при
    равной длине — все. День без данных период обрывает."""
    runs: list[DryRun] = []
    start: date | None = None
    day = date_from
    while day <= date_to:
        value = days.get(day)
        if value is not None and value < WET_DAY_MM:
            start = start or day
        elif start is not None:
            runs.append(DryRun(start, day - timedelta(days=1)))
            start = None
        day += timedelta(days=1)
    if start is not None:
        runs.append(DryRun(start, date_to))
    if not runs:
        return ()
    longest = max(run.days for run in runs)
    return tuple(run for run in runs if run.days == longest)


def compute_precipitation(
    values: StationValues,
    date_from: date,
    date_to: date,
    norms: StationNorms | None = None,
    missed_terms: set[date] | frozenset[date] = frozenset(),
) -> PrecipitationData:
    """values — вся история осадков станции (нормы считаются по ней, а не по
    периоду); norms — сохранённые нормы и полнота, без них считаются здесь же
    тем же правилом (тесты и данные, которых нет в базе); missed_terms — дни
    с пропущенным сроком наблюдения."""
    if norms is None:
        norms = compute_norms(PRECIPITATION, values.days, values.months, values.years, values.seasons)
    present = days_by_month(values.days)
    month_norms, annual_norm = norms.months, norms.annual

    # --- карточки по дням периода ---
    period_days = [(day, value) for day, value in values.days.items() if date_from <= day <= date_to]
    period_sum = round(sum(value for _, value in period_days), precipitation.ROUND_DIGITS) if period_days else None
    compared = [
        (value, month_norms[day.month] / monthrange(day.year, day.month)[1])
        for day, value in period_days
        if (day.year, day.month) in norms.complete_months and day.month in month_norms
    ]
    compared_sum = round(sum(value for value, _ in compared), precipitation.ROUND_DIGITS) if compared else None
    period_norm = sum(norm for _, norm in compared) if compared else None
    wet_by_year: dict[int, int] = {}
    for day, value in period_days:
        if value >= WET_DAY_MM:
            wet_by_year[day.year] = wet_by_year.get(day.year, 0) + 1

    # --- месяцы: теплокарта (по пересечению) и рекорды (полные, целиком внутри) ---
    month_cells: dict[tuple[int, int], MonthSum] = {}
    inside_months: list[MonthSum] = []
    for key in sorted(values.months):
        start, end = _month_bounds(*key)
        if end < date_from or start > date_to:
            continue
        cell = MonthSum(
            key[0], key[1], values.months[key], len(present.get(key, ())), end.day,
            complete=key in norms.complete_months, norm=month_norms.get(key[1]),
        )
        month_cells[key] = cell
        if cell.complete and date_from <= start and end <= date_to:
            inside_months.append(cell)

    # --- декады: теплокарта и рекорды короткого периода ---
    decade_cells: dict[date, DecadeSum] = {}
    inside_decades: list[DecadeSum] = []
    for start, (value, count, length) in sorted(decade_values(PRECIPITATION, values.days).items()):
        end = decade_bounds(start)[1]
        if end < date_from or start > date_to:
            continue
        cell = DecadeSum(
            start, value, count, length, decade_is_complete(count, length), norms.decades.get((start.month, start.day))
        )
        decade_cells[start] = cell
        if cell.complete and date_from <= start and end <= date_to:
            inside_decades.append(cell)

    # --- годы: полные и целиком внутри периода ---
    year_sums = [
        YearSum(year, values.years[year], annual_norm.value if annual_norm else None)
        for year in sorted(norms.complete_years)
        if year in values.years and date_from <= date(year, 1, 1) and date(year, 12, 31) <= date_to
    ]
    wet_years = [wet_by_year.get(item.year, 0) for item in year_sums]

    seasons = [
        SeasonSum(start, value, start in norms.complete_seasons, norms.seasons.get(start.month))
        for start, value in sorted(values.seasons.items())
        if precipitation.season_bounds(start)[1] >= date_from and start <= date_to
    ]

    return PrecipitationData(
        date_from=date_from,
        date_to=date_to,
        days_total=(date_to - date_from).days + 1,
        days_with_data=len(period_days),
        missed_term_days=sum(1 for day, _ in period_days if day in missed_terms),
        period_sum=period_sum,
        compared_sum=compared_sum,
        period_norm=period_norm,
        compared_days=len(compared),
        daily_max=_extreme(period_days, highest=True),
        wet_days=sum(wet_by_year.values()),
        wet_days_per_year=fmean(wet_years) if wet_years else None,
        wet_days_years=len(wet_years),
        longest_dry_runs=longest_dry_runs(values.days, date_from, date_to),
        wettest_years=_ties(year_sums, lambda y: y.value, highest=True),
        driest_years=_ties(year_sums, lambda y: y.value, highest=False),
        wettest_months=_ties(inside_months, lambda m: m.value, highest=True),
        driest_months=_ties(inside_months, lambda m: m.value, highest=False),
        short_period=is_short_period(date_from, date_to),
        wettest_decades=_ties(inside_decades, lambda d: d.value, highest=True),
        driest_decades=_ties(inside_decades, lambda d: d.value, highest=False),
        decade_cells=decade_cells,
        years=sorted({year for year, _ in month_cells}),
        month_cells=month_cells,
        year_sums=year_sums,
        trend=linear_trend([(item.year, item.anomaly) for item in year_sums if item.anomaly is not None]),
        seasons=seasons,
        month_norms=dict(month_norms),
        annual_norm=annual_norm,
        norm_standard=norms.standard,
    )


# --- подписи для экрана ---


def format_mm(value: float, digits: int = 1) -> str:
    return f"{value:.{digits}f} мм"


def describe_percent(percent: float, norm_name: str = "нормы") -> str:
    """"112 % нормы" — доля нормы словами, без знака: у осадков принято
    говорить "80 % нормы", а не "на 20 % ниже"."""
    return f"{percent:.0f} % {norm_name}"
