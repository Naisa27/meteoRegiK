"""Графики вкладки "Исходные данные" дашборда (корректировка 2026-10-10):
календарь полноты, распределение наблюдений по срокам, заполненность
параметров rp5. Общая часть (холст, подсказки по наведению) — _ChartCanvas
из ui/dashboard_charts.py.

Цвет (skill dataviz): везде одна величина — "сколько данных есть", поэтому
одна последовательная шкала одного тона (синий, светлый → тёмный), без
"светофора". В календаре тёмное поле — заполненные месяцы, пропуски видны
светлыми пятнами; месяц без единого дня дополнительно заштрихован, чтобы
не сливаться с фоном (и с клетками вне архива станции, которые просто
пусты). Точные числа — в подсказке по наведению.
"""

from __future__ import annotations

import math

from matplotlib.backend_bases import MouseEvent
from matplotlib.colors import LinearSegmentedColormap, Normalize
from matplotlib.patches import Patch, Rectangle
from matplotlib.ticker import PercentFormatter
from numpy import ma
from PySide6.QtWidgets import QWidget

from meteoregik.reports.dashboard_data import MONTHS_NOMINATIVE, MONTHS_SHORT
from meteoregik.reports.source_data import MonthFill, ParameterFill, StationSourceDetail, format_share
from meteoregik.ui import theme
from meteoregik.ui.dashboard_charts import (
    _MAX_YEAR_TICKS,
    _YEAR_TICK_STEPS,
    _ChartCanvas,
    _rotate_x_labels,
    _style_axes,
    _text_color_on,
)
from meteoregik.ui.dashboard_widgets import format_count
from meteoregik.ui.data_browser import _RP5_HEADER_BY_COLUMN, RP5_FIELD_DESCRIPTIONS

# Синие шаги последовательной шкалы dataviz (100 → 700)
FILL = "#256abf"
FILL_CMAP = LinearSegmentedColormap.from_list(
    "meteoregik_fill", ["#e6f0fc", "#86b6ef", "#256abf", "#0d366b"]
).with_extremes(bad=theme.BG)
OFF_GRID = "#9aa7b0"  # наблюдения вне сетки сроков — нейтральный серый

# Ниже строка календаря — подпись процента не помещается в клетку, а зазор
# между клетками съедает саму клетку (px)
MIN_ANNOTATED_ROW_HEIGHT = 15
MIN_GAPPED_ROW_HEIGHT = 9


def month_fill_tooltip(year: int, month: int, cell: MonthFill | None) -> str:
    title = f"{MONTHS_NOMINATIVE[month - 1].capitalize()} {year}"
    if cell is None:
        return f"{title}: вне архива станции"
    return (
        f"{title}: данные за {cell.days_with_data} из {cell.days_expected} дн. "
        f"({format_share(cell.share)})"
    )


