"""Графики вкладки "Осадки" экрана "Дашборд": теплокарта "год × месяц",
годовые суммы относительно нормы с трендом, суммы по сезонам и годовой ход
(matplotlib, общая основа — ui/dashboard_charts.py).

Цвет. У температуры пара "синий ↔ красный"; у осадков — "охра ↔
сине-зелёный" (сухо ↔ влажно), принятая в климатологии для аномалий осадков:
красно-синяя пара здесь читалась бы как "жарко/холодно". Сама сумма — не
полярность, а количество, поэтому в режиме "Сумма" шкала последовательная
(светлое — мало, тёмное — много), а в режиме "% нормы" — расходящаяся с
серединой в 100 %. Шкала процентов обрезана на 0–200 %: один ливневый месяц
в 400 % нормы иначе выбелил бы всю остальную карту.

Цвет не единственный носитель смысла: в клетках подписаны числа, неполные
месяцы и сезоны заштрихованы, у столбиков — легенда, точные значения — в
подсказке по наведению.
"""

from __future__ import annotations

from dataclasses import dataclass

from matplotlib.backend_bases import MouseEvent
from matplotlib.colors import CenteredNorm, LinearSegmentedColormap, Normalize
from matplotlib.lines import Line2D
from matplotlib.patches import Patch, Rectangle
from numpy import ma
from PySide6.QtWidgets import QWidget

from meteoregik.reports.dashboard_data import MONTHS_GENITIVE, MONTHS_NOMINATIVE, MONTHS_SHORT, format_signed
from meteoregik.reports.precipitation_data import (
    DecadeSum,
    MonthSum,
    PrecipitationData,
    SeasonSum,
    YearSum,
    describe_percent,
    format_mm,
)
from meteoregik.ui import theme
from meteoregik.ui.dashboard_charts import (
    _MAX_YEAR_TICKS,
    _YEAR_TICK_STEPS,
    MAX_ANNOTATED_YEARS,
    MAX_GAPPED_YEARS,
    _ChartCanvas,
    _rotate_x_labels,
    _style_axes,
    _text_color_on,
)

DRY = "#B9772A"  # охра: 3.6:1 на фоне окна
WET = "#2A8C82"  # сине-зелёный: 3.9:1
NORM_BAR = "#C3D2DB"  # столбики нормы — фон, на котором читается выбранный год
SELECTED_YEAR = "#0B4A47"
SUM_CMAP = LinearSegmentedColormap.from_list(
    "meteoregik_rain", ["#f0efec", "#bfe0dc", "#5fb3aa", "#1f7f78", "#0b4a47"]
).with_extremes(bad=theme.BG)
PERCENT_CMAP = LinearSegmentedColormap.from_list(
    "meteoregik_dry_wet", ["#5e3a08", "#b9772a", "#e6c995", "#f0efec", "#9fd3cc", "#2a8c82", "#0b4a47"]
).with_extremes(bad=theme.BG)

HEATMAP_PERCENT = "percent"
HEATMAP_SUM = "sum"
# Предел шкалы "% нормы": всё, что выше, — самым тёмным цветом
PERCENT_SCALE_MAX = 200.0

_HATCH = "////"


def _year_ticks(years: list[int]) -> list[int]:
    """Целые годы с "круглым" шагом (1960, 1965…)."""
    step = next((s for s in _YEAR_TICK_STEPS if len(years) / s <= _MAX_YEAR_TICKS), _YEAR_TICK_STEPS[-1])
    return [y for y in range(years[0], years[-1] + 1) if y % step == 0] or [years[0]]


def _norm_text(value: float, norm: float | None, percent: float | None) -> str:
    """"112 % нормы (+9.1 мм, норма 76.2 мм)" или причина, почему сравнения нет."""
    if norm is None:
        return "Норма не рассчитана"
    if percent is None:
        return f"Норма {format_mm(norm)}"
    return f"{describe_percent(percent)} ({format_signed(value - norm)} мм, норма {format_mm(norm)})"


HeatCell = MonthSum | DecadeSum


def month_tooltip(title: str, cell: HeatCell | None) -> str:
    """Подсказка клетки теплокарты и точки графика — месяца или декады."""
    if cell is None:
        return f"{title}: нет данных"
    lines = [f"{title}: {format_mm(cell.value)}", _norm_text(cell.value, cell.norm, cell.percent)]
    if not cell.complete:
        if isinstance(cell, DecadeSum):
            lines.append(f"Неполная декада: данные за {cell.days_with_data} из {cell.days_in_decade} дн.")
        else:
            lines.append(f"Неполный месяц: данные за {cell.days_with_data} из {cell.days_in_month} дн.")
    return "\n".join(lines)


