"""Графики блока "Осадки по периодам переходов температуры" вкладки "Осадки"
(корректировка 2026-10-27; данные — reports/transition_precipitation.py):
1. тёплый и холодный периоды по оси времени (фон — сам период, точка —
   сумма осадков за него);
2. вложенные периоды тёплого полугодия — прямоугольники от весенней до
   осенней даты высотой в сумму: сзади тёплый, впереди летний;
3. сумма одного выбранного периода по годам со средней многолетней.

Два прочтения метода (Гидрометцентр и "Климат России") различаются не
только цветом, но и формой маркера (круг / квадрат) и линией (сплошная /
пунктир) — разница видна и без цветового зрения и при печати. Цвета
прочтений — 1-й и 2-й категориальные слоты dataviz без синего: синий
читался бы на голубом фоне холодного периода; проверены валидатором
(scripts/validate_palette.js, светлая тема, фон окна): все проверки пройдены.

Заливки вложенных периодов — пастельные (фон, а не метки): тёплый —
розовый (тот же, что фон тёплого периода на первом графике), вегетация —
светло-зелёный и зелёный, летний — жёлтый. Соседние по вложенности пары
различаются и при протанопии/дейтеранопии (валидатор: CVD ΔE ≥ 10.5,
обычное зрение ΔE ≥ 16); светлоту и насыщенность "меток" пастель
намеренно не проходит — это области, у каждой ещё и тёмный контур, легенда
и подсказка по наведению.

Пустой маркер и штриховка — в периоде есть дни без данных об осадках: сумма
посчитана по тому, что есть, и может быть занижена (как "неполный месяц" на
остальных графиках вкладки).

Первые два графика на длинном архиве показывают окно в VISIBLE_YEARS лет с
полосой прокрутки под графиком (корректировка 2026-10-28): весь архив в
ширину окна сжимал период до тонкой полоски. Прокручивается только ось
времени — шкала сумм общая для всего архива, чтобы высоты в разных годах
сравнивались. Третьему графику (по годам) прокрутка не нужна.
"""

from __future__ import annotations

from datetime import date, timedelta
from math import nan

from matplotlib import dates as mdates
from matplotlib.backend_bases import MouseEvent
from matplotlib.lines import Line2D
from matplotlib.patches import Patch, Rectangle
from matplotlib.ticker import FuncFormatter
from matplotlib.transforms import blended_transform_factory
from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QScrollBar, QWidget

from meteoregik.calculations.anomalies import NORM_BASE_FIRST_YEAR, NORM_BASE_LAST_YEAR
from meteoregik.calculations.transitions import METHOD_HMC, METHOD_KR, METHOD_NAMES, METHODS
from meteoregik.reports.dashboard_data import MONTHS_SHORT
from meteoregik.reports.precipitation_data import format_mm
from meteoregik.reports.transition_precipitation import (
    COLD,
    KIND_NAMES,
    NESTED,
    WARM,
    MeanSum,
    PeriodSum,
    TransitionPrecipitation,
    format_period,
    kind_title,
    period_label,
)
from meteoregik.ui import theme
from meteoregik.ui.dashboard_charts import _MAX_YEAR_TICKS, _YEAR_TICK_STEPS, _ChartCanvas, _rotate_x_labels
from meteoregik.ui.dashboard_precip_charts import _HATCH, _style_bars, _year_ticks

METHOD_COLORS = {METHOD_HMC: "#6250d6", METHOD_KR: "#eb6834"}  # фиолетовый / оранжевый
METHOD_MARKERS = {METHOD_HMC: "o", METHOD_KR: "s"}
METHOD_LINES = {METHOD_HMC: "-", METHOD_KR: "--"}

COLD_BAND = "#D3E4F5"
PERIOD_FILLS = {WARM: "#F7D0DE", "vegetation": "#A9D18D", "active": "#5D9F55", "summer": "#F6DE78"}
PERIOD_EDGES = {WARM: "#C98AA3", "vegetation": "#7FAE62", "active": "#3F7A39", "summer": "#C9A93E"}
BANDS = {WARM: PERIOD_FILLS[WARM], COLD: COLD_BAND}