class FillCalendar(_ChartCanvas):
    """Строки — годы, столбцы — месяцы, цвет — доля дней месяца, за которые
    есть суточная средняя температура."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._cells: dict[tuple[int, int], MonthFill] = {}
        self._years: list[int] = []
        self._ax = None

    def plot(self, detail: StationSourceDetail) -> None:
        self.figure.clear()
        ax = self._ax = self.figure.add_subplot(111)
        self._cells = {(m.year, m.month): m for m in detail.months}
        self._years = sorted({m.year for m in detail.months})
        if not self._years:
            self._show_message(ax, "По станции нет посчитанных суточных значений.")
            self.canvas.draw_idle()
            return

        years = self._years
        shares = [
            [cell.share if (cell := self._cells.get((year, month))) else math.nan for month in range(1, 13)]
            for year in years
        ]
        # Высота строки зависит и от числа лет, и от высоты окна: 20 лет в
        # окне 750 px — уже 8 px на строку. Сверху и снизу — подписи месяцев и поля.
        row_height = (self.canvas.height() - 45) / len(years)
        gap = 1.5 if row_height >= MIN_GAPPED_ROW_HEIGHT else 0
        mesh = ax.pcolormesh(
            ma.masked_invalid(shares), cmap=FILL_CMAP, norm=Normalize(0, 1), edgecolors=theme.BG, linewidth=gap
        )
        ax.invert_yaxis()
        _style_axes(ax)
        for spine in ax.spines.values():
            spine.set_visible(False)
        ax.tick_params(length=0)
        ax.set_xticks([c + 0.5 for c in range(12)], MONTHS_SHORT)
        ax.xaxis.tick_top()
        step = next((s for s in _YEAR_TICK_STEPS if len(years) / s <= _MAX_YEAR_TICKS), _YEAR_TICK_STEPS[-1])
        ticks = [(row, year) for row, year in enumerate(years) if step == 1 or year % step == 0]
        ax.set_yticks([row + 0.5 for row, _ in ticks], [str(year) for _, year in ticks])

        annotate = row_height >= MIN_ANNOTATED_ROW_HEIGHT
        for row, year in enumerate(years):
            for col in range(12):
                cell = self._cells.get((year, col + 1))
                if cell is None or cell.days_with_data == cell.days_expected:
                    continue
                if cell.days_with_data == 0:
                    ax.add_patch(Rectangle((col, row), 1, 1, fill=False, hatch="////",
                                           edgecolor=theme.TEXT_SECONDARY, linewidth=0, alpha=0.55))
                if annotate:
                    # Подписаны только неполные месяцы — взгляд сразу идёт к пропускам
                    ax.text(col + 0.5, row + 0.5, format_share(cell.share).replace(" %", ""),
                            ha="center", va="center", fontsize=7.5,
                            color=_text_color_on(mesh.cmap(mesh.norm(cell.share))))

        colorbar = self.figure.colorbar(mesh, ax=ax, fraction=0.04, pad=0.02, format=PercentFormatter(1.0))
        colorbar.set_label("дней с данными", color=theme.TEXT_SECONDARY)
        colorbar.outline.set_visible(False)
        colorbar.ax.tick_params(colors=theme.TEXT_SECONDARY, labelsize=8, length=0)
        self.figure.tight_layout()
        self.canvas.draw_idle()

    def tooltip_at(self, event: MouseEvent) -> str | None:
        if event.inaxes is not self._ax or event.xdata is None or event.ydata is None:
            return None
        col, row = int(event.xdata), int(event.ydata)
        if not (0 <= col < 12 and 0 <= row < len(self._years)):
            return None
        year = self._years[row]
        return month_fill_tooltip(year, col + 1, self._cells.get((year, col + 1)))


def obs_hour_tooltip(hour: int, count: int, obs_days: int, on_grid: bool) -> str:
    if not on_grid:
        return f"{hour:02d}:00 — вне сетки сроков: {format_count(count)} набл."
    share = f" ({format_share(count / obs_days)})" if obs_days else ""
    return (
        f"Срок {hour:02d}:00: температура есть в {format_count(count)} "
        f"из {format_count(obs_days)} дн.{share}"
    )


class ObsHoursChart(_ChartCanvas):
    """Столбик на каждый срок — в скольких днях на этот срок есть
    температура. Линия — число дней с наблюдениями: столбик до линии — срок
    без пропусков, заметно ниже — срок систематически отсутствует."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._hours: list[int] = []
        self._detail: StationSourceDetail | None = None
        self._ax = None

    def plot(self, detail: StationSourceDetail) -> None:
        self.figure.clear()
        self._detail = detail
        ax = self._ax = self.figure.add_subplot(111)
        self._hours = sorted({*detail.obs_hours, *detail.hour_counts}) if detail.hour_counts else []
        if not self._hours:
            self._show_message(ax, "По станции нет срочных наблюдений rp5\n(данные ГГ — уже суточные).")
            self.canvas.draw_idle()
            return

        positions = list(range(len(self._hours)))
        counts = [detail.hour_counts.get(hour, 0) for hour in self._hours]
        off_grid = [hour not in detail.obs_hours for hour in self._hours]
        ax.bar(positions, counts, width=0.7, color=[OFF_GRID if off else FILL for off in off_grid])
        (line,) = ax.plot([-0.6, len(positions) - 0.4], [detail.obs_days] * 2,
                          color=theme.TEXT_PRIMARY, linewidth=1.2, linestyle=(0, (4, 3)))
        _style_axes(ax)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.grid(True, axis="y", color=theme.BORDER, linewidth=0.8)
        ax.set_axisbelow(True)
        ax.set_xticks(positions, [f"{hour:02d}:00" for hour in self._hours])
        if len(positions) > 10:
            _rotate_x_labels(ax)
        ax.set_xlim(-0.6, len(positions) - 0.4)
        ax.set_ylim(0, max(detail.obs_days, *counts) * 1.14)
        ax.set_ylabel("Дней с температурой в срок", color=theme.TEXT_SECONDARY, fontsize=9)
        ax.yaxis.set_major_formatter(lambda value, _: format_count(int(value)))

        line.set_label(f"Дней с наблюдениями: {format_count(detail.obs_days)}")
        handles = [line]
        if any(off_grid):
            handles.append(Patch(color=OFF_GRID, label="Вне сетки сроков"))
        ax.legend(handles=handles, loc="upper right", fontsize=8, frameon=False,
                  labelcolor=theme.TEXT_PRIMARY, ncols=len(handles))
        self.figure.tight_layout()
        self.canvas.draw_idle()

    def tooltip_at(self, event: MouseEvent) -> str | None:
        detail = self._detail
        if detail is None or event.inaxes is not self._ax or event.xdata is None:
            return None
        index = round(event.xdata)
        if not 0 <= index < len(self._hours):
            return None
        hour = self._hours[index]
        return obs_hour_tooltip(hour, detail.hour_counts.get(hour, 0), detail.obs_days, hour in detail.obs_hours)


