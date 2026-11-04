"""Снежные графики: весь архив, отдельные шкалы и подсказки наблюдений."""

from __future__ import annotations

from typing import TYPE_CHECKING

from matplotlib.backend_bases import MouseEvent
from matplotlib.colors import CenteredNorm, LinearSegmentedColormap, Normalize
from matplotlib.patches import Patch, Rectangle
from numpy import ma
from PySide6.QtCore import QCoreApplication, QEvent, QObject, Qt
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QHBoxLayout, QLabel, QScrollArea, QScrollBar, QToolTip, QWidget

from meteoregik.export.snow_excel import coverage_text
from meteoregik.reports.dashboard_data import MONTHS_SHORT
from meteoregik.reports.snow_data import SnowData, SnowPeriod, format_maximum_dates
from meteoregik.ui import theme
from meteoregik.ui.dashboard_charts import DIVERGING_CMAP, _ChartCanvas, _style_axes, _text_color_on
from meteoregik.ui.dashboard_period_charts import TIME_SCROLL_QSS
from meteoregik.ui.dashboard_widgets import tooltip_html

if TYPE_CHECKING:
    from meteoregik.reports.snow_data import SnowHeatCell

SNOW_CMAP = LinearSegmentedColormap.from_list("snow_days", ["#F4F7FA", "#A6CBE8", "#236491"])


class _SnowCanvas(_ChartCanvas):
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self.canvas and event.type() == QEvent.Type.Wheel:
            # FigureCanvas поглощает колесо; одного ignore() недостаточно
            # при вложенных QWidget. Передаём событие полосе всей страницы.
            owner = self.parentWidget()
            while owner is not None:
                if isinstance(owner, QScrollArea):
                    QCoreApplication.sendEvent(owner.verticalScrollBar(), event)
                    return True
                owner = owner.parentWidget()
        return super().eventFilter(watched, event)

    def _on_motion(self, event: MouseEvent) -> None:
        text = self.tooltip_at(event) if event.inaxes is not None else None
        if text:
            QToolTip.showText(QCursor.pos(), tooltip_html(text), self.canvas)
        else:
            QToolTip.hideText()