# Короче — по оси месяцы, длиннее — годы
MONTH_AXIS_YEARS = 3
# Сколько лет видно на графиках с прокруткой; архив длиннее — прокручивается
VISIBLE_YEARS = 5
_VISIBLE_DAYS = round(VISIBLE_YEARS * 365.25)
# Шаг прокрутки: стрелки полосы — месяц, щелчок по полосе — всё окно,
# колесо мыши с Shift — квартал за щелчок колеса
_SCROLL_STEP_DAYS = 31
_WHEEL_STEP_DAYS = 92
# Полоса прокрутки в цветах приложения: ползунок TEXT_SECONDARY на HEADER_BG —
# 4.4:1 (элемент управления, нужно ≥ 3:1); с клавиатуры — фокус по Tab с
# кольцом FOCUS_RING, сдвиг стрелками и PageUp/PageDown
TIME_SCROLL_QSS = f"""
QScrollBar#timeScroll:horizontal {{
    background: {theme.HEADER_BG};
    height: 16px;
    border: {theme.FOCUS_RING_WIDTH}px solid transparent;
    border-radius: 8px;
    margin: 0;
}}
QScrollBar#timeScroll:horizontal:focus {{ border-color: {theme.FOCUS_RING}; }}
QScrollBar#timeScroll::handle:horizontal {{
    background: {theme.TEXT_SECONDARY};
    border-radius: 4px;
    min-width: 48px;
    margin: 2px 0;
}}
QScrollBar#timeScroll::handle:horizontal:hover {{ background: {theme.TEXT_PRIMARY}; }}
QScrollBar#timeScroll::add-line:horizontal, QScrollBar#timeScroll::sub-line:horizontal {{
    width: 0; border: none; background: none;
}}
QScrollBar#timeScroll::add-page:horizontal, QScrollBar#timeScroll::sub-page:horizontal {{ background: none; }}
"""

NO_TRANSITIONS = "У станции нет дат перехода температуры:\nдля них нужны средние суточные температуры."
NO_PERIODS = "В выбранном периоде нет ни одного периода,\nу которого есть обе даты перехода."

_MARKER_SIZE = 7
# Квадрат "Климата России" крупнее и рисуется под кругом Гидрометцентра: при
# равных суммах видны оба маркера, а не только тот, что нарисован последним
_MARKER_SIZES = {METHOD_HMC: 6, METHOD_KR: 9}
# Порядок рисования прочтений — снизу вверх
_DRAW_ORDER = (METHOD_KR, METHOD_HMC)
# Меньше лет — по краям графика запас, иначе столбики одного года во всю ширину
_MIN_BAR_SLOTS = 4


def _num(day: date) -> float:
    return float(mdates.date2num(day))


def _day(x: float) -> date:
    return mdates.num2date(x).date()


def _month_label(x: float, position: int | None) -> str:
    day = _day(x)
    name = MONTHS_SHORT[day.month - 1]
    return f"{name} {day.year}" if day.month == 1 or position == 0 else name


def _date_axis(ax, first: date, last: date) -> None:
    """Ось времени: на коротком отрезке — месяцы, на длинном — годы с
    "круглым" шагом и месяцами-засечками (пока их не слишком много)."""
    span_years = (last - first).days / 365.25
    if span_years <= MONTH_AXIS_YEARS:
        ax.xaxis.set_major_locator(mdates.MonthLocator(bymonth=(1, 4, 7, 10)))
        ax.xaxis.set_major_formatter(FuncFormatter(_month_label))
        ax.xaxis.set_minor_locator(mdates.MonthLocator())
    else:
        years = last.year - first.year + 1
        step = next((s for s in _YEAR_TICK_STEPS if years / s <= _MAX_YEAR_TICKS), _YEAR_TICK_STEPS[-1])
        ax.xaxis.set_major_locator(mdates.YearLocator(base=step))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
        if span_years <= 15:
            ax.xaxis.set_minor_locator(mdates.MonthLocator())
    ax.tick_params(axis="x", which="minor", length=2, color=theme.BORDER)
    ax.tick_params(axis="x", labelsize=8)
    # Без поворота подписей: засечки ставит локатор при каждой отрисовке, и
    # поворот, заданный один раз (_rotate_x_labels), с новыми не сохранялся
    # бы; подписей и так не больше _MAX_YEAR_TICKS
    ax.set_xlim(_num(first), _num(last + timedelta(days=1)))


