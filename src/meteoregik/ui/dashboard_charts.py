"""Графики экрана "Дашборд": теплокарта "год × месяц" и аномалии
среднегодовой температуры с трендом (matplotlib/FigureCanvasQTAgg, как
ui/aggregate_chart_view.py).

Цвет — по задаче цвета (skill dataviz): и в теплокарте, и в аномалиях это
полярность относительно базовой линии, поэтому диверг. шкала "синий ↔
красный" с нейтральным серым посередине (dataviz references/palette.md,
Diverging pair). Середина теплокарты — 0 °C (для агрометеорологии это
осмысленная граница — переход через ноль), шкала симметрична (CenteredNorm),
чтобы одинаковый оттенок означал одинаковое удаление от нуля в обе стороны.

Цвет не единственный носитель смысла: в клетках теплокарты подписаны
значения (пока клеток не слишком много), у столбиков аномалий — легенда
"теплее/холоднее нормы", а точные числа — во всплывающей подсказке.
Подсказки — через QToolTip по движению мыши (canvas.mpl_connect), не
mplcursors: у теплокарты pcolormesh (между клетками — тонкий зазор цвета
фона, dataviz: 2px surface gap), а его mplcursors не поддерживает.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from matplotlib.backend_bases import MouseEvent
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.colors import CenteredNorm, LinearSegmentedColormap
from matplotlib.figure import Figure
from matplotlib.patches import Patch, Rectangle
from numpy import ma
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QToolTip, QVBoxLayout, QWidget

from meteoregik.reports.dashboard_data import (
    MONTHS_GENITIVE,
    MONTHS_NOMINATIVE,
    MONTHS_SHORT,
    DashboardData,
    DecadeCell,
    MonthCell,
    YearAnomaly,
    format_signed,
    format_temperature,
)
from meteoregik.ui import theme

# Диверг. пара dataviz: синие шаги последовательной шкалы (700→100),
# нейтральная середина #f0efec, красное плечо той же длины.
COLD = "#2a78d6"
WARM = "#e34948"
DIVERGING_CMAP = LinearSegmentedColormap.from_list(
    "meteoregik_diverging",
    ["#0d366b", "#256abf", "#86b6ef", "#f0efec", "#f2a39a", "#e34948", "#8c1d1c"],
).with_extremes(bad=theme.BG)


# Больше строк — подписи в клетках становятся мельче читаемого (≈ 8px),
# остаются только цвет и подсказка по наведению.
MAX_ANNOTATED_YEARS = 25
# Больше строк — зазор между клетками съедает саму клетку (у архива в 60+
# лет строка теплокарты всего 3–4 px), клетки рисуются вплотную.
MAX_GAPPED_YEARS = 30
_MAX_YEAR_TICKS = 14
# Шаг подписей годов — "круглый", чтобы подписи шли 1960, 1965, 1970…
_YEAR_TICK_STEPS = (1, 2, 5, 10, 20)


def _text_color_on(rgba: tuple[float, float, float, float]) -> str:
    """Белый текст на тёмной клетке, тёмный — на светлой (по яркости WCAG)."""

    def channel(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b, _ = rgba
    luminance = 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)
    return "#FFFFFF" if luminance < 0.30 else theme.TEXT_PRIMARY


def _style_axes(ax) -> None:
    ax.set_facecolor(theme.BG)
    ax.tick_params(colors=theme.TEXT_SECONDARY, labelsize=9)
    for spine in ax.spines.values():
        spine.set_color(theme.BORDER)


class _ChartCanvas(QWidget):
    """Общая часть: Figure в цвет окна + подсказка по наведению."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.figure = Figure(facecolor=theme.BG)
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.canvas.setMinimumHeight(260)
        self.canvas.mpl_connect("motion_notify_event", self._on_motion)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.canvas)

    def tooltip_at(self, event: MouseEvent) -> str | None:
        raise NotImplementedError

    def _on_motion(self, event: MouseEvent) -> None:
        text = self.tooltip_at(event) if event.inaxes is not None else None
        if text:
            QToolTip.showText(QCursor.pos(), text, self.canvas)
        else:
            QToolTip.hideText()

    def _show_message(self, ax, text: str) -> None:
        ax.set_axis_off()
        ax.text(0.5, 0.5, text, ha="center", va="center", wrap=True,
                color=theme.TEXT_SECONDARY, fontsize=10, transform=ax.transAxes)


# Режимы теплокарты: цвет — сама средняя или её отклонение от нормы.
HEATMAP_VALUE = "value"
HEATMAP_ANOMALY = "anomaly"

