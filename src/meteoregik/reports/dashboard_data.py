"""Данные экрана "Дашборд" (этап 1 — только по уже посчитанной температуре
T avg/min/max, см. plan.md, раздел "Экраны UI", п.4): карточки-сводки,
теплокарта "год × месяц" и аномалии среднегодовой температуры с трендом.
Qt-независимо — тот же принцип, что у reports/aggregate_query.py: экран
только рисует готовый DashboardData.

Полнота данных. В computed_indicators месячные/годовые средние считаются по
тем дням, что есть (calculations/temperature.py), — "год" из одного января
получится аномально холодным. Для нормы, аномалий и рекордов (самый
тёплый/холодный год и месяц) поэтому берутся только ПОЛНЫЕ месяцы и годы:
месяц полный по правилу ВМО "3/5" (пропущено не больше 5 дней всего и не
больше 3 подряд), год — если полны все 12 месяцев. Неполные месяцы на
теплокарте показываются, но помечаются (MonthCell.complete).

Норма — стандартная климатическая норма ВМО за 1991–2020 (WMO-No. 1203),
одна для всех лет и любого выбранного периода: норма "своего десятилетия"
для каждого года ломала бы ряд аномалий ступеньками и съедала бы тренд.
По ВМО норма пригодна, если в 30-летии есть хотя бы 24 года данных, — здесь
это 24 ПОЛНЫХ года. Если у станции их меньше, норма считается по всей
истории станции (подписывается на экране как "средняя по архиву"): тогда
сравнение всего архива с ней почти всегда даёт "на уровне" — данные
сравниваются сами с собой, но другого ориентира у такой станции нет.
Месячная норма — средняя по полным месяцам этого календарного месяца внутри
базы, годовая — средняя по полным годам базы. Отклонение средней за период от нормы
считается по дням: (суточная средняя − норма её календарного месяца),
усреднённая по дням периода, — так оно корректно для любого периода, в т.ч.
неполного года или одного сезона (сравнение летнего периода с годовой нормой
давало бы бессмысленные "+15 °C").

Значения в выборке — по пересечению с периодом для теплокарты (как в
"Просмотре агрегатных данных": месяц, частично попавший в период,
показывается целиком), но рекорды и аномалии — только по месяцам/годам,
целиком лежащим внутри периода: иначе "самым холодным годом" мог бы оказаться
год, из которого в период попал только январь.
"""

from __future__ import annotations

from calendar import monthrange
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from statistics import fmean

from sqlalchemy import func, select
from sqlalchemy.engine import Engine

from meteoregik.calculations.anomalies import (  # noqa: F401 — часть имён реэкспортируется
    MAX_MISSING_DAYS,
    MAX_MISSING_DECADE_DAYS,
    MAX_MISSING_RUN,
    MIN_NORM_BASE_YEARS,
    NORM_BASE_FIRST_YEAR,
    NORM_BASE_LAST_YEAR,
    PARAMETER,
    DayTemps,
    NormInfo,
    StationNorms,
    compute_station_norms,
    days_with_avg_by_month,
    decade_averages,
    decade_is_complete,
    ensure_fresh,
    fetch_station_temperatures,
    load_station_norms,
    month_is_complete,
)
from meteoregik.calculations.temperature import decade_bounds
from meteoregik.db.schema import computed_indicators

# Период короче стольких месяцев — вместо самого тёплого/холодного года
# показываются декады: полных лет в нём не больше одного, и "рекорд" из
# одного года ничего не говорит.
MIN_YEAR_RECORD_MONTHS = 24
# Меньше точек — линия тренда по ним ничего не говорит (одна-две аномалии
# дают "тренд" любого знака), поэтому тренд не строится.
MIN_TREND_YEARS = 5


@dataclass(frozen=True)
class DatedValue:
    """Рекорд и все дни, когда он наблюдался (по возрастанию дат)."""

    value: float
    days: tuple[date, ...]


@dataclass(frozen=True)
class MonthCell:
    """Ячейка теплокарты: средняя месяца из computed_indicators и полнота."""

    year: int
    month: int
    value: float | None
    days_with_data: int
    days_in_month: int
    complete: bool
    norm: float | None = None  # норма этого календарного месяца

    @property
    def anomaly(self) -> float | None:
        return self.value - self.norm if self.value is not None and self.norm is not None else None


@dataclass(frozen=True)
class DecadeCell:
    """Ячейка теплокарты короткого периода (строки — месяцы, столбцы —
    декады): средняя декады, полнота и норма той же декады того же месяца."""

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