def _method_handle(method: str, line: bool = False) -> Line2D:
    color = METHOD_COLORS[method]
    return Line2D(
        [], [], color=color, linestyle=METHOD_LINES[method] if line else "none", linewidth=2,
        marker=METHOD_MARKERS[method], markersize=_MARKER_SIZES[method], markerfacecolor=color, markeredgecolor=color,
        label=METHOD_NAMES[method],
    )


def _hollow_handle() -> Line2D:
    return Line2D([], [], linestyle="none", marker="o", markersize=_MARKER_SIZE, markerfacecolor=theme.BG,
                  markeredgecolor=theme.TEXT_SECONDARY, markeredgewidth=1.5, label="Есть дни без данных")


def _bottom_legend(figure, handles: list, rows_reserved: float = 0.12, top: bool = False) -> None:
    """Легенда вне области графика: над точками и полосами ей места нет.
    top — над графиком: у графиков с прокруткой под осью времени стоит
    полоса прокрутки, и легенда между ними отрывала бы полосу от оси."""
    figure.legend(handles=handles, loc="upper center" if top else "lower center", ncols=min(len(handles), 4),
                  fontsize=8, frameon=False, labelcolor=theme.TEXT_PRIMARY)
    figure.tight_layout(rect=(0, 0, 1, 1 - rows_reserved) if top else (0, rows_reserved, 1, 1))


def _plot_markers(ax, periods: list[PeriodSum], method: str, x_of) -> None:
    """Точки сумм: заполненные — полные периоды, пустые — с пропусками."""
    color = METHOD_COLORS[method]
    for complete in (True, False):
        part = [p for p in periods if p.value is not None and p.complete is complete]
        if part:
            ax.plot([x_of(p) for p in part], [p.value for p in part], linestyle="none",
                    marker=METHOD_MARKERS[method], markersize=_MARKER_SIZES[method], markeredgewidth=1.5,
                    markerfacecolor=color if complete else theme.BG, markeredgecolor=color,
                    zorder=4 + _DRAW_ORDER.index(method))


def period_text(period: PeriodSum) -> str:
    """"412.3 мм, 19.03.2015–22.10.2015 (218 дн.)" и пропуски, если есть."""
    if period.value is None:
        return f"нет данных об осадках, {format_period(period)} ({period.days} дн.)"
    text = f"{format_mm(period.value)}, {format_period(period)} ({period.days} дн.)"
    if not period.complete:
        text += f"\n    без данных об осадках {period.missing_days} дн. — сумма может быть занижена"
    return text


def _ordered(methods: tuple[str, ...]) -> list[str]:
    return [method for method in _DRAW_ORDER if method in methods]


def mean_text(method: str, mean: MeanSum) -> str:
    base = (
        f"{NORM_BASE_FIRST_YEAR}–{NORM_BASE_LAST_YEAR}, {mean.years} лет" if mean.standard
        else f"по архиву, {mean.years} лет ({mean.first_year}–{mean.last_year})"
    )
    return f"Средняя многолетняя ({METHOD_NAMES[method]}): {format_mm(mean.value)} — {base}"