_DECADE_COLUMNS = ["1-я дек.", "2-я дек.", "3-я дек."]

HeatCell = MonthCell | DecadeCell


@dataclass(frozen=True)
class HeatGrid:
    """Раскладка теплокарты, общая для двух видов: годы × месяцы и (для
    короткого периода) месяцы × декады."""

    row_labels: list[str]
    col_labels: list[str]
    cells: list[list[HeatCell | None]]
    label_every: int  # подписывать строки, у которых год делится на это число (1 — все)


def _cell_title(cell: HeatCell) -> str:
    if isinstance(cell, DecadeCell):
        return f"{cell.number}-я декада {MONTHS_GENITIVE[cell.start.month - 1]} {cell.start.year}"
    return f"{MONTHS_NOMINATIVE[cell.month - 1].capitalize()} {cell.year}"


def heat_grid(data: DashboardData) -> HeatGrid:
    """Короткий период (меньше 24 месяцев) — строки-месяцы и столбцы-декады:
    в виде "годы × месяцы" такой период — одна-две строки, по ним не виден
    ход температуры внутри месяца. Иначе — строки-годы и столбцы-месяцы."""
    if data.short_period:
        months = sorted({(c.start.year, c.start.month) for c in data.decade_cells.values()})
        by_key = {(c.start.year, c.start.month, c.number): c for c in data.decade_cells.values()}
        return HeatGrid(
            [f"{MONTHS_SHORT[m - 1]} {y}" for y, m in months],
            _DECADE_COLUMNS,
            [[by_key.get((y, m, n)) for n in (1, 2, 3)] for y, m in months],
            1,
        )
    step = next((s for s in _YEAR_TICK_STEPS if len(data.years) / s <= _MAX_YEAR_TICKS), _YEAR_TICK_STEPS[-1])
    return HeatGrid(
        [str(year) for year in data.years],
        list(MONTHS_SHORT),
        [
            [
                cell if (cell := data.month_cells.get((year, m))) is not None and cell.value is not None else None
                for m in range(1, 13)
            ]
            for year in data.years
        ],
        step,
    )


def _cell_number(cell: HeatCell | None, mode: str) -> float | None:
    if cell is None:
        return None
    return cell.anomaly if mode == HEATMAP_ANOMALY else cell.value


def heat_cell_tooltip(title: str, cell: HeatCell | None) -> str:
    """Подсказка клетки — оба числа сразу, какой бы режим ни был выбран."""
    if cell is None or cell.value is None:
        return f"{title}: нет данных"
    lines = [f"{title}: {format_temperature(cell.value)}"]
    if cell.anomaly is None or cell.norm is None:
        lines.append("Норма не рассчитана")
    else:
        lines.append(
            f"Отклонение от нормы: {format_signed(cell.anomaly)} °C (норма {format_temperature(cell.norm)})"
        )
    if not cell.complete:
        if isinstance(cell, DecadeCell):
            lines.append(f"Неполная декада: данные за {cell.days_with_data} из {cell.days_in_decade} дн.")
        else:
            lines.append(f"Неполный месяц: данные за {cell.days_with_data} из {cell.days_in_month} дн.")
    return "\n".join(lines)