def _cell_title(cell: HeatCell) -> str:
    if isinstance(cell, DecadeSum):
        return f"{cell.number}-я декада {MONTHS_GENITIVE[cell.start.month - 1]} {cell.start.year}"
    return f"{MONTHS_NOMINATIVE[cell.month - 1].capitalize()} {cell.year}"


_DECADE_COLUMNS = ["1-я дек.", "2-я дек.", "3-я дек."]


@dataclass(frozen=True)
class HeatGrid:
    """Раскладка теплокарты: годы × месяцы или (короткий период) месяцы × декады."""

    row_labels: list[str]
    col_labels: list[str]
    cells: list[list[HeatCell | None]]
    labeled_rows: frozenset[int]  # номера строк с подписью


def heat_grid(data: PrecipitationData) -> HeatGrid:
    """Короткий период — строки-месяцы и столбцы-декады: в виде "годы ×
    месяцы" это одна-две строки (как у теплокарты температуры)."""
    if data.short_period:
        months = sorted({(c.start.year, c.start.month) for c in data.decade_cells.values()})
        by_key = {(c.start.year, c.start.month, c.number): c for c in data.decade_cells.values()}
        return HeatGrid(
            [f"{MONTHS_SHORT[m - 1]} {y}" for y, m in months],
            _DECADE_COLUMNS,
            [[by_key.get((y, m, n)) for n in (1, 2, 3)] for y, m in months],
            frozenset(range(len(months))),
        )
    years = data.years
    ticks = set(_year_ticks(years)) if len(years) > _MAX_YEAR_TICKS else set(years)
    return HeatGrid(
        [str(year) for year in years],
        list(MONTHS_SHORT),
        [[data.month_cells.get((year, month)) for month in range(1, 13)] for year in years],
        frozenset(row for row, year in enumerate(years) if year in ticks),
    )