class _PeriodChart(_ChartCanvas):
    """Общее: данные и выбранные прочтения (оба или одно)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._data: TransitionPrecipitation | None = None
        self._methods: tuple[str, ...] = METHODS
        self._ax = None

    @property
    def methods(self) -> tuple[str, ...]:
        return self._methods

    def plot(self, data: TransitionPrecipitation | None, methods: tuple[str, ...] | None = None) -> None:
        self._data = data
        if methods is not None:
            self._methods = methods
        self._redraw()

    def set_methods(self, methods: tuple[str, ...]) -> None:
        self._methods = methods
        if self._data is not None:
            self._redraw()

    def _redraw(self) -> None:
        self.figure.clear()
        ax = self._ax = self.figure.add_subplot(111)
        data = self._data
        if data is None or not data.has_transitions:
            self._show_message(ax, NO_TRANSITIONS)
        elif not self._draw(ax, data):
            self._show_message(ax, NO_PERIODS)
        self.canvas.draw_idle()

    def _draw(self, ax, data: TransitionPrecipitation) -> bool:
        """Нарисовать; False — рисовать нечего."""
        raise NotImplementedError


class _ScrollingTimeChart(_PeriodChart):
    """График по оси времени с окном в VISIBLE_YEARS лет: под графиком —
    полоса прокрутки и подпись видимых лет. Архив не длиннее окна — полосы
    нет. По умолчанию видны последние годы: обычно смотрят свежие данные.
    Колесо мыши по-прежнему прокручивает вкладку, с Shift (или
    горизонтальным колесом) — график."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._first: date | None = None
        self._last: date | None = None
        self._window = 0
        self.scrollbar = QScrollBar(Qt.Orientation.Horizontal)
        self.scrollbar.setObjectName("timeScroll")
        self.scrollbar.setStyleSheet(TIME_SCROLL_QSS)
        self.scrollbar.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.scrollbar.setAccessibleName("Прокрутка графика по годам")
        self.scrollbar.setSingleStep(_SCROLL_STEP_DAYS)
        self.scrollbar.setCursor(Qt.CursorShape.PointingHandCursor)
        self.scrollbar.valueChanged.connect(self._on_scroll)
        self.window_label = QLabel()
        self.window_label.setObjectName("chartNote")
        self.scroll_row = QWidget()
        row = QHBoxLayout(self.scroll_row)
        row.setContentsMargins(60, 0, 8, 0)  # от оси Y — полоса под самой областью графика
        row.setSpacing(10)
        row.addWidget(self.scrollbar, stretch=1)
        row.addWidget(self.window_label)
        self.scroll_row.setVisible(False)
        self.layout().addWidget(self.scroll_row)

    def _redraw(self) -> None:
        self.scroll_row.setVisible(False)  # покажет _set_span, если есть что прокручивать
        super()._redraw()

    def _set_span(self, ax, first: date, last: date) -> None:
        """Ось времени по всему архиву [first, last], видно — окно."""
        total = (last - first).days + 1
        window = min(total, _VISIBLE_DAYS)
        keep = self._first == first and self.scrollbar.maximum() == total - window
        _date_axis(ax, first, first + timedelta(days=window - 1))
        self._first, self._window = first, window
        self.scrollbar.blockSignals(True)
        position = self.scrollbar.value()
        self.scrollbar.setRange(0, total - window)
        self.scrollbar.setPageStep(window)
        # Переключение прочтений не сбрасывает прокрутку; новые данные — к последним годам
        self.scrollbar.setValue(position if keep else total - window)
        self.scrollbar.blockSignals(False)
        self.scroll_row.setVisible(total > window)
        self._last = last
        self._apply_window(ax)

    def _apply_window(self, ax) -> None:
        assert self._first is not None
        start = self._first + timedelta(days=self.scrollbar.value())
        end = start + timedelta(days=self._window - 1)
        ax.set_xlim(_num(start), _num(end + timedelta(days=1)))
        self.window_label.setText(f"Показаны {start.year}–{end.year} из {self._first.year}–{self._last.year}")

    def _on_scroll(self, _value: int) -> None:
        if self._ax is not None and self._first is not None:
            self._apply_window(self._ax)
            self.canvas.draw_idle()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 — имя метода Qt
        if (
            watched is self.canvas and event.type() == QEvent.Type.Wheel and not self.scroll_row.isHidden()
            and (event.modifiers() & Qt.KeyboardModifier.ShiftModifier or event.angleDelta().x())
        ):
            delta = event.angleDelta().x() or event.angleDelta().y()
            self.scrollbar.setValue(self.scrollbar.value() - round(delta / 120 * _WHEEL_STEP_DAYS))
            return True
        return super().eventFilter(watched, event)