class TemperatureHeatmap(_ChartCanvas):
    """Строки — годы (сверху вниз по возрастанию), столбцы — месяцы; для
    короткого периода — строки-месяцы, столбцы-декады. Цвет — средняя
    температура или её отклонение от нормы (set_mode): в абсолютных
    значениях карта показывает в основном смену сезонов (все январи синие,
    все июли красные), а отличия между годами видны по отклонению."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._data: DashboardData | None = None
        self._grid: HeatGrid | None = None
        self._mode = HEATMAP_ANOMALY
        self._ax = None

    @property
    def mode(self) -> str:
        return self._mode

    def set_mode(self, mode: str) -> None:
        self._mode = mode
        if self._data is not None:
            self._draw()

    def plot(self, data: DashboardData) -> None:
        self._data = data
        self._draw()

    def _draw(self) -> None:
        assert self._data is not None
        self.figure.clear()
        grid = self._grid = heat_grid(self._data)
        ax = self._ax = self.figure.add_subplot(111)
        if not grid.row_labels:
            self._show_message(ax, "Нет значений за выбранный период.")
            self.canvas.draw_idle()
            return

        numbers = [[_cell_number(cell, self._mode) for cell in row] for row in grid.cells]
        values = ma.masked_invalid([[math.nan if v is None else v for v in row] for row in numbers])
        halfrange = float(abs(values).max()) if values.count() else 1.0
        norm = CenteredNorm(vcenter=0, halfrange=max(halfrange, 0.5))
        gap = 1.5 if len(grid.row_labels) <= MAX_GAPPED_YEARS else 0
        mesh = ax.pcolormesh(values, cmap=DIVERGING_CMAP, norm=norm, edgecolors=theme.BG, linewidth=gap)
        ax.invert_yaxis()
        _style_axes(ax)
        for spine in ax.spines.values():
            spine.set_visible(False)
        ax.tick_params(length=0)

        ax.set_xticks([c + 0.5 for c in range(len(grid.col_labels))], grid.col_labels)
        ax.xaxis.tick_top()
        ticks = [
            (row, label) for row, label in enumerate(grid.row_labels)
            if grid.label_every == 1 or int(label) % grid.label_every == 0
        ]
        ax.set_yticks([row + 0.5 for row, _ in ticks], [label for _, label in ticks])

        annotate = len(grid.row_labels) <= MAX_ANNOTATED_YEARS
        for row, cells in enumerate(grid.cells):
            for col, cell in enumerate(cells):
                number = numbers[row][col]
                if cell is None or number is None:
                    continue
                if not cell.complete:
                    # Штриховка — неполный месяц/декада (пометка не только цветом).
                    ax.add_patch(Rectangle((col, row), 1, 1, fill=False, hatch="////",
                                           edgecolor=theme.TEXT_SECONDARY, linewidth=0, alpha=0.55))
                if annotate:
                    ax.text(col + 0.5, row + 0.5, format_signed(number), ha="center", va="center",
                            fontsize=7.5, color=_text_color_on(mesh.cmap(mesh.norm(number))))

        colorbar = self.figure.colorbar(mesh, ax=ax, fraction=0.04, pad=0.02)
        colorbar.set_label("отклонение, °C" if self._mode == HEATMAP_ANOMALY else "°C",
                           color=theme.TEXT_SECONDARY)
        colorbar.outline.set_visible(False)
        colorbar.ax.tick_params(colors=theme.TEXT_SECONDARY, labelsize=8, length=0)

        self.figure.tight_layout()
        self.canvas.draw_idle()

    def tooltip_at(self, event: MouseEvent) -> str | None:
        grid = self._grid
        if grid is None or event.inaxes is not self._ax or event.xdata is None or event.ydata is None:
            return None
        col, row = int(event.xdata), int(event.ydata)
        if not (0 <= col < len(grid.col_labels) and 0 <= row < len(grid.row_labels)):
            return None
        cell = grid.cells[row][col]
        if cell is not None:
            return heat_cell_tooltip(_cell_title(cell), cell)
        if self._data is not None and self._data.short_period:
            return heat_cell_tooltip(f"{grid.col_labels[col]}, {grid.row_labels[row]}", None)
        return heat_cell_tooltip(f"{MONTHS_NOMINATIVE[col].capitalize()} {grid.row_labels[row]}", None)


def _rotate_x_labels(ax) -> None:
    """Подписи оси X под 60°: горизонтально 18–24 месяца или десятки лет
    наезжают друг на друга. Привязка правым краем к засечке — иначе
    повёрнутая подпись смещается вправо от своего столбика."""
    for label in ax.get_xticklabels():
        label.set_rotation(60)
        label.set_horizontalalignment("right")
        label.set_rotation_mode("anchor")


def month_anomaly_cells(data: DashboardData) -> list[MonthCell]:
    """Месяцы короткого периода с отклонением от нормы — по порядку."""
    return [data.month_cells[key] for key in sorted(data.month_cells) if data.month_cells[key].anomaly is not None]


class AnomalyChart(_ChartCanvas):
    """Столбики — отклонение среднегодовой от нормы, линия — тренд. Для
    короткого периода (меньше 24 месяцев) полных лет в нём не больше одного,
    и годовой график вырождается в один столбик во всю ширину, — поэтому
    тогда столбики по месяцам: отклонение средней месяца от нормы этого
    календарного месяца (неполные месяцы — со штриховкой, как на теплокарте)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._anomalies: list[YearAnomaly] = []
        self._months: list[MonthCell] = []
        self._ax = None

    def plot(self, data: DashboardData) -> None:
        self.figure.clear()
        self._anomalies, self._months = [], []
        ax = self._ax = self.figure.add_subplot(111)
        if data.short_period:
            self._months = month_anomaly_cells(data)
            if not self._months:
                self._show_message(ax, "Нет месяцев с рассчитанной нормой —\nотклонения построить не из чего.")
                self.canvas.draw_idle()
                return
            self._plot_months(ax)
        else:
            self._anomalies = data.year_anomalies
            if not self._anomalies:
                self._show_message(
                    ax,
                    "В выбранном периоде нет полных лет —\nаномалии по годам построить не из чего.",
                )
                self.canvas.draw_idle()
                return
            self._plot_years(ax, data)
        self.figure.tight_layout()
        self.canvas.draw_idle()

    @staticmethod
    def _style_bars(ax) -> None:
        ax.axhline(0, color=theme.TEXT_SECONDARY, linewidth=0.8)
        _style_axes(ax)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.grid(True, axis="y", color=theme.BORDER, linewidth=0.8)
        ax.set_axisbelow(True)
        ax.set_ylabel("Отклонение от нормы, °C", color=theme.TEXT_SECONDARY, fontsize=9)

    def _plot_years(self, ax, data: DashboardData) -> None:
        years = [a.year for a in self._anomalies]
        ax.bar(years, [a.anomaly for a in self._anomalies], width=0.75,
               color=[WARM if a.anomaly >= 0 else COLD for a in self._anomalies])
        self._style_bars(ax)
        # Подписи — только целые годы с "круглым" шагом и без научной записи:
        # автоматический локатор на 2–3 годах давал "−1, 0, +2.02e3", а один
        # год растягивал столбик на всю ширину — поэтому и явные границы оси.
        step = next((s for s in _YEAR_TICK_STEPS if len(years) / s <= _MAX_YEAR_TICKS), _YEAR_TICK_STEPS[-1])
        ticks = [y for y in range(years[0], years[-1] + 1) if y % step == 0] or [years[0]]
        ax.set_xticks(ticks, [str(y) for y in ticks])
        _rotate_x_labels(ax)
        ax.set_xlim(years[0] - 0.75, years[-1] + 0.75)

        handles = [Patch(color=WARM, label="Теплее нормы"), Patch(color=COLD, label="Холоднее нормы")]
        if data.trend is not None:
            first, last = years[0], years[-1]
            (line,) = ax.plot([first, last], [data.trend.at(first), data.trend.at(last)],
                              color=theme.TEXT_PRIMARY, linewidth=2)
            line.set_label(f"Тренд: {format_signed(data.trend.per_decade, 2)} °C за 10 лет")
            handles.append(line)
        ax.legend(handles=handles, loc="upper left", fontsize=8, frameon=False, labelcolor=theme.TEXT_PRIMARY)

    def _plot_months(self, ax) -> None:
        months = self._months
        positions = list(range(len(months)))
        anomalies = [m.anomaly for m in months]
        bars = ax.bar(positions, anomalies, width=0.75, color=[WARM if a >= 0 else COLD for a in anomalies])
        for bar, cell in zip(bars, months):
            if not cell.complete:
                bar.set_hatch("////")
                bar.set_edgecolor(theme.BG)
        self._style_bars(ax)
        # Подпись месяца — "янв", у января и первого месяца периода ещё и год
        labels = [
            f"{MONTHS_SHORT[m.month - 1]} {m.year}" if i == 0 or m.month == 1 else MONTHS_SHORT[m.month - 1]
            for i, m in enumerate(months)
        ]
        ax.set_xticks(positions, labels)
        ax.tick_params(axis="x", labelsize=8)
        _rotate_x_labels(ax)
        ax.set_xlim(-0.75, len(months) - 0.25)
        handles = [Patch(color=WARM, label="Теплее нормы месяца"), Patch(color=COLD, label="Холоднее нормы месяца")]
        if any(not m.complete for m in months):
            handles.append(Patch(facecolor=theme.TEXT_SECONDARY, hatch="////", edgecolor=theme.BG,
                                 label="Неполный месяц"))
        ax.legend(handles=handles, loc="upper left", fontsize=8, frameon=False, labelcolor=theme.TEXT_PRIMARY)

    def tooltip_at(self, event: MouseEvent) -> str | None:
        if event.inaxes is not self._ax or event.xdata is None:
            return None
        if self._months:
            index = round(event.xdata)
            if 0 <= index < len(self._months):
                cell = self._months[index]
                return heat_cell_tooltip(f"{MONTHS_NOMINATIVE[cell.month - 1].capitalize()} {cell.year}", cell)
            return None
        year = round(event.xdata)
        for anomaly in self._anomalies:
            if anomaly.year == year:
                return (
                    f"{year}: {format_signed(anomaly.anomaly)} °C от нормы\n"
                    f"Среднегодовая {format_temperature(anomaly.value)}"
                )
        return None