def parameter_code(column: str) -> str:
    """Код столбца как в файле rp5.ru: "T_temperature" -> "T"."""
    return _RP5_HEADER_BY_COLUMN.get(column, column)


def parameter_tooltip(fill: ParameterFill) -> str:
    name = RP5_FIELD_DESCRIPTIONS.get(fill.column, fill.column)
    return (
        f"{parameter_code(fill.column)} — {name}\n"
        f"Заполнено {format_count(fill.filled)} из {format_count(fill.total)} строк ({format_share(fill.share)})"
    )


class ParameterFillChart(_ChartCanvas):
    """Столбик на каждый параметр rp5 (в порядке столбцов файла) — доля
    строк станции, где значение заполнено."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._parameters: list[ParameterFill] = []
        self._ax = None

    def plot(self, detail: StationSourceDetail) -> None:
        self.figure.clear()
        ax = self._ax = self.figure.add_subplot(111)
        self._parameters = detail.parameters
        if not self._parameters:
            self._show_message(ax, "По станции нет данных rp5 —\nпараметры есть только в этом формате.")
            self.canvas.draw_idle()
            return

        positions = list(range(len(self._parameters)))
        ax.bar(positions, [p.share for p in self._parameters], width=0.7, color=FILL)
        _style_axes(ax)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.grid(True, axis="y", color=theme.BORDER, linewidth=0.8)
        ax.set_axisbelow(True)
        ax.set_xticks(positions, [parameter_code(p.column) for p in self._parameters])
        ax.tick_params(axis="x", labelsize=8)
        _rotate_x_labels(ax)
        ax.set_xlim(-0.6, len(positions) - 0.4)
        ax.set_ylim(0, 1)
        ax.yaxis.set_major_formatter(PercentFormatter(1.0))
        ax.set_ylabel("Заполнено строк", color=theme.TEXT_SECONDARY, fontsize=9)
        self.figure.tight_layout()
        self.canvas.draw_idle()

    def tooltip_at(self, event: MouseEvent) -> str | None:
        if event.inaxes is not self._ax or event.xdata is None:
            return None
        index = round(event.xdata)
        if not 0 <= index < len(self._parameters):
            return None
        return parameter_tooltip(self._parameters[index])