class WarmColdChart(_ScrollingTimeChart):
    """Тёплый и холодный периоды подряд по оси времени. Фон — периоды
    (голубой — холодный, розовый — тёплый), точка посередине периода —
    сумма осадков за него. При двух прочтениях фон — по датам
    Гидрометцентра: два фона друг на друге не читались бы."""

    def _periods(self, method: str) -> list[PeriodSum]:
        assert self._data is not None
        return sorted(self._data.series(WARM, method) + self._data.series(COLD, method), key=lambda p: p.start)

    def _draw(self, ax, data: TransitionPrecipitation) -> bool:
        shown = {method: self._periods(method) for method in self._methods}
        everything = [p for periods in shown.values() for p in periods]
        if not everything:
            return False
        background = shown[self._methods[0]]
        for period in background:
            ax.axvspan(_num(period.start), _num(period.end + timedelta(days=1)), color=BANDS[period.kind],
                       linewidth=0, zorder=0)
        for method in _ordered(self._methods):
            _plot_markers(ax, shown[method], method, lambda p: _num(p.middle))
        _style_bars(ax, "Сумма осадков, мм")
        self._set_span(ax, min(p.start for p in everything), max(p.end for p in everything))
        ax.set_ylim(0, max([p.value for p in everything if p.value is not None] or [1.0]) * 1.22)
        # Подписи периодов — у верхнего края полос: "2015", "2014/2015". В окне
        # не больше VISIBLE_YEARS лет, поэтому они помещаются горизонтально;
        # clip_on — подписи периодов за краем окна не вылезают на поля
        to_axes = blended_transform_factory(ax.transData, ax.transAxes)
        for period in background:
            ax.text(_num(period.middle), 0.98, period.label, transform=to_axes, ha="center", va="top",
                    fontsize=7.5, color=theme.TEXT_SECONDARY, clip_on=True)
        handles: list = [Patch(color=COLD_BAND, label="Холодный период (ниже 0 °C)"),
                         Patch(color=BANDS[WARM], label="Тёплый период (выше 0 °C)")]
        handles += [_method_handle(method) for method in self._methods]
        if any(not p.complete for p in everything):
            handles.append(_hollow_handle())
        _bottom_legend(self.figure, handles, 0.14, top=True)
        return True

    def tooltip_at(self, event: MouseEvent) -> str | None:
        if self._data is None or event.inaxes is not self._ax or event.xdata is None:
            return None
        day = _day(event.xdata)
        lines = []
        for method in self._methods:
            period = next((p for p in self._periods(method) if p.start <= day <= p.end), None)
            if period is not None:
                lines.append(f"{METHOD_NAMES[method]}: {KIND_NAMES[period.kind]} {period.label}\n    {period_text(period)}")
        return "\n".join(lines) or None