@dataclass(frozen=True)
class MonthRecord:
    year: int
    month: int
    value: float
    anomaly: float | None  # от нормы этого календарного месяца
    norm: float | None = None  # сама норма этого календарного месяца


@dataclass(frozen=True)
class DecadeRecord:
    """Декада (1–10, 11–20, 21–конец месяца): средняя и отклонение от нормы
    той же декады того же месяца."""

    start: date  # первый день декады
    value: float
    anomaly: float | None
    norm: float | None = None

    @property
    def number(self) -> int:
        return (self.start.day - 1) // 10 + 1


@dataclass(frozen=True)
class YearAnomaly:
    year: int
    value: float
    anomaly: float


@dataclass(frozen=True)
class Trend:
    """Линейный тренд аномалий: anomaly ≈ intercept + slope_per_year * year."""

    slope_per_year: float
    intercept: float

    @property
    def per_decade(self) -> float:
        return self.slope_per_year * 10

    def at(self, year: float) -> float:
        return self.intercept + self.slope_per_year * year


@dataclass(frozen=True)
class DashboardData:
    date_from: date
    date_to: date
    days_total: int
    days_with_data: int
    period_mean: float | None
    period_anomaly: float | None
    abs_max: DatedValue | None
    abs_min: DatedValue | None
    # Все годы с рекордной средней (равные значения — все, по возрастанию лет);
    # пустой кортеж — нет полных лет в периоде
    warmest_years: tuple[YearAnomaly, ...]
    coldest_years: tuple[YearAnomaly, ...]
    # Месяцы — тоже все с рекордным значением: по абсолютной средней и по
    # отклонению от нормы своего календарного месяца
    warmest_months: tuple[MonthRecord, ...]
    coldest_months: tuple[MonthRecord, ...]
    anomalous_warm_months: tuple[MonthRecord, ...] = ()
    anomalous_cold_months: tuple[MonthRecord, ...] = ()
    # Период короче MIN_YEAR_RECORD_MONTHS — карточки годов показывают декады
    short_period: bool = False
    warmest_decades: tuple[DecadeRecord, ...] = ()
    coldest_decades: tuple[DecadeRecord, ...] = ()
    # Теплокарта короткого периода: декады, пересекающиеся с периодом
    decade_cells: dict[date, DecadeCell] = field(default_factory=dict)
    years: list[int] = field(default_factory=list)  # строки теплокарты
    month_cells: dict[tuple[int, int], MonthCell] = field(default_factory=dict)
    year_anomalies: list[YearAnomaly] = field(default_factory=list)
    trend: Trend | None = None
    annual_norm: NormInfo | None = None
    # Норма, с которой сравнивалась средняя за период (среднее норм месяцев по дням периода)
    period_norm: float | None = None
    # Отдельно от annual_norm: месячные нормы бывают и без единого полного года
    norm_standard: bool = True

    @property
    def has_data(self) -> bool:
        return self.days_with_data > 0 or bool(self.month_cells)


# --- выборка ---


def station_day_bounds(engine: Engine, wmo_index: int) -> tuple[date, date] | None:
    """Первый и последний день с суточными значениями — "весь архив" для
    пустого периода. Не aggregate_query.station_period_bounds: та берёт
    границы и годовых строк (01.01–31.12), и архив с 01.07.1959 по 28.02.2025
    превращался бы в 1959–2025 целиком, а ~490 дней за краями данных
    считались бы "пропусками" в карточке полноты."""
    with engine.connect() as conn:
        first, last = conn.execute(
            select(func.min(computed_indicators.c.period_start), func.max(computed_indicators.c.period_end)).where(
                computed_indicators.c.wmo_index == wmo_index,
                computed_indicators.c.parameter == PARAMETER,
                computed_indicators.c.period_type == "day",
                computed_indicators.c.value.is_not(None),
            )
        ).one()
    if first is None or last is None:
        return None
    return first, last


def build_dashboard(engine: Engine, wmo_index: int, date_from: date, date_to: date) -> DashboardData:
    """Нормы и полнота берутся сохранённые (calculations/anomalies.py); если
    данные станции менялись после последнего расчёта — сначала досчитываются."""
    ensure_fresh(engine, [wmo_index])
    days, months, years = fetch_station_temperatures(engine, wmo_index)
    return compute_dashboard(days, months, years, date_from, date_to, load_station_norms(engine, wmo_index))


# --- расчёт ---


