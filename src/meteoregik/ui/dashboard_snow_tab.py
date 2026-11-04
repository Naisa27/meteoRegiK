"""Снежный дашборд: сводки, теплокарта и графики всего архива."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGridLayout, QLabel, QSizePolicy, QStackedWidget, QVBoxLayout, QWidget

from meteoregik.export.snow_excel import HELP_FROST, HELP_SNOW, coverage_text
from meteoregik.reports.snow_data import SnowData, format_maximum_dates
from meteoregik.ui.dashboard_snow_charts import SnowHeatmap, SnowSeriesChart
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
        self._section(body, "Дни со снежным покровом по месяцам и декадам")
        switch, self.heatmap_modes = mode_switch(["Число дней", "Отклонение от нормы"], self)
        body.addWidget(switch, alignment=Qt.AlignmentFlag.AlignLeft)
        self.heatmap_modes.idClicked.connect(lambda index: self.heatmap.set_mode("value" if index == 0 else "anomaly"))
        self.heatmap = SnowHeatmap()
        self.heatmap.canvas.setMinimumHeight(330)
        body.addWidget(self.heatmap)
        self.norm_note = QLabel("Нормы по архиву rp5: пропуски высоты приняты за отсутствие снега. "
                                "Штриховка — доступная часть периода. База нормы указана в подсказке каждой клетки.")
        self.norm_note.setWordWrap(True)
        self.norm_note.setObjectName("chartNote")
        body.addWidget(self.norm_note)
        self._section(body, "Дни со снегом и сильные морозы при малоснежье")
        self.count_chart = SnowSeriesChart()
        body.addWidget(self.count_chart)
        self._section(body, "Максимальная высота снега по месяцам")
        self.depth_chart = SnowSeriesChart(height=True)
        body.addWidget(self.depth_chart)
        self._section(body, "Дни со снегом по зимним сезонам (01.07–30.06)")
        self.season_chart = SnowSeriesChart(seasonal=True)
        body.addWidget(self.season_chart)
        self.chart_note = QLabel("Горизонтальная полоса под графиком открывает весь архив. "
                                 "Колесо прокручивает страницу, Shift + колесо — график по времени.")
        self.chart_note.setWordWrap(True)
        self.chart_note.setObjectName("chartNote")
        body.addWidget(self.chart_note)
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
                     format_maximum_dates(total.maximum_dates), HELP_SNOW),
            CardText("Сильные морозы при малоснежье, дни",
                     str(total.frost_days) if total.frost_days is not None else "нет данных",
                     coverage_text(total), HELP_FROST),
        )
        for card, value in zip(self.cards, values):
            card.set_text(value)
        self.heatmap.plot(data)
        self.count_chart.plot(data.months)
        self.depth_chart.plot(data.months)
        self.season_chart.plot(data.seasons)
        self.stack.setCurrentWidget(self.content_scroll)

    def clear_results(self, message: str) -> None:
        self._data = None
        self.hint.setText(message)
        self.stack.setCurrentWidget(self.hint)
        for chart in (self.count_chart, self.depth_chart, self.season_chart):
            chart.plot(())
        self.heatmap.plot(SnowData(None))

    def set_loading(self, loading: bool) -> None:
        self.setEnabled(not loading)