class NestedPeriodsChart(_ScrollingTimeChart):
    """Тёплое полугодие каждого года — прямоугольники от весенней до осенней
    даты высотой в сумму осадков: сзади тёплый (самый широкий и высокий),
    перед ним вегетационный, активной вегетации, впереди летний. При двух
    прочтениях залиты периоды Гидрометцентра, "Климат России" — пунктирным
    контуром поверх."""

    def _fill_method(self) -> str:
        return self._methods[0] if len(self._methods) == 1 else METHOD_HMC

    def _draw(self, ax, data: TransitionPrecipitation) -> bool:
        fill_method = self._fill_method()
        outline_method = METHOD_KR if len(self._methods) == 2 else None
        drawn: list[PeriodSum] = []
        for depth, kind in enumerate(NESTED):
            for period in data.series(kind, fill_method):
                if period.value is None:
                    continue
                ax.add_patch(Rectangle(
                    (_num(period.start), 0), period.days, period.value, facecolor=PERIOD_FILLS[kind],
                    edgecolor=PERIOD_EDGES[kind], linewidth=0.8, hatch=None if period.complete else _HATCH,
                    zorder=1 + depth,
                ))
                drawn.append(period)
        if outline_method is not None:
            for kind in NESTED:
                for period in data.series(kind, outline_method):
                    if period.value is None:
                        continue
                    ax.add_patch(Rectangle(
                        (_num(period.start), 0), period.days, period.value, fill=False,
                        edgecolor=PERIOD_EDGES[kind], linewidth=1.4, linestyle=(0, (4, 2)), zorder=10,
                    ))
                    drawn.append(period)
        if not drawn:
            return False
        _style_bars(ax, "Сумма осадков, мм")
        self._set_span(ax, min(p.start for p in drawn), max(p.end for p in drawn))
        ax.set_ylim(0, max(p.value for p in drawn if p.value is not None) * 1.12)
        handles: list = [
            Patch(facecolor=PERIOD_FILLS[kind], edgecolor=PERIOD_EDGES[kind], label=kind_title(kind))
            for kind in NESTED
        ]
        if outline_method is not None:
            handles.append(Patch(fill=False, edgecolor=theme.TEXT_SECONDARY, linestyle=(0, (4, 2)), linewidth=1.4,
                                 label=f"Контур — {METHOD_NAMES[METHOD_KR]}"))
        if any(not p.complete for p in drawn):
            handles.append(Patch(facecolor=theme.BG, edgecolor=theme.TEXT_SECONDARY, hatch=_HATCH,
                                 label="Есть дни без данных"))
        _bottom_legend(self.figure, handles, 0.17, top=True)
        return True

    def tooltip_at(self, event: MouseEvent) -> str | None:
        data = self._data
        if data is None or event.inaxes is not self._ax or event.xdata is None:
            return None
        day = _day(event.xdata)
        year = next(
            (p.year for kind in NESTED for method in self._methods for p in data.series(kind, method)
             if p.start <= day <= p.end),
            None,
        )
        if year is None:
            return None
        lines = [str(year)]
        for method in self._methods:
            lines.append(f"{METHOD_NAMES[method]}:")
            for kind in NESTED:
                period = next((p for p in data.series(kind, method) if p.year == year), None)
                if period is not None:
                    lines.append(f"  {KIND_NAMES[kind]} — {period_text(period)}")
                elif (gap := data.gap(kind, method, year)) is not None:
                    lines.append(f"  {KIND_NAMES[kind]} — нет: {gap.reason}")
        return "\n".join(lines)