def linear_trend(points: list[tuple[int, float]]) -> Trend | None:
    """Метод наименьших квадратов; None, если точек меньше MIN_TREND_YEARS."""
    if len(points) < MIN_TREND_YEARS:
        return None
    xs = [x for x, _ in points]
    ys = [y for _, y in points]
    mean_x, mean_y = fmean(xs), fmean(ys)
    sxx = sum((x - mean_x) ** 2 for x in xs)
    if sxx == 0:
        return None
    slope = sum((x - mean_x) * (y - mean_y) for x, y in points) / sxx
    return Trend(slope_per_year=slope, intercept=mean_y - slope * mean_x)


def _month_bounds(year: int, month: int) -> tuple[date, date]:
    return date(year, month, 1), date(year, month, monthrange(year, month)[1])


def _extreme(days: list[tuple[date, float]], *, highest: bool) -> DatedValue | None:
    """Рекорд и все дни с ним: одинаковый максимум в разные дни — обычное
    дело (значения с точностью 0.1 °C), и показать только один день значило
    бы молча выбрать "победителя"."""
    if not days:
        return None
    best = max(v for _, v in days) if highest else min(v for _, v in days)
    return DatedValue(best, tuple(sorted(d for d, v in days if v == best)))


def _ties[T](items: list[T], key: Callable[[T], float], *, highest: bool) -> tuple[T, ...]:
    """Все записи с рекордным значением, в исходном (хронологическом)
    порядке, — как и у абсолютных экстремумов, при равенстве не выбираем
    "победителя" молча. Сравнение — с точностью хранения (2 знака): аномалии
    месяцев — разности float, и "равные" для пользователя значения могли бы
    отличаться в последних битах."""
    if not items:
        return ()
    rounded = [round(key(item), 2) for item in items]
    best = max(rounded) if highest else min(rounded)
    return tuple(item for item, value in zip(items, rounded) if value == best)


def _add_months(day: date, months: int) -> date:
    """Тот же день через months месяцев (31.01 + 1 → 28/29.02)."""
    total = day.year * 12 + day.month - 1 + months
    year, month = divmod(total, 12)
    return date(year, month + 1, min(day.day, monthrange(year, month + 1)[1]))


def is_short_period(date_from: date, date_to: date) -> bool:
    """Период короче MIN_YEAR_RECORD_MONTHS месяцев: 01.01.2019–31.12.2020 —
    ровно 24 месяца (не короткий), на день меньше — короткий."""
    return date_to < _add_months(date_from, MIN_YEAR_RECORD_MONTHS) - timedelta(days=1)


