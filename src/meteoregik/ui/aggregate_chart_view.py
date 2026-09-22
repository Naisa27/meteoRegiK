"""Первая интеграция matplotlib/FigureCanvasQTAgg в проекте — режим
"графики" экрана "Просмотр агрегатных данных". Все выбранные показатели
рисуются на одном графике с общей легендой (решение пользователя).

Цвета серий — фиксированные по показателю (не по позиции в текущей
выборке), первые 3 слота валидированной категориальной палитры (skill
dataviz, references/palette.md — эти три прошли all-pairs проверку CVD/
контраста как набор, что и нужно для ровно 3 сегодняшних показателей: t
средняя/Мин/макс): показатель всегда одного цвета независимо от того, какие
ещё показатели включены галочками — иначе "перекрашивание" при смене
фильтра сбивало бы читателя (dataviz: "color follows the entity, never its
rank")."""

from __future__ import annotations

import mplcursors
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PySide6.QtWidgets import QVBoxLayout, QWidget

from meteoregik.reports.chart_data import ChartData
from meteoregik.reports.parameters import DEFAULT_PARAMETERS, ParamKey, label_for
from meteoregik.ui import theme

# Первые 3 слота категориальной палитры dataviz (validated all-pairs для
# ровно 3 серий) — цвет закреплён за позицией показателя в реестре
# PARAMETER_LABELS, не за позицией в текущей выборке parameters.
_SERIES_COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]

# Не показывать больше этого числа подписей по оси X — иначе на "Подробно"
# за несколько лет (сотни дней) подписи слипаются в нечитаемое пятно.
_MAX_X_TICKS = 20


def _color_for(key: ParamKey) -> str:
    try:
        index = DEFAULT_PARAMETERS.index(key)
    except ValueError:
        index = 0
    return _SERIES_COLORS[index % len(_SERIES_COLORS)]


class AggregateChartView(QWidget):
    """Один Figure/один Axes на весь график — все кривые вместе."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._figure = Figure(facecolor=theme.BG)
        self._canvas = FigureCanvasQTAgg(self._figure)
        self._ax = self._figure.add_subplot(111)
        self._cursor: mplcursors.Cursor | None = None
        self._x_labels: list[str] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._canvas)

    def plot(self, chart_data: ChartData, parameters: list[ParamKey]) -> None:
        self._ax.clear()
        if self._cursor is not None:
            self._cursor.remove()
            self._cursor = None
        self._x_labels = chart_data.x_labels

        lines = []
        for key in parameters:
            values = chart_data.series.get(key, [])
            line, = self._ax.plot(
                chart_data.x_labels, values, color=_color_for(key), linewidth=2, label=label_for(key)
            )
            lines.append(line)

        self._ax.set_facecolor(theme.BG)
        self._ax.grid(True, color=theme.BORDER, linewidth=0.8, alpha=0.8)
        self._ax.tick_params(colors=theme.TEXT_SECONDARY, labelrotation=45)
        for spine in self._ax.spines.values():
            spine.set_color(theme.BORDER)

        self._thin_x_ticks(len(chart_data.x_labels))

        if len(parameters) >= 2:
            self._ax.legend(facecolor=theme.BG, edgecolor=theme.BORDER, labelcolor=theme.TEXT_PRIMARY)

        self._figure.tight_layout()
        if lines:
            self._cursor = mplcursors.cursor(lines, hover=True)
            self._cursor.connect("add", self._annotate)
        self._canvas.draw_idle()

    def _thin_x_ticks(self, point_count: int) -> None:
        if point_count <= _MAX_X_TICKS:
            return
        step = -(-point_count // _MAX_X_TICKS)  # округление вверх
        self._ax.set_xticks(list(range(0, point_count, step)))

    def _annotate(self, sel: object) -> None:
        # mplcursors.Selection — типизируем как object, чтобы не тянуть
        # приватные типы mplcursors в аннотации; sel.target — (x, y) в
        # координатах данных (x — позиция точки, т.к. ось X категориальная).
        x_pos, y = sel.target  # type: ignore[attr-defined]
        label = sel.artist.get_label()  # type: ignore[attr-defined]
        index = round(x_pos)
        x_label = self._x_labels[index] if 0 <= index < len(self._x_labels) else str(x_pos)
        sel.annotation.set_text(f"{label}\n{x_label}: {y:.2f}")  # type: ignore[attr-defined]