class PrecipitationHeatmap(_ChartCanvas):
    """Строки — годы, столбцы — месяцы (для короткого периода — месяцы и
    декады); цвет — сумма осадков или её доля от нормы того же месяца/декады
    (set_mode). В суммах карта показывает годовой ход (летние месяцы
    темнее), по доле нормы видны сухие и влажные годы."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._data: PrecipitationData | None = None
        self._grid: HeatGrid | None = None
        self._mode = HEATMAP_PERCENT
        self._ax = None

    @property
    def mode(self) -> str:
        return self._mode

    def set_mode(self, mode: str) -> None:
        self._mode = mode
        if self._data is not None:
            self._draw()

    def plot(self, data: PrecipitationData) -> None:
        self._data = data
        self._draw()

    def _number(self, cell: HeatCell | None) -> float | None:
        if cell is None:
            return None
        return cell.percent if self._mode == HEATMAP_PERCENT else cell.value

    def _draw(self) -> None:
        data = self._data
        assert data is not None
        self.figure.clear()
        ax = self._ax = self.figure.add_subplot(111)
        grid = self._grid = heat_grid(data)
        if not grid.row_labels:
            self._show_message(ax, "Нет значений за выбранный период.")
            self.canvas.draw_idle()
            return

        cells = grid.cells
        numbers = [[self._number(cell) for cell in row] for row in cells]
        values = ma.masked_invalid([[float("nan") if v is None else v for v in row] for row in numbers])
        if self._mode == HEATMAP_PERCENT:
            cmap, norm = PERCENT_CMAP, CenteredNorm(vcenter=100, halfrange=PERCENT_SCALE_MAX - 100, clip=True)
        else:
            cmap, norm = SUM_CMAP, Normalize(vmin=0, vmax=max(float(values.max()) if values.count() else 1.0, 1.0))
        gap = 1.5 if len(grid.row_labels) <= MAX_GAPPED_YEARS else 0
        mesh = ax.pcolormesh(values, cmap=cmap, norm=norm, edgecolors=theme.BG, linewidth=gap)
        ax.invert_yaxis()
        _style_axes(ax)
        for spine in ax.spines.values():
            spine.set_visible(False)
        ax.tick_params(length=0)
        ax.set_xticks([c + 0.5 for c in range(len(grid.col_labels))], grid.col_labels)
        ax.xaxis.tick_top()
        labeled = sorted(grid.labeled_rows)
        ax.set_yticks([row + 0.5 for row in labeled], [grid.row_labels[row] for row in labeled])

        annotate = len(grid.row_labels) <= MAX_ANNOTATED_YEARS
        for row, row_cells in enumerate(cells):
            for col, cell in enumerate(row_cells):
                number = numbers[row][col]
                if cell is None or number is None:
                    continue
                if not cell.complete:
                    ax.add_patch(Rectangle((col, row), 1, 1, fill=False, hatch=_HATCH,
                                           edgecolor=theme.TEXT_SECONDARY, linewidth=0, alpha=0.55))
                if annotate:
                    ax.text(col + 0.5, row + 0.5, f"{number:.0f}", ha="center", va="center",
                            fontsize=7.5, color=_text_color_on(mesh.cmap(mesh.norm(number))))

        colorbar = self.figure.colorbar(mesh, ax=ax, fraction=0.04, pad=0.02)
        colorbar.set_label("% нормы" if self._mode == HEATMAP_PERCENT else "мм", color=theme.TEXT_SECONDARY)
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
            return month_tooltip(_cell_title(cell), cell)
        if self._data is not None and self._data.short_period:
            return month_tooltip(f"{grid.col_labels[col]}, {grid.row_labels[row]}", None)
        return month_tooltip(f"{MONTHS_NOMINATIVE[col].capitalize()} {grid.row_labels[row]}", None)


def _style_bars(ax, ylabel: str) -> None:
    _style_axes(ax)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(True, axis="y", color=theme.BORDER, linewidth=0.8)
    ax.set_axisbelow(True)
    ax.set_ylabel(ylabel, color=theme.TEXT_SECONDARY, fontsize=9)


class AnnualSumChart(_ChartCanvas):
    """Столбики — отклонение годовой суммы от нормы в мм (влажнее/суше),
    линия — тренд. Только полные годы: сумма неполного года занижена. Для
    короткого периода (меньше 24 месяцев) — столбики по месяцам: отклонение
    суммы месяца от нормы этого месяца, неполные месяцы — со штриховкой."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._years: list[YearSum] = []
        self._months: list[MonthSum] = []
        self._ax = None

    def plot(self, data: PrecipitationData) -> None:
        self.figure.clear()
        ax = self._ax = self.figure.add_subplot(111)
        self._years, self._months = [], []
        if data.short_period:
            self._months = [cell for _, cell in sorted(data.month_cells.items()) if cell.anomaly is not None]
            if not self._months:
                self._show_message(ax, "Нет месяцев с рассчитанной нормой —\nотклонения построить не из чего.")
            else:
                self._plot_months(ax)
                self.figure.tight_layout()
            self.canvas.draw_idle()
            return
        self._years = [item for item in data.year_sums if item.anomaly is not None]
        if not self._years:
            self._show_message(ax, "В выбранном периоде нет полных лет —\nгодовые суммы сравнить с нормой не из чего.")
            self.canvas.draw_idle()
            return
        years = [item.year for item in self._years]
        anomalies = [item.anomaly for item in self._years]
        ax.bar(years, anomalies, width=0.75, color=[WET if a >= 0 else DRY for a in anomalies])
        ax.axhline(0, color=theme.TEXT_SECONDARY, linewidth=0.8)
        _style_bars(ax, "Отклонение от нормы, мм")
        ticks = _year_ticks(years)
        ax.set_xticks(ticks, [str(y) for y in ticks])
        _rotate_x_labels(ax)
        ax.set_xlim(years[0] - 0.75, years[-1] + 0.75)

        handles = [Patch(color=WET, label="Влажнее нормы"), Patch(color=DRY, label="Суше нормы")]
        if data.trend is not None:
            first, last = years[0], years[-1]
            (line,) = ax.plot([first, last], [data.trend.at(first), data.trend.at(last)],
                              color=theme.TEXT_PRIMARY, linewidth=2)
            line.set_label(f"Тренд: {format_signed(data.trend.per_decade)} мм за 10 лет")
            handles.append(line)
        ax.legend(handles=handles, loc="upper left", fontsize=8, frameon=False, labelcolor=theme.TEXT_PRIMARY)
        self.figure.tight_layout()
        self.canvas.draw_idle()

    def _plot_months(self, ax) -> None:
        months = self._months
        positions = list(range(len(months)))
        anomalies = [m.anomaly for m in months]
        bars = ax.bar(positions, anomalies, width=0.75, color=[WET if a >= 0 else DRY for a in anomalies])
        for bar, cell in zip(bars, months):
            if not cell.complete:
                bar.set_hatch(_HATCH)
                bar.set_edgecolor(theme.BG)
        ax.axhline(0, color=theme.TEXT_SECONDARY, linewidth=0.8)
        _style_bars(ax, "Отклонение от нормы, мм")
        # Подпись месяца — "янв", у января и первого месяца периода ещё и год
        labels = [
            f"{MONTHS_SHORT[m.month - 1]} {m.year}" if i == 0 or m.month == 1 else MONTHS_SHORT[m.month - 1]
            for i, m in enumerate(months)
        ]
        ax.set_xticks(positions, labels)
        ax.tick_params(axis="x", labelsize=8)
        _rotate_x_labels(ax)
        ax.set_xlim(-0.75, len(months) - 0.25)
        low, high = ax.get_ylim()
        ax.set_ylim(low, high + (high - low) * 0.18)  # запас сверху — под легенду
        handles = [Patch(color=WET, label="Влажнее нормы месяца"), Patch(color=DRY, label="Суше нормы месяца")]
        if any(not m.complete for m in months):
            handles.append(Patch(facecolor=theme.TEXT_SECONDARY, hatch=_HATCH, edgecolor=theme.BG,
                                 label="Неполный месяц"))
        ax.legend(handles=handles, loc="upper left", fontsize=8, frameon=False, labelcolor=theme.TEXT_PRIMARY)

    def tooltip_at(self, event: MouseEvent) -> str | None:
        if event.inaxes is not self._ax or event.xdata is None:
            return None
        if self._months:
            index = round(event.xdata)
            return month_tooltip(_cell_title(self._months[index]), self._months[index]) if (
                0 <= index < len(self._months)
            ) else None
        year = round(event.xdata)
        for item in self._years:
            if item.year == year:
                return f"{year}: {format_mm(item.value)}\n{_norm_text(item.value, item.norm, item.percent)}"
        return None