class PeriodSeriesChart(_PeriodChart):
    """Сумма одного периода (set_kind) по годам: линии прочтений и
    горизонтали их средних многолетних сумм. Для периода дашборда короче
    24 месяцев — парные столбики: линия из одной-двух точек ничего не
    показывает."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._kind = WARM
        self._years: list[int] = []
        self._bars = False

    @property
    def kind(self) -> str:
        return self._kind

    def set_kind(self, kind: str) -> None:
        self._kind = kind
        if self._data is not None:
            self._redraw()

    def _value(self, method: str, year: int) -> PeriodSum | None:
        assert self._data is not None
        return next((p for p in self._data.series(self._kind, method) if p.year == year), None)

    def _draw(self, ax, data: TransitionPrecipitation) -> bool:
        self._years = data.years(self._kind)
        self._bars = data.short_period
        if not any(p.value is not None for m in self._methods for p in data.series(self._kind, m)):
            return False
        years = self._years
        positions = list(range(len(years))) if self._bars else years
        width = 0.8 / len(self._methods)
        any_incomplete = False
        for method in _ordered(self._methods):
            number = self._methods.index(method)
            color = METHOD_COLORS[method]
            periods = [self._value(method, year) for year in years]
            if self._bars:
                offset = (number - (len(self._methods) - 1) / 2) * width
                for pos, period in zip(positions, periods):
                    if period is None or period.value is None:
                        continue
                    ax.bar(pos + offset, period.value, width=width * 0.92, color=color,
                           hatch=None if period.complete else _HATCH, edgecolor=theme.BG, zorder=2)
                    any_incomplete |= not period.complete
            else:
                values = [p.value if p is not None and p.value is not None else nan for p in periods]
                ax.plot(positions, values, color=color, linestyle=METHOD_LINES[method], linewidth=2,
                        zorder=2 + _DRAW_ORDER.index(method))
                present = [p for p in periods if p is not None]
                _plot_markers(ax, present, method, lambda p: p.year)
                any_incomplete |= any(not p.complete for p in present if p.value is not None)
        _style_bars(ax, "Сумма осадков, мм")
        means = [data.means[(self._kind, m)].value for m in self._methods if (self._kind, m) in data.means]
        ax.set_ylim(bottom=0, top=max([ax.get_ylim()[1], *means]) * 1.12)
        mean_handles = self._mean_lines(ax, data)
        ticks = years if len(years) <= _MAX_YEAR_TICKS else _year_ticks(years)
        tick_positions = [positions[years.index(year)] for year in ticks if year in years]
        ax.set_xticks(tick_positions, [period_label(self._kind, year, short=True) for year in ticks if year in years])
        ax.tick_params(axis="x", labelsize=8)
        _rotate_x_labels(ax)
        pad = 0.75 + (max(_MIN_BAR_SLOTS - len(years), 0) / 2 if self._bars else 0)
        ax.set_xlim(positions[0] - pad, positions[-1] + pad)
        handles: list = [_method_handle(method, line=not self._bars) for method in self._methods] + mean_handles
        if any_incomplete:
            handles.append(
                Patch(facecolor=theme.TEXT_SECONDARY, hatch=_HATCH, edgecolor=theme.BG, label="Есть дни без данных")
                if self._bars else _hollow_handle()
            )
        _bottom_legend(self.figure, handles, 0.13)
        return True

    def _mean_lines(self, ax, data: TransitionPrecipitation) -> list[Line2D]:
        """Горизонтали средних — цветом и пунктиром своего прочтения; значения —
        в легенде, а не подписью на графике: подпись у края закрывала бы
        последние точки ряда. Возвращает строки легенды."""
        handles = []
        for method in self._methods:
            mean = data.means.get((self._kind, method))
            if mean is None:
                continue
            ax.axhline(mean.value, color=METHOD_COLORS[method], linestyle=":", linewidth=1.4, zorder=1)
            handles.append(Line2D([], [], color=METHOD_COLORS[method], linestyle=":", linewidth=1.4,
                                  label=f"Средняя ({METHOD_NAMES[method]}): {mean.value:.0f} мм"))
        return handles

    def tooltip_at(self, event: MouseEvent) -> str | None:
        data = self._data
        if data is None or event.inaxes is not self._ax or event.xdata is None or not self._years:
            return None
        index = round(event.xdata)
        if self._bars:
            if not 0 <= index < len(self._years):
                return None
            year = self._years[index]
        elif index in self._years:
            year = index
        else:
            return None
        lines = [f"{kind_title(self._kind)}, {period_label(self._kind, year)}"]
        for method in self._methods:
            period = self._value(method, year)
            if period is not None:
                lines.append(f"{METHOD_NAMES[method]}: {period_text(period)}")
            elif (gap := data.gap(self._kind, method, year)) is not None:
                lines.append(f"{METHOD_NAMES[method]}: периода нет — {gap.reason}")
        for method in self._methods:
            if (mean := data.means.get((self._kind, method))) is not None:
                lines.append(mean_text(method, mean))
        return "\n".join(lines)