class SnowSeriesChart(_SnowCanvas):
    """Под графиком — доступ ко всем периодам, без усечения архива."""

    def __init__(self, parent: QWidget | None = None, *, height: bool = False, seasonal: bool = False) -> None:
        super().__init__(parent)
        self.height, self.seasonal = height, seasonal
        self.periods: tuple[SnowPeriod, ...] = ()
        self._ax = None
        self.scrollbar = QScrollBar(Qt.Orientation.Horizontal)
        self.scrollbar.setObjectName("timeScroll")
        self.scrollbar.setStyleSheet(TIME_SCROLL_QSS)
        self.scrollbar.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.scrollbar.setAccessibleName("Прокрутка снежного графика по периодам")
        self.scrollbar.valueChanged.connect(self._scroll)
        self.window_label = QLabel()
        self.window_label.setObjectName("chartNote")
        self.scroll_row = QWidget()
        row = QHBoxLayout(self.scroll_row)
        row.setContentsMargins(60, 0, 8, 0)
        row.addWidget(self.scrollbar, 1)
        row.addWidget(self.window_label)
        self.layout().addWidget(self.scroll_row)
        self.scroll_row.hide()

    def plot(self, periods: tuple[SnowPeriod, ...]) -> None:
        self.periods = periods
        self.figure.clear()
        ax = self._ax = self.figure.add_subplot(111)
        if not periods:
            self._show_message(ax, "За выбранный период данных нет.")
            self.scroll_row.hide()
            self.canvas.draw_idle()
            return
        positions = list(range(len(periods)))
        if self.height:
            ax.plot(positions, [p.max_depth for p in periods], color=theme.ACCENT_SKY,
                    marker="o", markersize=3, label="Максимальная высота")
            ax.set_ylabel("Высота, см", color=theme.TEXT_SECONDARY)
            handles = [Patch(facecolor=theme.ACCENT_SKY, label="Максимальная высота снега")]
        else:
            ax.bar([x - .2 for x in positions], [p.snow_days for p in periods], width=.4,
                   color=theme.ACCENT_SKY, label="Дни со снегом")
            ax.bar([x + .2 for x in positions],
                   [p.frost_days if p.frost_days is not None else float("nan") for p in periods],
                   width=.4, color=theme.ACCENT, label="Морозы при малоснежье")
            ax.set_ylabel("Дни", color=theme.TEXT_SECONDARY)
            handles = [Patch(facecolor=theme.ACCENT_SKY, label="Дни со снегом"),
                       Patch(facecolor=theme.ACCENT, label="Морозы при малоснежье")]
        if any(p.partial or p.temperature_days < p.available_days for p in periods):
            handles.append(Patch(facecolor=theme.BG, edgecolor=theme.TEXT_SECONDARY,
                                 hatch="////", label="Доступная часть / есть дни без температуры"))
            if not self.height:
                for x, period in enumerate(periods):
                    if period.partial:
                        ax.bar(x - .2, period.snow_days, width=.4, fill=False, hatch="////",
                               edgecolor=theme.TEXT_SECONDARY, linewidth=.5)
                    if period.partial or period.temperature_days < period.available_days:
                        if period.frost_days is not None:
                            ax.bar(x + .2, period.frost_days, width=.4, fill=False, hatch="////",
                                   edgecolor=theme.TEXT_SECONDARY, linewidth=.5)
        _style_axes(ax)
        ax.set_ylim(bottom=0)
        ax.grid(axis="y", color=theme.BORDER, alpha=.6)
        ax.set_axisbelow(True)
        self.figure.legend(handles=handles, loc="lower center", ncol=2, frameon=False,
                           fontsize=9, labelcolor=theme.TEXT_PRIMARY)
        self.figure.subplots_adjust(left=.08, right=.98, top=.94, bottom=.30)
        window = 20 if self.seasonal else 60
        self.scrollbar.blockSignals(True)
        self.scrollbar.setRange(0, max(0, len(periods) - window))
        self.scrollbar.setPageStep(window)
        self.scrollbar.setValue(self.scrollbar.maximum())
        self.scrollbar.blockSignals(False)
        self.scroll_row.setVisible(self.scrollbar.maximum() > 0)
        self._scroll(self.scrollbar.value())

    def _label(self, period: SnowPeriod) -> str:
        return (f"{period.start.year}/{period.end.year % 100:02d}" if self.seasonal
                else f"{period.start:%m.%Y}")

    def _scroll(self, position: int) -> None:
        if self._ax is None or not self.periods:
            return
        end = min(len(self.periods), position + (20 if self.seasonal else 60))
        self._ax.set_xlim(position - .7, end - .3)
        step = max(1, (end - position + 11) // 12)
        ticks = list(range(position, end, step))
        self._ax.set_xticks(ticks, [self._label(self.periods[x]) for x in ticks], rotation=45, ha="right")
        self.window_label.setText(f"{self._label(self.periods[position])}–{self._label(self.periods[end - 1])}")
        self.canvas.draw_idle()

    def tooltip_at(self, event: MouseEvent) -> str | None:
        if event.xdata is None or not self.periods:
            return None
        index = round(event.xdata)
        if not 0 <= index < len(self.periods):
            return None
        period = self.periods[index]
        frost = str(period.frost_days) if period.frost_days is not None else "нет данных"
        text = (f"{self._label(period)}\nВысота: {period.max_depth:g} см\n"
                f"Даты максимума: {format_maximum_dates(period.maximum_dates)}" if self.height else
                f"{self._label(period)}\nДней со снегом: {period.snow_days}\nМорозы при малоснежье: {frost} дн.")
        return text + "\n\n" + coverage_text(period)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if (watched is self.canvas and event.type() == QEvent.Type.Wheel
                and (event.modifiers() & Qt.KeyboardModifier.ShiftModifier or event.angleDelta().x())):
            delta = event.angleDelta().x() or event.angleDelta().y()
            self.scrollbar.setValue(self.scrollbar.value() - round(delta / 120) * 3)
            return True
        return super().eventFilter(watched, event)


class SnowHeatmap(_SnowCanvas):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._data: SnowData | None = None
        self._mode = "value"
        self._cells: list[list[SnowHeatCell | None]] = []
        self._ax = None

    def set_mode(self, mode: str) -> None:
        self._mode = mode
        if self._data is not None:
            self.plot(self._data)

    def plot(self, data: SnowData) -> None:
        self._data = data
        self.figure.clear()
        ax = self._ax = self.figure.add_subplot(111)
        months = getattr(data, "heat_months", ())
        decades = getattr(data, "heat_decades", ())
        short = len(months) < 24
        if short:
            rows = sorted({(cell.start.year, cell.start.month) for cell in decades})
            lookup = {(cell.start.year, cell.start.month, (cell.start.day - 1) // 10): cell for cell in decades}
            self._cells = [[lookup.get((year, month, number)) for number in range(3)] for year, month in rows]
            labels = [f"{MONTHS_SHORT[month - 1]} {year}" for year, month in rows]
            columns = ["1-я декада", "2-я декада", "3-я декада"]
        else:
            years = sorted({cell.start.year for cell in months})
            lookup = {(cell.start.year, cell.start.month): cell for cell in months}
            self._cells = [[lookup.get((year, month)) for month in range(1, 13)] for year in years]
            labels, columns = [str(year) for year in years], list(MONTHS_SHORT)
        if not self._cells:
            self._show_message(ax, "Нормы и месячные значения пока не рассчитаны.")
            self.canvas.draw_idle()
            return
        values = [[(cell.anomaly if self._mode == "anomaly" else cell.value) if cell else None
                   for cell in row] for row in self._cells]
        masked = ma.masked_invalid([[float("nan") if value is None else value for value in row] for row in values])
        cmap = DIVERGING_CMAP if self._mode == "anomaly" else SNOW_CMAP
        present = [value for row in values for value in row if value is not None]
        norm = (CenteredNorm(vcenter=0, halfrange=max(1, max((abs(value) for value in present), default=1)))
                if self._mode == "anomaly" else Normalize(0, max(1, max(present, default=1))))
        mesh = ax.pcolormesh(masked, cmap=cmap, norm=norm, edgecolors=theme.BG, linewidth=.8)
        ax.set_xticks([i + .5 for i in range(len(columns))], columns)
        step = max(1, (len(labels) + 24) // 25)
        ax.set_yticks([i + .5 for i in range(0, len(labels), step)], labels[::step])
        ax.invert_yaxis()
        _style_axes(ax)
        for row, cells in enumerate(self._cells):
            for column, cell in enumerate(cells):
                if cell is not None and not cell.complete:
                    ax.add_patch(Rectangle((column, row), 1, 1, fill=False, hatch="////",
                                           edgecolor=theme.TEXT_SECONDARY, linewidth=0))
                value = values[row][column]
                if value is not None and len(labels) <= 25:
                    text = f"{value:+.1f}" if self._mode == "anomaly" else str(int(value))
                    ax.text(column + .5, row + .5, text, ha="center", va="center", fontsize=9,
                            color=_text_color_on(cmap(norm(value))))
        bar = self.figure.colorbar(mesh, ax=ax, fraction=.04, pad=.03)
        bar.set_label("Отклонение, дни" if self._mode == "anomaly" else "Дней со снегом")
        self.figure.tight_layout()
        self.canvas.draw_idle()

    def tooltip_at(self, event: MouseEvent) -> str | None:
        if event.xdata is None or event.ydata is None:
            return None
        row, column = int(event.ydata), int(event.xdata)
        if not 0 <= row < len(self._cells) or not 0 <= column < len(self._cells[row]):
            return None
        cell = self._cells[row][column]
        if cell is None:
            return "Нет данных"
        norm = f"{cell.norm:.1f}" if cell.norm is not None else "не рассчитана"
        anomaly = f"{cell.anomaly:+.1f} дн." if cell.anomaly is not None else "нет данных"
        return (f"{cell.start:%d.%m.%Y}–{cell.end:%d.%m.%Y}\nДней со снегом: {cell.value}\n"
                f"Норма: {norm}\nОтклонение: {anomaly}\n{cell.base_text}\n"
                + ("Полный период." if cell.complete else "Доступная часть периода.")
                + "\nПропуски высоты внутри rp5-архива приняты за 0.")
