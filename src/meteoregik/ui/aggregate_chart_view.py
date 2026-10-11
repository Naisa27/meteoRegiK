"""Первая интеграция matplotlib/FigureCanvasQTAgg в проекте — режим
"графики" экрана "Просмотр агрегатных данных". Все выбранные показатели
рисуются на одном графике с общей легендой (решение пользователя).

Цвета серий — фиксированные по показателю (не по позиции в текущей
выборке), слоты валидированной категориальной палитры (skill dataviz,
references/palette.md): показатель всегда одного цвета независимо от того,
какие ещё показатели включены галочками — иначе "перекрашивание" при смене
фильтра сбивало бы читателя (dataviz: "color follows the entity, never its
rank").

Осадки (корректировка 2026-10-11) — столбцами, а не линией: сумма за период —
количество, между соседними суммами нет промежуточных значений. Вместе с
температурой — на своей оси справа: миллиметры и градусы на одной шкале
сплющили бы одну из величин."""

from __future__ import annotations

import mplcursors
from matplotlib.axes import Axes
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PySide6.QtWidgets import QVBoxLayout, QWidget

from meteoregik.reports.chart_data import ChartData
from meteoregik.reports.parameters import (
    DEFAULT_PARAMETERS,
    ParamKey,
    decimals_for,
    is_bar,
    label_for,
    quantity_title,
)
from meteoregik.ui import theme

# Слоты категориальной палитры dataviz — цвет закреплён за позицией
# показателя в реестре PARAMETER_LABELS, не за позицией в текущей выборке.
_SERIES_COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]

# Не показывать больше этого числа подписей по оси X — иначе на "Подробно"
# за несколько лет (сотни дней) подписи слипаются в нечитаемое пятно.
_MAX_X_TICKS = 20
# Столбцы осадков полупрозрачные: за ними должны читаться кривые температуры
_BAR_ALPHA = 0.55
_BAR_WIDTH = 0.8


def _color_for(key: ParamKey) -> str:
    try:
        index = DEFAULT_PARAMETERS.index(key)
    except ValueError:
        index = 0
    return _SERIES_COLORS[index % len(_SERIES_COLORS)]


class AggregateChartView(QWidget):
    """Один Figure на весь график: кривые на основной оси, столбцы — на
    основной же, если кривых нет, иначе на второй оси справа."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._figure = Figure(facecolor=theme.BG)
        self._canvas = FigureCanvasQTAgg(self._figure)
        self._ax = self._figure.add_subplot(111)
        self._bar_ax: Axes | None = None
        self._cursor: mplcursors.Cursor | None = None
        self._x_labels: list[str] = []
        self._decimals: dict[str, int] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._canvas)

    def plot(self, chart_data: ChartData, parameters: list[ParamKey]) -> None:
        if self._cursor is not None:
            self._cursor.remove()
            self._cursor = None
        # Вторая ось создаётся заново на каждый рисунок: clear() её не убирает
        self._figure.clear()
        self._ax = self._figure.add_subplot(111)
        self._bar_ax = None
        self._x_labels = chart_data.x_labels
        self._decimals = {label_for(key): decimals_for(key) for key in parameters}

        line_keys = [key for key in parameters if not is_bar(key)]
        bar_keys = [key for key in parameters if is_bar(key)]
        positions = list(range(len(chart_data.x_labels)))

        artists = []
        for key in line_keys:
            values = chart_data.series.get(key, [])
            line, = self._ax.plot(positions, values, color=_color_for(key), linewidth=2, label=label_for(key))
            artists.append(line)

        bar_ax = self._ax
        if bar_keys and line_keys:
            bar_ax = self._bar_ax = self._ax.twinx()
        for key in bar_keys:
            values = [value if value is not None else 0.0 for value in chart_data.series.get(key, [])]
            bars = bar_ax.bar(
                positions, values, width=_BAR_WIDTH, color=_color_for(key), alpha=_BAR_ALPHA, label=label_for(key)
            )
            artists.append(bars)

        self._style_axis(self._ax, quantity_title(line_keys or bar_keys))
        if self._bar_ax is not None:
            self._style_axis(self._bar_ax, quantity_title(bar_keys), grid=False)
            # Кривые — поверх столбцов: ось-близнец иначе рисуется над основной
            self._ax.set_zorder(self._bar_ax.get_zorder() + 1)
            self._ax.patch.set_visible(False)

        self._set_x_ticks(chart_data.x_labels)

        if len(parameters) >= 2:
            # Над графиком, в одну строку: внутри области построения легенда
            # закрывала бы столбцы осадков, а они занимают её нижнюю часть целиком
            self._ax.legend(
                artists, [artist.get_label() for artist in artists],
                loc="lower center", bbox_to_anchor=(0.5, 1.0), ncol=len(artists), frameon=False,
                labelcolor=theme.TEXT_PRIMARY,
            )

        self._figure.tight_layout()
        if artists:
            self._cursor = mplcursors.cursor(artists, hover=True)
            self._cursor.connect("add", self._annotate)
        self._canvas.draw_idle()

    def _style_axis(self, ax: Axes, title: str, grid: bool = True) -> None:
        ax.set_facecolor(theme.BG)
        if grid:
            ax.grid(True, color=theme.BORDER, linewidth=0.8, alpha=0.8)
        ax.tick_params(colors=theme.TEXT_SECONDARY)
        ax.set_ylabel(title, color=theme.TEXT_SECONDARY)
        for spine in ax.spines.values():
            spine.set_color(theme.BORDER)

    def _set_x_ticks(self, x_labels: list[str]) -> None:
        """Подписи периода под углом; на длинном ряду — не каждая."""
        step = max(1, -(-len(x_labels) // _MAX_X_TICKS))  # округление вверх
        ticks = list(range(0, len(x_labels), step))
        self._ax.set_xticks(ticks)
        self._ax.set_xticklabels([x_labels[i] for i in ticks], rotation=45, ha="right")

    def _annotate(self, sel: object) -> None:
        # mplcursors.Selection — типизируем как object, чтобы не тянуть
        # приватные типы mplcursors в аннотации; sel.target — (x, y) в
        # координатах данных (x — номер точки на оси периода).
        x_pos, y = sel.target  # type: ignore[attr-defined]
        label = sel.artist.get_label()  # type: ignore[attr-defined]
        index = round(x_pos)
        x_label = self._x_labels[index] if 0 <= index < len(self._x_labels) else str(x_pos)
        decimals = self._decimals.get(label, 2)
        sel.annotation.set_text(f"{label}\n{x_label}: {y:.{decimals}f}")  # type: ignore[attr-defined]
