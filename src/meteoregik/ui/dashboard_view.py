"""Экран "Дашборд", этап 1 (решение пользователя 2026-10-01, plan.md, "Экраны
UI", п.4): одна станция + период -> сверху карточки-сводки, ниже теплокарта
"год × месяц" и аномалии среднегодовой температуры с трендом. Только по уже
посчитанной температуре; агрометеорологические показатели появятся позже
отдельной вкладкой (после их расчёта в разделе "Расчёт показателей").

Фильтры и их поведение — как в "Просмотре агрегатных данных"
(ui/query_panel.py): станция обязательна и не запоминается между
открытиями, пустой период = весь архив станции, результат строится только
по "Применить", загрузка в фоне с лоадером поверх результата. Все расчёты —
в reports/dashboard_data.py (без Qt), экран только раскладывает готовое.

Карточки — текстом с явной формулировкой ("на 0.6 °C выше нормы"), не
только знаком числа или цветом; значения — цветом обычного текста (dataviz:
text wears text tokens), цвет "тепло/холодно" есть только на графиках.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Callable

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy.engine import Engine

from meteoregik.reports.dashboard_data import (
    MONTHS_GENITIVE,
    MONTHS_NOMINATIVE,
    MIN_TREND_YEARS,
    DashboardData,
    MonthRecord,
    YearAnomaly,
    build_dashboard,
    describe_anomaly,
    format_temperature,
    station_day_bounds,
)
from meteoregik.resources import DASHBOARD_ICON_PATH
from meteoregik.stations.choices import load_stations_with_computed_indicators
from meteoregik.ui import theme
from meteoregik.ui.background import run_in_background
from meteoregik.ui.buttons import reserve_focus_width
from meteoregik.ui.dashboard_charts import AnomalyChart, TemperatureHeatmap
from meteoregik.ui.date_field import DateField, DateFieldError
from meteoregik.ui.dialogs import enable_min_max_buttons, minimize_owner_instead
from meteoregik.ui.loading_overlay import LoadingOverlay
from meteoregik.ui.station_combo import StationCombo

_TITLE = "Дашборд"
_NO_VALUE = "—"

DASHBOARD_QSS = f"""
QFrame#statCard {{
    background: {theme.INPUT_BG};
    border: 1px solid {theme.BORDER};
    border-radius: 8px;
}}
QLabel#statCaption {{ color: {theme.TEXT_SECONDARY}; font-size: 12px; }}
QLabel#statValue {{ color: {theme.TEXT_PRIMARY}; font-size: 20px; font-weight: 600; }}
QLabel#statNote {{ color: {theme.TEXT_SECONDARY}; font-size: 12px; }}
QLabel#chartTitle {{ color: {theme.TEXT_PRIMARY}; font-size: 14px; font-weight: 600; }}
QLabel#chartNote {{ color: {theme.TEXT_SECONDARY}; font-size: 12px; }}
QLabel#emptyHint {{ color: {theme.TEXT_SECONDARY}; font-size: 14px; }}
"""


def _format_day(day: date) -> str:
    return f"{day:%d.%m.%Y}"


def _format_count(value: int) -> str:
    """Тысячи через неразрывный пробел — "3 652", как принято в русском тексте."""
    return f"{value:,}".replace(",", " ")


@dataclass(frozen=True)
class CardText:
    caption: str
    value: str
    note: str


def card_texts(data: DashboardData) -> list[CardText]:
    """Тексты восьми карточек по порядку: средняя, максимум, минимум,
    полнота; тёплый/холодный год, тёплый/холодный месяц. Отдельно от
    виджетов — проверяется тестами без раскладки."""
    if data.period_mean is None:
        mean = CardText("Средняя температура", _NO_VALUE, "нет суточных значений в периоде")
    elif data.period_anomaly is None:
        mean = CardText("Средняя температура", format_temperature(data.period_mean),
                        "норма не рассчитана: нет полных месяцев")
    else:
        mean = CardText("Средняя температура", format_temperature(data.period_mean),
                        describe_anomaly(data.period_anomaly))

    def extreme(caption: str, record) -> CardText:
        if record is None:
            return CardText(caption, _NO_VALUE, "нет данных в периоде")
        return CardText(caption, format_temperature(record.value), _format_day(record.day))

    missing = data.days_total - data.days_with_data
    share = data.days_with_data / data.days_total * 100 if data.days_total else 0.0
    completeness = CardText(
        "Полнота данных",
        f"{share:.1f} %",
        f"{_format_count(data.days_with_data)} из {_format_count(data.days_total)} дн."
        + (f", пропущено {_format_count(missing)}" if missing else ", без пропусков"),
    )

    def year(caption: str, record: YearAnomaly | None) -> CardText:
        if record is None:
            return CardText(caption, _NO_VALUE, "нет полных лет в периоде")
        return CardText(caption, str(record.year),
                        f"{format_temperature(record.value)}, {describe_anomaly(record.anomaly)}")

    def month(caption: str, record: MonthRecord | None) -> CardText:
        if record is None:
            return CardText(caption, _NO_VALUE, "нет полных месяцев в периоде")
        note = format_temperature(record.value)
        if record.anomaly is not None:
            note += ", " + describe_anomaly(record.anomaly, f"нормы {MONTHS_GENITIVE[record.month - 1]}")
        return CardText(caption, f"{MONTHS_NOMINATIVE[record.month - 1].capitalize()} {record.year}", note)

    return [
        mean,
        extreme("Абсолютный максимум", data.abs_max),
        extreme("Абсолютный минимум", data.abs_min),
        completeness,
        year("Самый тёплый год", data.warmest_year),
        year("Самый холодный год", data.coldest_year),
        month("Самый тёплый месяц", data.warmest_month),
        month("Самый холодный месяц", data.coldest_month),
    ]


def anomaly_note(data: DashboardData) -> str:
    """Пояснение под заголовком графика аномалий — от чего отсчитано."""
    norm = data.annual_norm
    if norm is None:
        return "Норма не рассчитана: у станции нет ни одного полного года."
    years = f"{norm.first_year}–{norm.last_year}" if norm.first_year != norm.last_year else str(norm.first_year)
    text = (
        f"Норма {format_temperature(norm.value)} — средняя за {norm.year_count} полн. лет ({years}) "
        "всего архива станции. Показаны только полные годы."
    )
    if data.year_anomalies and data.trend is None:
        text += f" Линия тренда — от {MIN_TREND_YEARS} полных лет в периоде."
    return text


class _StatCard(QFrame):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("statCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(2)
        self.caption = QLabel()
        self.caption.setObjectName("statCaption")
        self.value = QLabel()
        self.value.setObjectName("statValue")
        self.note = QLabel()
        self.note.setObjectName("statNote")
        self.note.setWordWrap(True)
        for label in (self.caption, self.value, self.note):
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            layout.addWidget(label)
        layout.addStretch(1)

    def set_text(self, text: CardText) -> None:
        self.caption.setText(text.caption)
        self.value.setText(text.value)
        self.note.setText(text.note)


@dataclass(frozen=True)
class _AppliedFilter:
    wmo_index: int
    date_from: date
    date_to: date
    station_title: str


class DashboardDialog(QDialog):
    def __init__(self, engine: Engine, parent=None, run_async: bool = True) -> None:
        super().__init__(parent)
        enable_min_max_buttons(self)
        self.engine = engine
        # run_async=False — расчёт в том же потоке (тесты).
        self._run_async = run_async
        self._request_id = 0
        self.setWindowTitle(_TITLE)
        self.setWindowIcon(QIcon(str(DASHBOARD_ICON_PATH)))
        if parent is not None:
            self.resize(parent.size())
        else:
            self.resize(1100, 750)
        self.setStyleSheet(theme.DIALOG_QSS + DASHBOARD_QSS)
        self._build_ui()
        self.station_combo.set_choices(load_stations_with_computed_indicators(self.engine))

    # --- построение интерфейса ---

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)
        layout.addWidget(self._build_filter_card())

        self._station_label = QLabel("")
        self._station_label.setStyleSheet("font-weight: 600; font-size: 14px;")
        layout.addWidget(self._station_label)

        # До первого "Применить" — подсказка, что делать, а не пустые карточки.
        self._stack = QStackedWidget()
        hint = QLabel(
            "Выберите метеостанцию и нажмите «Применить».\n"
            "Если период не указан, берётся весь архив станции."
        )
        hint.setObjectName("emptyHint")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._stack.addWidget(hint)
        self._content = self._build_content()
        self._stack.addWidget(self._content)

        results_area = QWidget()
        results_layout = QGridLayout(results_area)
        results_layout.setContentsMargins(0, 0, 0, 0)
        results_layout.addWidget(self._stack, 0, 0)
        self.loading_overlay = LoadingOverlay("Считаем показатели…")
        results_layout.addWidget(self.loading_overlay, 0, 0)
        layout.addWidget(results_area, stretch=1)

        close_row = QHBoxLayout()
        close_row.addStretch(1)
        close_btn = QPushButton("Закрыть")
        close_btn.setObjectName("secondary")
        close_btn.clicked.connect(self.accept)
        close_row.addWidget(close_btn)
        layout.addLayout(close_row)
        reserve_focus_width(*self.findChildren(QPushButton))

    def _build_filter_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("card")
        row = QHBoxLayout(card)
        row.setContentsMargins(16, 12, 16, 12)
        row.setSpacing(10)

        row.addWidget(QLabel("Метеостанция:"))
        self.station_combo = StationCombo()
        row.addWidget(self.station_combo, stretch=1)

        period = QFrame()
        period.setObjectName("group")
        period_layout = QHBoxLayout(period)
        period_layout.setContentsMargins(12, 6, 12, 6)
        period_layout.setSpacing(8)
        period_title = QLabel("Период:")
        period_title.setStyleSheet("font-weight: 600;")
        period_layout.addWidget(period_title)
        period_layout.addWidget(QLabel("с"))
        self.date_from_edit = DateField()
        period_layout.addWidget(self.date_from_edit)
        period_layout.addWidget(QLabel("по"))
        self.date_to_edit = DateField()
        period_layout.addWidget(self.date_to_edit)
        row.addWidget(period)

        self.apply_btn = QPushButton("Применить")
        self.apply_btn.setObjectName("primary")
        self.apply_btn.clicked.connect(self._apply_filters)
        row.addWidget(self.apply_btn)
        return card

    def _build_content(self) -> QWidget:
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        cards = QGridLayout()
        cards.setHorizontalSpacing(10)
        cards.setVerticalSpacing(10)
        self.cards = [_StatCard() for _ in range(8)]
        for i, card in enumerate(self.cards):
            cards.addWidget(card, i // 4, i % 4)
        for col in range(4):
            cards.setColumnStretch(col, 1)
        layout.addLayout(cards)

        charts = QHBoxLayout()
        charts.setSpacing(16)
        self.heatmap = TemperatureHeatmap()
        charts.addLayout(
            self._chart_block(
                "Средняя температура по месяцам",
                "Штриховка — неполный месяц (пропущено больше 5 дней или больше 3 подряд), "
                "пустая клетка — нет данных.",
                self.heatmap,
            ),
            stretch=1,
        )
        self.anomaly_chart = AnomalyChart()
        self._anomaly_note = QLabel()
        charts.addLayout(
            self._chart_block("Отклонение среднегодовой температуры от нормы", self._anomaly_note,
                              self.anomaly_chart),
            stretch=1,
        )
        layout.addLayout(charts, stretch=1)
        return content

    @staticmethod
    def _chart_block(title: str, note: str | QLabel, chart: QWidget) -> QVBoxLayout:
        block = QVBoxLayout()
        block.setSpacing(2)
        title_label = QLabel(title)
        title_label.setObjectName("chartTitle")
        block.addWidget(title_label)
        note_label = note if isinstance(note, QLabel) else QLabel(note)
        note_label.setObjectName("chartNote")
        note_label.setWordWrap(True)
        block.addWidget(note_label)
        block.addWidget(chart, stretch=1)
        return block

    # --- применение фильтров ---

    def _apply_filters(self) -> None:
        try:
            wmo_index = self.station_combo.selected_wmo()
        except ValueError:
            QMessageBox.warning(self, "Фильтр", "Выберите метеостанцию из списка.")
            return

        dates: list[date | None] = []
        for label, field in (("с", self.date_from_edit), ("по", self.date_to_edit)):
            try:
                dates.append(field.value())
            except DateFieldError as exc:
                QMessageBox.warning(self, "Фильтр", f"Период, поле «{label}»: {exc}")
                return
        date_from, date_to = dates
        if (date_from is None) != (date_to is None):
            QMessageBox.warning(
                self, "Фильтр",
                "Период задаётся двумя датами: заполните обе или очистите обе "
                "(тогда берётся весь архив станции).",
            )
            return
        if date_from is not None and date_to is not None and date_from > date_to:
            QMessageBox.warning(self, "Фильтр", "Начало периода позже его конца.")
            return
        if date_from is None or date_to is None:
            bounds = station_day_bounds(self.engine, wmo_index)
            if bounds is None:
                QMessageBox.information(self, _TITLE, "По этой станции ещё нет посчитанных данных.")
                return
            date_from, date_to = bounds

        self._load(_AppliedFilter(wmo_index, date_from, date_to, self.station_combo.currentText()))

    def _load(self, applied: _AppliedFilter) -> None:
        self._request_id += 1
        request_id = self._request_id
        self._set_loading(True)

        def task() -> DashboardData:
            return build_dashboard(self.engine, applied.wmo_index, applied.date_from, applied.date_to)

        def on_done(data: DashboardData) -> None:
            if request_id != self._request_id:
                return
            self._set_loading(False)
            self._show(applied, data)

        def on_error(exc: BaseException) -> None:
            if request_id != self._request_id:
                return
            self._set_loading(False)
            QMessageBox.warning(self, _TITLE, f"Не удалось загрузить данные: {exc}")

        self._run(task, on_done, on_error)

    def _run(
        self,
        task: Callable[[], DashboardData],
        on_done: Callable[[DashboardData], None],
        on_error: Callable[[BaseException], None],
    ) -> None:
        if self._run_async:
            run_in_background(self, task, on_done, on_error)
            return
        try:
            result = task()
        except Exception as exc:  # noqa: BLE001 — тот же путь, что и у фонового режима
            on_error(exc)
        else:
            on_done(result)

    def _set_loading(self, loading: bool) -> None:
        self.loading_overlay.setVisible(loading)
        self.apply_btn.setEnabled(not loading)
        if loading:
            self.loading_overlay.raise_()

    def _show(self, applied: _AppliedFilter, data: DashboardData) -> None:
        if not data.has_data:
            QMessageBox.information(self, _TITLE, "За выбранный период данных не найдено.")
            return
        self._station_label.setText(
            f"{applied.station_title} — {_format_day(applied.date_from)}–{_format_day(applied.date_to)}"
        )
        for card, text in zip(self.cards, card_texts(data)):
            card.set_text(text)
        self._anomaly_note.setText(anomaly_note(data))
        self._stack.setCurrentWidget(self._content)
        self.heatmap.plot(data)
        self.anomaly_chart.plot(data)

    # --- свернуть окно ---

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        minimize_owner_instead(self, event)