def compute_dashboard(
    days: dict[date, DayTemps],
    months: dict[tuple[int, int], float],
    years: dict[int, float],
    date_from: date,
    date_to: date,
    norms: StationNorms | None = None,
) -> DashboardData:
    """norms — сохранённые нормы и полнота станции; без них считаются здесь
    же тем же правилом (compute_station_norms) — так работают тесты и расчёт
    по данным, которых нет в базе."""
    if norms is None:
        norms = compute_station_norms(days, months, years)
    present = days_with_avg_by_month(days)
    complete_months, complete_years = norms.complete_months, norms.complete_years
    norm_standard, month_norms, annual_norm = norms.standard, norms.months, norms.annual

    # --- карточки по дням периода ---
    period_days = [(d, t) for d, t in days.items() if date_from <= d <= date_to]
    avgs = [(d, t.avg) for d, t in period_days if t.avg is not None]
    period_mean = fmean(v for _, v in avgs) if avgs else None
    deviations = [v - month_norms[d.month] for d, v in avgs if d.month in month_norms]
    period_anomaly = fmean(deviations) if deviations else None
    # Норма "для этого периода" — по тем же дням, что и отклонение: для лета
    # это летняя норма, для целого года — почти годовая.
    period_norms = [month_norms[d.month] for d, _ in avgs if d.month in month_norms]
    period_norm = fmean(period_norms) if period_norms else None
    abs_max = _extreme([(d, t.max) for d, t in period_days if t.max is not None], highest=True)
    abs_min = _extreme([(d, t.min) for d, t in period_days if t.min is not None], highest=False)

    # --- месяцы: теплокарта (по пересечению) и рекорды (целиком внутри) ---
    month_cells: dict[tuple[int, int], MonthCell] = {}
    inside_months: list[MonthRecord] = []
    for year in range(date_from.year, date_to.year + 1):
        for month in range(1, 13):
            start, end = _month_bounds(year, month)
            if end < date_from or start > date_to:
                continue
            key = (year, month)
            value = months.get(key)
            day_count = len(present.get(key, set()))
            if value is None and day_count == 0:
                continue
            month_cells[key] = MonthCell(
                year, month, value, day_count, end.day, complete=key in complete_months, norm=month_norms.get(month)
            )
            if value is not None and key in complete_months and date_from <= start and end <= date_to:
                norm = month_norms.get(month)
                inside_months.append(
                    MonthRecord(year, month, value, value - norm if norm is not None else None, norm)
                )

    # --- декады: рекорды для короткого периода (полные декады целиком внутри) ---
    decade_avgs = decade_averages(days)
    decade_norms = norms.decades
    inside_decades: list[DecadeRecord] = []
    decade_cells: dict[date, DecadeCell] = {}
    for start in sorted(decade_avgs):
        value, count, length = decade_avgs[start]
        end = decade_bounds(start)[1]
        if end < date_from or start > date_to:
            continue
        complete = decade_is_complete(count, length)
        norm = decade_norms.get((start.month, start.day))
        # теплокарта — по пересечению с периодом (как месяцы), рекорды — целиком внутри
        decade_cells[start] = DecadeCell(start, value, count, length, complete, norm)
        if complete and date_from <= start and end <= date_to:
            inside_decades.append(DecadeRecord(start, value, value - norm if norm is not None else None, norm))

    # --- годы: аномалии и тренд (полные годы целиком внутри периода) ---
    year_anomalies: list[YearAnomaly] = []
    if annual_norm is not None:
        for year in sorted(complete_years):  # все полные годы, не только годы базы нормы
            if date_from <= date(year, 1, 1) and date(year, 12, 31) <= date_to:
                year_anomalies.append(YearAnomaly(year, years[year], years[year] - annual_norm.value))

    return DashboardData(
        date_from=date_from,
        date_to=date_to,
        days_total=(date_to - date_from).days + 1,
        days_with_data=len(avgs),
        period_mean=period_mean,
        period_anomaly=period_anomaly,
        period_norm=period_norm,
        abs_max=abs_max,
        abs_min=abs_min,
        warmest_years=_ties(year_anomalies, lambda y: y.value, highest=True),
        coldest_years=_ties(year_anomalies, lambda y: y.value, highest=False),
        warmest_months=_ties(inside_months, lambda m: m.value, highest=True),
        coldest_months=_ties(inside_months, lambda m: m.value, highest=False),
        # "аномально тёплый" — только среди месяцев выше нормы, иначе в
        # сплошь холодном периоде им оказался бы месяц "на 0.3 °C ниже нормы"
        anomalous_warm_months=_ties(
            [m for m in inside_months if m.anomaly is not None and round(m.anomaly, 1) > 0],
            lambda m: m.anomaly, highest=True,
        ),
        short_period=is_short_period(date_from, date_to),
        warmest_decades=_ties(inside_decades, lambda d: d.value, highest=True),
        coldest_decades=_ties(inside_decades, lambda d: d.value, highest=False),
        anomalous_cold_months=_ties(
            [m for m in inside_months if m.anomaly is not None and round(m.anomaly, 1) < 0],
            lambda m: m.anomaly, highest=False,
        ),
        years=sorted({year for year, _ in month_cells}),
        month_cells=month_cells,
        decade_cells=decade_cells,
        year_anomalies=year_anomalies,
        trend=linear_trend([(y.year, y.anomaly) for y in year_anomalies]),
        annual_norm=annual_norm,
        norm_standard=norm_standard,
    )


# --- подписи для экрана ---

MONTHS_NOMINATIVE = [
    "январь", "февраль", "март", "апрель", "май", "июнь",
    "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь",
]
MONTHS_GENITIVE = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]
MONTHS_SHORT = ["янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]

_MINUS = "−"  # типографский минус: дефис рядом с "+" выглядит короче и сбивает выравнивание


def format_signed(value: float, digits: int = 1) -> str:
    """Число со знаком: "+5.8", "−3.2", "0.0" (ноль без знака — у нуля
    нет направления, а "+0.0"/"−0.0" читались бы как отклонение)."""
    rounded = round(value, digits)
    if rounded == 0:
        return f"{0:.{digits}f}"
    text = f"{abs(rounded):.{digits}f}"
    return f"+{text}" if rounded > 0 else f"{_MINUS}{text}"


def format_temperature(value: float) -> str:
    return f"{format_signed(value)} °C"


def describe_anomaly(anomaly: float, norm_name: str = "нормы") -> str:
    """"на 0.6 °C выше нормы" / "ниже" / "на уровне нормы" — словами, а не
    только знаком и цветом."""
    rounded = round(anomaly, 1)
    if rounded == 0:
        return f"на уровне {norm_name}"
    side = "выше" if rounded > 0 else "ниже"
    return f"на {abs(rounded):.1f} °C {side} {norm_name}"