SEASON_ORDER = (12, 3, 6, 9)  # зима, весна, лето, осень — по первому месяцу


def season_tooltip(season: SeasonSum) -> str:
    lines = [
        f"{season.name.capitalize()} {season.year}: {format_mm(season.value)}",
        _norm_text(season.value, season.norm, season.percent),
    ]
    if not season.complete:
        lines.append("Неполный сезон: в его месяцах есть пропуски, сумма занижена")
    return "\n".join(lines)


class SeasonsChart(_ChartCanvas):
    """Четыре панели — зима, весна, лето, осень: суммы сезона по годам и
    линия его нормы. Панели, а не четыре столбика на год: сравнивать нужно
    один и тот же сезон между годами, а не сезоны внутри года. Шкала Y —
    общая, чтобы высота столбика означала одно и то же на всех панелях."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.canvas.setMinimumHeight(340)
        self._by_axes: dict[object, dict[int, SeasonSum]] = {}

    def plot(self, data: PrecipitationData) -> None:
        self.figure.clear()
        self._by_axes = {}
        if not data.seasons:
            self._show_message(self.figure.add_subplot(111), "Нет сезонных сумм за выбранный период.")
            self.canvas.draw_idle()
            return
        years = sorted({season.year for season in data.seasons})
        axes = self.figure.subplots(2, 2, sharex=True, sharey=True)
        ticks = _year_ticks(years)
        for ax, first_month in zip(axes.flat, SEASON_ORDER):
            seasons = [season for season in data.seasons if season.start.month == first_month]
            bars = ax.bar([s.year for s in seasons], [s.value for s in seasons], width=0.75, color=WET)
            for bar, season in zip(bars, seasons):
                if not season.complete:
                    bar.set_hatch(_HATCH)
                    bar.set_edgecolor(theme.BG)
            norm = next((s.norm for s in seasons if s.norm is not None), None)
            title = SEASON_TITLES[first_month]
            if norm is not None:
                ax.axhline(norm, color=theme.TEXT_PRIMARY, linewidth=1.2, linestyle="--")
                title += f" — норма {norm:.0f} мм"
            ax.set_title(title, fontsize=9, color=theme.TEXT_PRIMARY, loc="left")
            _style_bars(ax, "")
            ax.set_xticks(ticks, [str(y) for y in ticks])
            ax.set_xlim(years[0] - 0.75, years[-1] + 0.75)
            ax.tick_params(axis="x", labelsize=8)
            _rotate_x_labels(ax)
            self._by_axes[ax] = {s.year: s for s in seasons}
        for ax in axes[:, 0]:
            ax.set_ylabel("мм", color=theme.TEXT_SECONDARY, fontsize=9)
        handles = [Line2D([], [], color=theme.TEXT_PRIMARY, linewidth=1.2, linestyle="--", label="Норма сезона")]
        if any(not season.complete for season in data.seasons):
            handles.append(Patch(facecolor=WET, hatch=_HATCH, edgecolor=theme.BG, label="Неполный сезон"))
        self.figure.legend(handles=handles, loc="lower center", ncols=len(handles), fontsize=8, frameon=False,
                           labelcolor=theme.TEXT_PRIMARY)
        self.figure.tight_layout(rect=(0, 0.05, 1, 1))
        self.canvas.draw_idle()

    def tooltip_at(self, event: MouseEvent) -> str | None:
        seasons = self._by_axes.get(event.inaxes)
        if seasons is None or event.xdata is None:
            return None
        season = seasons.get(round(event.xdata))
        return season_tooltip(season) if season is not None else None


SEASON_TITLES: dict[int, str] = {12: "Зима", 3: "Весна", 6: "Лето", 9: "Осень"}


class AnnualCourseChart(_ChartCanvas):
    """Годовой ход: столбики — нормы двенадцати месяцев, точки с линией —
    суммы месяцев выбранного года (set_year). Неполный месяц года — пустой
    кружок: его сумма занижена."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._data: PrecipitationData | None = None
        self._year: int | None = None
        self._ax = None

    @property
    def year(self) -> int | None:
        return self._year

    def plot(self, data: PrecipitationData, year: int | None) -> None:
        self._data = data
        self._year = year
        self._draw()

    def set_year(self, year: int | None) -> None:
        self._year = year
        if self._data is not None:
            self._draw()

    def _year_cells(self) -> list[MonthSum | None]:
        assert self._data is not None
        return [self._data.month_cells.get((self._year, month)) for month in range(1, 13)]

    def _draw(self) -> None:
        data = self._data
        assert data is not None
        self.figure.clear()
        ax = self._ax = self.figure.add_subplot(111)
        cells = self._year_cells() if self._year is not None else [None] * 12
        if not data.month_norms and not any(cells):
            self._show_message(ax, "Нет месячных норм и сумм за выбранный период.")
            self.canvas.draw_idle()
            return
        positions = list(range(12))
        handles: list[object] = []
        if data.month_norms:
            ax.bar(positions, [data.month_norms.get(month, 0.0) for month in range(1, 13)], width=0.75, color=NORM_BAR)
            handles.append(Patch(color=NORM_BAR, label="Норма месяца"))
        points = [(pos, cell) for pos, cell in zip(positions, cells) if cell is not None]
        if points:
            ax.plot([pos for pos, _ in points], [cell.value for _, cell in points], color=SELECTED_YEAR, linewidth=1.8)
            for complete, face in ((True, SELECTED_YEAR), (False, theme.BG)):
                part = [(pos, cell) for pos, cell in points if cell.complete is complete]
                ax.plot([pos for pos, _ in part], [cell.value for _, cell in part], linestyle="none", marker="o",
                        markersize=6, markerfacecolor=face, markeredgecolor=SELECTED_YEAR, markeredgewidth=1.5)
            handles.append(Line2D([], [], color=SELECTED_YEAR, linewidth=1.8, marker="o", markersize=6,
                                  label=f"{self._year} год"))
            if any(not cell.complete for _, cell in points):
                handles.append(Line2D([], [], linestyle="none", marker="o", markersize=6, markerfacecolor=theme.BG,
                                      markeredgecolor=SELECTED_YEAR, markeredgewidth=1.5, label="Неполный месяц"))
        _style_bars(ax, "мм")
        ax.set_xticks(positions, MONTHS_SHORT)
        ax.set_xlim(-0.75, 11.75)
        ax.set_ylim(bottom=0, top=ax.get_ylim()[1] * 1.2)  # запас сверху — под легенду
        ax.legend(handles=handles, loc="upper left", fontsize=8, frameon=False, labelcolor=theme.TEXT_PRIMARY)
        self.figure.tight_layout()
        self.canvas.draw_idle()

    def tooltip_at(self, event: MouseEvent) -> str | None:
        data = self._data
        if data is None or event.inaxes is not self._ax or event.xdata is None:
            return None
        index = round(event.xdata)
        if not 0 <= index < 12:
            return None
        month = MONTHS_NOMINATIVE[index].capitalize()
        cell = self._year_cells()[index] if self._year is not None else None
        if cell is not None:
            return month_tooltip(f"{month} {self._year}", cell)
        norm = data.month_norms.get(index + 1)
        return f"{month}: норма {format_mm(norm)}" if norm is not None else None
