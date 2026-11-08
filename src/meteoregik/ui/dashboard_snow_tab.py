"""Снежный дашборд: сводки, теплокарта и графики всего архива."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QGridLayout, QLabel, QSizePolicy, QStackedWidget, QVBoxLayout, QWidget

from meteoregik.export.snow_excel import HELP_FROST, HELP_SNOW, coverage_text
from meteoregik.reports.snow_data import SnowData, format_maximum_dates
from meteoregik.ui.dashboard_snow_charts import SnowComparisonChart, SnowHeatmap, SnowSeriesChart
from meteoregik.ui.dashboard_widgets import CardText, GrooveDivider, StatCard, mode_switch, scrollable, tooltip_html


class SnowTab(QWidget):
    """Фильтр и фоновую загрузку предоставляет общее окно дашборда."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._data: SnowData | None = None
        layout = QVBoxLayout(self)
        self.stack = QStackedWidget()
        self.hint = QLabel("Выберите метеостанцию и нажмите «Применить».")
        self.hint.setWordWrap(True)
        self.hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.stack.addWidget(self.hint)
        content = QWidget()
        body = QVBoxLayout(content)
        body.setSpacing(12)
        self.title_label = QLabel()
        self.title_label.setWordWrap(True)
        self.title_label.setObjectName("chartTitle")
        body.addWidget(self.title_label)
        self.cards = [StatCard() for _ in range(3)]
        cards = QGridLayout()
        for column, card in enumerate(self.cards):
            card.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
            cards.addWidget(card, 0, column)
            cards.setColumnStretch(column, 1)
        body.addLayout(cards)
        self.note = QLabel("Пропуски высоты внутри архива rp5 приняты за 0. "
                           "Дни без температуры не оценены для морозов. "
                           "Таблицы и экспорт — «Расчёт показателей» → «Снежный покров».")
        self.note.setWordWrap(True)
        self.note.setMaximumWidth(760)
        self.note.setObjectName("chartNote")
        self.note.setToolTip(tooltip_html(HELP_SNOW + "\n\n" + HELP_FROST))
        body.addWidget(self.note)
        top = QHBoxLayout()
        left = QVBoxLayout()
        header = QHBoxLayout()
        self.heatmap_title = QLabel("Отклонение от нормы, дни")
        self.heatmap_title.setObjectName("chartTitle")
        header.addWidget(self.heatmap_title)
        header.addStretch()
        switch, self.heatmap_modes = mode_switch(["Отклонение от нормы", "Число дней"], self)
        header.addWidget(switch)
        left.addLayout(header)
        self.heatmap = SnowHeatmap()
        self.heatmap.canvas.setMinimumHeight(330)
        self.norm_note = QLabel()
        self.norm_note.setWordWrap(True)
        self.norm_note.setObjectName("chartNote")
        left.addWidget(self.norm_note)
        left.addWidget(self.heatmap, 1)
        self.heatmap_modes.idClicked.connect(self._set_heatmap_mode)
        self._set_heatmap_mode(0)
        top.addLayout(left, 1)
        top.addWidget(GrooveDivider())
        right = QVBoxLayout()
        self.comparison_title = QLabel("Снежные дни относительно нормы")
        self.comparison_title.setWordWrap(True)
        self.comparison_title.setObjectName("chartTitle")
        right.addWidget(self.comparison_title)
        self.comparison_note = QLabel()
        self.comparison_note.setWordWrap(True)
        self.comparison_note.setObjectName("chartNote")
        right.addWidget(self.comparison_note)
        self.season_chart = SnowComparisonChart()
        right.addWidget(self.season_chart, 1)
        top.addLayout(right, 1)
        body.addLayout(top)
        self._section(body, "Дни со снегом и сильные морозы при малоснежье")
        self.count_chart = SnowSeriesChart()
        body.addWidget(self.count_chart)
        self._section(body, "Максимальная высота снега по месяцам")
        self.depth_chart = SnowSeriesChart(height=True)
        body.addWidget(self.depth_chart)
        body.addWidget(GrooveDivider(orientation=Qt.Orientation.Horizontal))
        maximum_header = QHBoxLayout()
        self.maximum_title = QLabel("Месячные максимумы высоты относительно среднего")
        self.maximum_title.setObjectName("chartTitle")
        maximum_header.addWidget(self.maximum_title)
        maximum_header.addStretch()
        self.maximum_month = QComboBox()
        self.maximum_month.setAccessibleName("Месяц сравнения максимальной высоты снега")
        self.maximum_month.addItems(["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
                                    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"])
        self._month_selected = False
        self.maximum_month.currentIndexChanged.connect(self._on_month_changed)
        maximum_header.addWidget(self.maximum_month)
        body.addLayout(maximum_header)
        self.maximum_note = QLabel()
        self.maximum_note.setWordWrap(True)
        self.maximum_note.setObjectName("chartNote")
        body.addWidget(self.maximum_note)
        self.maximum_chart = SnowComparisonChart(maximum=True)
        body.addWidget(self.maximum_chart)
        self.content_scroll = scrollable(content)
        self.stack.addWidget(self.content_scroll)
        layout.addWidget(self.stack)

    def _section(self, layout: QVBoxLayout, title: str) -> None:
        layout.addWidget(GrooveDivider(orientation=Qt.Orientation.Horizontal))
        label = QLabel(title)
        label.setWordWrap(True)
        label.setObjectName("chartTitle")
        layout.addWidget(label)

    def show_data(self, title: str, data: SnowData) -> None:
        if not data.has_data or data.total is None:
            self.clear_results("За выбранный период данных rp5 о снежном покрове нет.")
            return
        self._data = data
        self.title_label.setText(title)
        total = data.total
        values = (
            CardText("Дней со снежным покровом", str(total.snow_days), coverage_text(total), HELP_SNOW),
            CardText("Максимальная высота снега", f"{total.max_depth:g} см",
                     format_maximum_dates(total.maximum_dates) + "\n" + self._season_mean_text(data), HELP_SNOW),
            CardText("Сильные морозы при малоснежье, дни",
                     str(total.frost_days) if total.frost_days is not None else "нет данных",
                     coverage_text(total) + "\n" + self._frost_seasons(data, compact=True),
                     HELP_FROST + "\n\n" + self._frost_seasons(data)),
        )
        for card, value in zip(self.cards, values):
            card.set_text(value)
        self.cards[2].note.setToolTip(tooltip_html(self._frost_seasons(data)))
        self.heatmap.plot(data)
        self.count_chart.plot(data.months)
        self.depth_chart.plot(data.months)
        short = len(data.heat_months) < 24
        periods = data.months if short else data.seasons
        lookup = {cell.start: cell for cell in data.heat_months}
        normals = tuple((lookup[p.start].norm if p.start in lookup else None) if short
                        else data.season_snow_norm for p in periods)
        bases = tuple((lookup[p.start].base_text if p.start in lookup else "") if short
                      else data.season_snow_base for p in periods)
        self.comparison_title.setText("Отклонение снежных дней по месяцам" if short else
                                      "Отклонение снежных дней по зимним сезонам (01.07–30.06)")
        self._set_comparison_note(data, short, normals, bases)
        self.season_chart.plot_comparison(periods, normals, bases, seasonal=not short)
        if not self._month_selected:
            self.maximum_month.blockSignals(True)
            self.maximum_month.setCurrentIndex(data.months[-1].available_end.month - 1 if data.months else 0)
            self.maximum_month.blockSignals(False)
        self._update_maximum_chart()
        self._set_heatmap_mode(self.heatmap_modes.checkedId())
        self.stack.setCurrentWidget(self.content_scroll)

    def clear_results(self, message: str) -> None:
        self._data = None
        self.hint.setText(message)
        self.stack.setCurrentWidget(self.hint)
        for chart in (self.count_chart, self.depth_chart, self.season_chart, self.maximum_chart):
            chart.plot(())
        self.heatmap.plot(SnowData(None))

    @staticmethod
    def _frost_seasons(data: SnowData, *, compact: bool = False) -> str:
        lines = [f"{p.start.year}/{p.end.year % 100:02d}: {p.frost_days} дн."
                 + (" (доступная часть)" if p.partial else "")
                 + (" (есть дни без температуры)" if p.temperature_days < p.available_days else "")
                 for p in data.seasons if p.frost_days is not None and p.frost_days > 0]
        if not lines:
            return ("Нет дней с температурой для оценки морозов по сезонам."
                    if not any(p.temperature_days for p in data.seasons) else
                    "В оценённых днях сезонов сильных морозов при малоснежье нет.")
        if compact and len(lines) > 5:
            return "\n".join(lines[:5]) + f"\nЕщё сезонов: {len(lines) - 5} — полный список в подсказке."
        return "\n".join(lines)

    def _set_comparison_note(self, data: SnowData, short: bool,
                             normals: tuple[float | None, ...], bases: tuple[str, ...]) -> None:
        available = [value for value in normals if value is not None]
        if short:
            values = sorted(set(available))
            value_text = (f"{values[0]:.1f} дн." if len(values) == 1 else
                          f"{values[0]:.1f}–{values[-1]:.1f} дн." if values else "не рассчитаны")
            text = (f"Нормы месяцев: {value_text}. Средняя по годам доля снежных дней "
                    "умножается на расчётную длину месяца. База каждого месяца — в подсказке.")
        else:
            value_text = f"{data.season_snow_norm:.1f} дн." if data.season_snow_norm is not None else "не рассчитана"
            unique = list(dict.fromkeys(part.split(": ", 1)[-1] for part in data.season_snow_base.split("; ") if part))
            base = unique[0] if len(unique) == 1 else "База различается по месяцам; подробности — в подсказке."
            text = f"Норма сезона: {value_text} — сумма норм 12 месяцев. {base}"
        self.comparison_note.setText(text + " Разница с нормой — в днях. Ромб — неполный период или нет нормы.")
        self.comparison_note.setToolTip(tooltip_html("\n".join(dict.fromkeys(bases))))

    @staticmethod
    def _season_mean_text(data: SnowData) -> str:
        mean = data.maximum_season_mean
        return (f"Средний максимум за зимний сезон: {mean.value:.1f} см\n{mean.base_text}"
                if mean is not None else "Средний максимум за зимний сезон: нет полных сезонов")

    def _set_heatmap_mode(self, index: int) -> None:
        anomaly = index == 0
        self.heatmap.set_mode("anomaly" if anomaly else "value")
        self.heatmap_title.setText("Отклонение от нормы, дни" if anomaly else "Дни со снежным покровом")
        unit = "декаду" if self._data is None or len(self._data.heat_months) < 24 else "месяц"
        explanation = ("Разница с нормой: красный — больше нормы, синий — меньше. "
                       if anomaly else f"Чем темнее клетка, тем больше снежных дней за {unit}. ")
        self.norm_note.setText(explanation + "Штриховка — доступная часть периода; пустая клетка — нет данных или нормы. База — в подсказке.")

    def _on_month_changed(self, index: int) -> None:
        self._month_selected = True
        self._update_maximum_chart()

    def _update_maximum_chart(self) -> None:
        if self._data is None:
            return
        month = self.maximum_month.currentIndex() + 1
        periods = tuple(p for p in self._data.months if p.start.month == month)
        mean = self._data.maximum_month_means[month - 1]
        normal = mean.value if mean is not None else None
        base = mean.base_text if mean is not None else "Нет полных месяцев для расчёта среднего."
        self.maximum_note.setText((f"{self.maximum_month.currentText()}: средний максимум {normal:.1f} см; {base}. "
                                  if normal is not None else base + " ") + "Штриховка — неполный месяц.")
        self.maximum_chart.plot_comparison(periods, (normal,) * len(periods), (base,) * len(periods))

    def set_loading(self, loading: bool) -> None:
        self.setEnabled(not loading)
