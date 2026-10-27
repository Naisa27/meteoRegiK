"""Экран "Карта" (2026-10-10): станции на карте в трёх режимах —
"Станции" (что залито: rp5 / ГГ / оба), "Показатель" (показатель
температуры или осадков цветом и числом — за период или по шагам времени с
ползунком) и "Полнота" (доля дней с данными цветом, длина архива размером
кружка). У кружка во всех режимах подписано название станции, значение —
второй строкой под ним.
Щелчок по станции — карточка с периодами и пропусками и переход в дашборд.

Карта — страница static/map/map.html (Leaflet) в QWebEngineView. Не folium,
как планировалось: folium подключает Leaflet из интернета и на каждое
изменение пересобирает всю страницу, а карта должна открываться без
интернета и перерисовывать маркеры на каждый шаг ползунка. Поэтому Leaflet и
границы лежат в поставке, страница грузится один раз, дальше в неё
передаются только данные (MapCanvas.call -> mapApi.* в map.html).

Подложка — гибрид (решение пользователя): тайлы OpenStreetMap, когда есть
интернет, иначе встроенная контурная карта; переключается сама страница.

Что рисовать, считают reports/map_data.py и reports/map_render.py (без Qt);
экран только собирает фильтры и передаёт готовый слой на страницу. Тесты
подставляют вместо браузера RecordingCanvas.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date
from typing import Any

from PySide6.QtCore import QEvent, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy.engine import Engine

from meteoregik.calculations.anomalies import PRECIPITATION
from meteoregik.reports.map_data import (
    INDICATORS,
    MEAN,
    PRECIP_PERCENT,
    PRECIP_SUM,
    STEP_MONTH,
    STEP_YEAR,
    MapData,
    StationValue,
    TimeStep,
    fetch_map_data,
    period_values,
    time_series,
)
from meteoregik.reports.map_render import (
    SCOPE_ARCHIVE,
    SCOPE_MONTH,
    SCOPE_PERIOD,
    SCOPE_YEAR,
    MapLayer,
    background_points,
    fill_layer,
    indicator_layer,
    indicator_scale,
    stations_layer,
)
from meteoregik.resources import MAP_PAGE_PATH
from meteoregik.ui import theme
from meteoregik.ui.background import run_in_background
from meteoregik.ui.buttons import reserve_focus_width
from meteoregik.ui.dashboard_view import DASHBOARD_QSS, TAB_PRECIPITATION, TAB_TEMPERATURE, DashboardDialog
from meteoregik.ui.dashboard_widgets import mode_switch
from meteoregik.ui.date_field import DateField, DateFieldError
from meteoregik.ui.dialogs import enable_min_max_buttons, exec_window_modal, minimize_owner_instead
from meteoregik.ui.loading_overlay import LoadingOverlay

_TITLE = "Карта"
MODE_STATIONS, MODE_INDICATOR, MODE_FILL = 0, 1, 2
TIME_PERIOD, TIME_YEARS, TIME_MONTHS = "period", STEP_YEAR, STEP_MONTH
_TIME_LABELS = {TIME_PERIOD: "за период", TIME_YEARS: "по годам", TIME_MONTHS: "по месяцам"}
BASE_OSM, BASE_OUTLINE = "osm", "outline"
# Пауза между шагами при проигрывании ползунка времени
PLAY_INTERVAL_MS = 700
WHOLE_ARCHIVE = "за весь архив каждой станции"
# Сумма осадков и процент нормы без периода — не за весь архив (архивы
# разной длины несравнимы), а в среднем за год: reports/map_data.py
WHOLE_ARCHIVE_PER_YEAR = "в среднем за год — по полным годам архива каждой станции"
_USER_AGENT_SUFFIX = " meteoRegiK"

MAP_QSS = f"""
QLabel#mapStatus {{ color: {theme.TEXT_SECONDARY}; font-size: 12px; }}
QLabel#mapStep {{ color: {theme.TEXT_PRIMARY}; font-size: 14px; font-weight: 600; }}
QCheckBox {{ color: {theme.TEXT_PRIMARY}; background: transparent; spacing: 6px; }}
QSlider::groove:horizontal {{ height: 6px; border-radius: 3px; background: {theme.HEADER_BORDER}; }}
QSlider::sub-page:horizontal {{ border-radius: 3px; background: {theme.ACCENT_SKY}; }}
QSlider::handle:horizontal {{
    width: 16px; height: 16px; margin: -6px 0; border-radius: 8px;
    background: {theme.INPUT_BG}; border: 2px solid {theme.ACCENT_SKY};
}}
QSlider::handle:horizontal:focus {{ border: {theme.FOCUS_RING_WIDTH}px solid {theme.FOCUS_RING}; }}
"""


class RecordingCanvas(QWidget):
    """Замена браузера в тестах: запоминает вызовы страницы вместо отрисовки."""

    dashboard_requested = Signal(int)
    base_changed = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def call(self, function: str, *args: Any) -> None:
        self.calls.append((function, args))

    def last(self, function: str) -> tuple[Any, ...]:
        return next(args for name, args in reversed(self.calls) if name == function)


class _MapPage(QWebEnginePage):
    """Ссылки со страницы карты наружу не открываются внутри карты:
    app://dashboard/<индекс> — команда приложению, http(s) (лицензия
    OpenStreetMap в подписи карты) — в браузер пользователя."""

    dashboard_requested = Signal(int)

    def acceptNavigationRequest(  # noqa: N802 — имя метода Qt
        self, url: QUrl, navigation_type: QWebEnginePage.NavigationType, is_main_frame: bool
    ) -> bool:
        if url.scheme() == "app":
            wmo_index = url.path().strip("/")
            if url.host() == "dashboard" and wmo_index.isdigit():
                self.dashboard_requested.emit(int(wmo_index))
            return False
        if url.scheme() in ("http", "https"):
            QDesktopServices.openUrl(url)
            return False
        return True


class ScriptQueue:
    """Вызовы страницы карты: до загрузки страницы копятся (mapApi появляется
    только после выполнения её скрипта), после — выполняются сразу.

    Страница считается загруженной с первого успешного loadFinished и
    навсегда. Qt присылает loadFinished(False) ещё и на каждую отклонённую
    ссылку — "Открыть в дашборде" в карточке станции, лицензия OpenStreetMap,
    — хотя сама страница при этом никуда не девается. Раньше такой сигнал
    сбрасывал готовность, и после первого перехода по ссылке карта молча
    переставала перерисовываться (корректировка 2026-10-10)."""

    def __init__(self, run: Callable[[str], None]) -> None:
        self._run = run
        self.ready = False
        self._pending: list[str] = []

    def push(self, script: str) -> None:
        if self.ready:
            self._run(script)
        else:
            self._pending.append(script)

    def page_loaded(self, ok: bool) -> None:
        if not ok or self.ready:
            return
        self.ready = True
        pending, self._pending = self._pending, []
        for script in pending:
            self._run(script)


class WebMapCanvas(QWidget):
    """Страница карты в QWebEngineView."""

    dashboard_requested = Signal(int)
    base_changed = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.view = QWebEngineView()
        self.view.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        page = _MapPage(self.view)
        # Локальной странице по умолчанию запрещены запросы в интернет — без
        # этого тайлы OpenStreetMap не загрузились бы никогда
        page.settings().setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
        # Правила тайлов OpenStreetMap: приложение должно называть себя
        agent = page.profile().httpUserAgent()
        if _USER_AGENT_SUFFIX not in agent:
            page.profile().setHttpUserAgent(agent + _USER_AGENT_SUFFIX)
        page.dashboard_requested.connect(self.dashboard_requested)
        page.titleChanged.connect(self._on_title)
        self._scripts = ScriptQueue(page.runJavaScript)
        page.loadFinished.connect(self._scripts.page_loaded)
        self.view.setPage(page)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.view)
        self.view.load(QUrl.fromLocalFile(str(MAP_PAGE_PATH)))

    def call(self, function: str, *args: Any) -> None:
        arguments = ", ".join(json.dumps(arg, ensure_ascii=False) for arg in args)
        self._scripts.push(f"mapApi.{function}({arguments});")

    def _on_title(self, title: str) -> None:
        if title.startswith("base:"):
            self.base_changed.emit(title.removeprefix("base:"))


def base_status_text(base: str, outline_only: bool) -> str:
    if outline_only:
        return "Подложка: контурная карта"
    if base == BASE_OSM:
        return "Подложка: OpenStreetMap"
    return "Подложка: контурная карта — нет связи с OpenStreetMap, проверяем раз в полминуты"


def missing_coordinates_text(data: MapData) -> str:
    rows = data.without_coordinates
    if not rows:
        return ""
    names = ", ".join(row.label for row in rows[:3]) + (f" и ещё {len(rows) - 3}" if len(rows) > 3 else "")
    return f"Нет на карте (в справочнике не заданы координаты): {names}. Задать — «Справочники» на главном экране."


class MapDialog(QDialog):
    def __init__(
        self,
        engine: Engine,
        parent: QWidget | None = None,
        run_async: bool = True,
        canvas: QWidget | None = None,
        open_guide: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__(parent)
        enable_min_max_buttons(self)
        self.engine = engine
        # Передаётся дальше в дашборд ("Открыть в дашборде") — для его ссылок в путеводитель
        self._open_guide = open_guide
        # run_async=False — загрузка в том же потоке (тесты).
        self._run_async = run_async
        self._data: MapData | None = None
        self._period: tuple[date, date] | None = None
        self._values: dict[int, StationValue] = {}
        self._steps: list[TimeStep] = []
        self._base = BASE_OUTLINE
        self.setWindowTitle(_TITLE)
        if parent is not None:
            self.resize(parent.size())
        else:
            self.resize(1100, 750)
        self.setStyleSheet(theme.DIALOG_QSS + DASHBOARD_QSS + MAP_QSS)
        self.canvas = canvas if canvas is not None else WebMapCanvas()
        self._play_timer = QTimer(self)
        self._play_timer.setInterval(PLAY_INTERVAL_MS)
        self._play_timer.timeout.connect(self._play_step)
        self._build_ui()
        self.canvas.dashboard_requested.connect(self._open_dashboard)
        self.canvas.base_changed.connect(self._on_base_changed)
        self._update_controls()
        self._load()

    # --- построение интерфейса ---

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)
        layout.addWidget(self._build_controls())

        self._note = QLabel("")
        self._note.setObjectName("chartNote")
        self._note.setWordWrap(True)
        self._note.hide()
        layout.addWidget(self._note)

        map_area = QWidget()
        map_layout = QGridLayout(map_area)
        map_layout.setContentsMargins(0, 0, 0, 0)
        frame = QFrame()
        frame.setObjectName("card")
        frame_layout = QVBoxLayout(frame)
        frame_layout.setContentsMargins(1, 1, 1, 1)
        frame_layout.addWidget(self.canvas)
        map_layout.addWidget(frame, 0, 0)
        self.loading_overlay = LoadingOverlay("Собираем данные станций…")
        map_layout.addWidget(self.loading_overlay, 0, 0)
        layout.addWidget(map_area, stretch=1)

        bottom = QHBoxLayout()
        self._status = QLabel(base_status_text(self._base, False))
        self._status.setObjectName("mapStatus")
        bottom.addWidget(self._status, stretch=1)
        close_btn = QPushButton("Закрыть")
        close_btn.setObjectName("secondary")
        close_btn.clicked.connect(self.accept)
        bottom.addWidget(close_btn)
        layout.addLayout(bottom)
        reserve_focus_width(self.apply_btn, self.play_btn, close_btn)

    def _build_controls(self) -> QFrame:
        card = QFrame()
        card.setObjectName("card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 10, 16, 10)
        layout.setSpacing(8)

        top = QHBoxLayout()
        top.setSpacing(14)
        top.addWidget(QLabel("Показывать:"))
        switch, self.mode = mode_switch(["Станции", "Показатель", "Полнота"], self)
        top.addWidget(switch)
        self.mode.idToggled.connect(self._on_mode)
        top.addStretch(1)
        self.labels_check = QCheckBox("Подписи")
        self.labels_check.setChecked(True)
        self.labels_check.setToolTip("Название станции и значение под ним рядом с кружком")
        self.labels_check.toggled.connect(lambda on: self.canvas.call("setLabelsVisible", on))
        top.addWidget(self.labels_check)
        self.background_check = QCheckBox("Все станции справочника")
        self.background_check.setChecked(True)
        self.background_check.setToolTip("Серые точки — станции справочника, по которым данные не залиты")
        self.background_check.toggled.connect(lambda on: self.canvas.call("showBackground", on))
        top.addWidget(self.background_check)
        self.outline_check = QCheckBox("Контурная карта")
        self.outline_check.setToolTip(
            "Только границы стран и регионов, без карты OpenStreetMap. "
            "Без интернета карта и так становится контурной."
        )
        self.outline_check.toggled.connect(self._on_outline_only)
        top.addWidget(self.outline_check)
        layout.addLayout(top)

        self._indicator_row = QWidget()
        row = QHBoxLayout(self._indicator_row)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        row.addWidget(QLabel("Показатель:"))
        self.indicator_combo = QComboBox()
        rules = None
        for key, info in INDICATORS.items():
            # Черта между показателями температуры и осадков
            if rules is not None and info.rules is not rules:
                self.indicator_combo.insertSeparator(self.indicator_combo.count())
            rules = info.rules
            self.indicator_combo.addItem(info.label, key)
        self.indicator_combo.currentIndexChanged.connect(self._on_indicator_changed)
        row.addWidget(self.indicator_combo)
        self.time_combo = QComboBox()
        for key, label in _TIME_LABELS.items():
            self.time_combo.addItem(label, key)
        self.time_combo.setAccessibleName("Время")
        self.time_combo.currentIndexChanged.connect(self._on_time_changed)
        row.addWidget(self.time_combo)

        self._period_box = QWidget()
        period = QHBoxLayout(self._period_box)
        period.setContentsMargins(0, 0, 0, 0)
        period.setSpacing(8)
        period.addWidget(QLabel("с"))
        self.date_from_edit = DateField()
        period.addWidget(self.date_from_edit)
        period.addWidget(QLabel("по"))
        self.date_to_edit = DateField()
        period.addWidget(self.date_to_edit)
        self.apply_btn = QPushButton("Применить")
        self.apply_btn.setObjectName("primary")
        self.apply_btn.setToolTip("Пустой период — весь архив каждой станции")
        self.apply_btn.clicked.connect(self._apply_period)
        period.addWidget(self.apply_btn)
        period.addStretch(1)
        row.addWidget(self._period_box, stretch=1)

        self._slider_box = QWidget()
        slider = QHBoxLayout(self._slider_box)
        slider.setContentsMargins(0, 0, 0, 0)
        slider.setSpacing(10)
        self.play_btn = QPushButton("Проиграть")
        self.play_btn.setObjectName("secondary")
        self.play_btn.setCheckable(True)
        self.play_btn.toggled.connect(self._on_play_toggled)
        slider.addWidget(self.play_btn)
        self.time_slider = QSlider(Qt.Orientation.Horizontal)
        self.time_slider.setPageStep(5)
        self.time_slider.setAccessibleName("Шаг времени")
        self.time_slider.valueChanged.connect(self._on_slider_moved)
        slider.addWidget(self.time_slider, stretch=1)
        self._step_label = QLabel("")
        self._step_label.setObjectName("mapStep")
        self._step_label.setMinimumWidth(120)
        slider.addWidget(self._step_label)
        row.addWidget(self._slider_box, stretch=1)
        layout.addWidget(self._indicator_row)
        return card

    # --- состояние фильтров ---

    @property
    def indicator(self) -> str:
        return self.indicator_combo.currentData() or MEAN

    @property
    def time_mode(self) -> str:
        return self.time_combo.currentData() or TIME_PERIOD

    def _update_controls(self) -> None:
        indicator_mode = self.mode.checkedId() == MODE_INDICATOR
        by_period = self.time_mode == TIME_PERIOD
        self._indicator_row.setVisible(indicator_mode)
        self._period_box.setVisible(by_period)
        self._slider_box.setVisible(not by_period)

    def _on_mode(self, mode: int, checked: bool) -> None:
        if not checked:
            return
        self.play_btn.setChecked(False)
        self._update_controls()
        self._refresh()

    def _on_indicator_changed(self) -> None:
        self._recompute()
        self._refresh()

    def _on_time_changed(self) -> None:
        self.play_btn.setChecked(False)
        self._update_controls()
        self._recompute()
        self._refresh()

    def _on_outline_only(self, only: bool) -> None:
        self.canvas.call("setOutlineOnly", only)
        self._status.setText(base_status_text(self._base, only))

    def _on_base_changed(self, base: str) -> None:
        self._base = base
        self._status.setText(base_status_text(base, self.outline_check.isChecked()))

    # --- загрузка и расчёт ---

    def _load(self) -> None:
        self.loading_overlay.setVisible(True)
        self.loading_overlay.raise_()

        def task() -> MapData:
            # Заодно досчитывает нормы и отклонения станций, чьи данные менялись
            return fetch_map_data(self.engine)

        def failed(exc: BaseException) -> None:
            self.loading_overlay.setVisible(False)
            QMessageBox.warning(self, _TITLE, f"Не удалось загрузить данные: {exc}")

        if self._run_async:
            run_in_background(self, task, self._show_data, failed)
            return
        try:
            data = task()
        except Exception as exc:  # noqa: BLE001 — тот же путь, что и у фонового режима
            failed(exc)
        else:
            self._show_data(data)

    def _show_data(self, data: MapData) -> None:
        self._data = data
        self.loading_overlay.setVisible(False)
        notes = [missing_coordinates_text(data)]
        if not data.sources:
            notes.insert(0, "Данные ещё не залиты — на карте только станции справочника.")
        text = " ".join(note for note in notes if note)
        self._note.setText(text)
        self._note.setVisible(bool(text))
        self.canvas.call("setBackground", background_points(data))
        self._recompute()
        self._refresh(fit=True)

    def _recompute(self) -> None:
        """Пересчитать значения показателя под текущие фильтры: за период —
        агрегатным запросом к базе, ряды для ползунка — из уже прочитанного."""
        data = self._data
        if data is None:
            return
        if self.time_mode == TIME_PERIOD:
            self._steps = []
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            try:
                self._values = period_values(self.engine, data, self.indicator, *(self._period or (None, None)))
            finally:
                QApplication.restoreOverrideCursor()
            return
        self._steps = time_series(data, self.indicator, self.time_mode)
        self.time_slider.blockSignals(True)
        self.time_slider.setRange(0, max(len(self._steps) - 1, 0))
        self.time_slider.setValue(self.time_slider.maximum())  # последний год/месяц
        self.time_slider.blockSignals(False)
        self.time_slider.setEnabled(len(self._steps) > 1)
        self.play_btn.setEnabled(len(self._steps) > 1)

    def _apply_period(self) -> None:
        dates: list[date | None] = []
        for label, field in (("с", self.date_from_edit), ("по", self.date_to_edit)):
            try:
                dates.append(field.value())
            except DateFieldError as exc:
                QMessageBox.warning(self, _TITLE, f"Период, поле «{label}»: {exc}")
                return
        date_from, date_to = dates
        if (date_from is None) != (date_to is None):
            QMessageBox.warning(
                self, _TITLE,
                "Период задаётся двумя датами: заполните обе или очистите обе "
                "(тогда берётся весь архив каждой станции).",
            )
            return
        if date_from is not None and date_to is not None and date_from > date_to:
            QMessageBox.warning(self, _TITLE, "Начало периода позже его конца.")
            return
        self._period = (date_from, date_to) if date_from is not None and date_to is not None else None
        self._recompute()
        self._refresh()

    # --- показ ---

    def current_layer(self) -> MapLayer | None:
        data = self._data
        if data is None:
            return None
        mode = self.mode.checkedId()
        if mode == MODE_STATIONS:
            return stations_layer(data)
        if mode == MODE_FILL:
            return fill_layer(data)
        if self.time_mode == TIME_PERIOD:
            if self._period:
                title = f"{self._period[0]:%d.%m.%Y}–{self._period[1]:%d.%m.%Y}"
                scope = SCOPE_PERIOD
            else:
                title = WHOLE_ARCHIVE_PER_YEAR if self.indicator in (PRECIP_SUM, PRECIP_PERCENT) else WHOLE_ARCHIVE
                scope = SCOPE_ARCHIVE
            numbers = [item.value for item in self._values.values() if item.value is not None]
            scale = indicator_scale(self.indicator, numbers)
            return indicator_layer(data, self.indicator, self._values, scale, title, scope)
        if not self._steps:
            return indicator_layer(data, self.indicator, {}, indicator_scale(self.indicator, []), "нет данных")
        # Шкала — одна на все шаги: цвет должен значить одно и то же в любом году
        numbers = [item.value for step in self._steps for item in step.values.values() if item.value is not None]
        step = self._steps[self.time_slider.value()]
        scope = SCOPE_MONTH if self.time_mode == TIME_MONTHS else SCOPE_YEAR
        return indicator_layer(
            data, self.indicator, step.values, indicator_scale(self.indicator, numbers), step.label, scope
        )

    def _refresh(self, fit: bool = False) -> None:
        layer = self.current_layer()
        if layer is None:
            return
        self._step_label.setText(self._steps[self.time_slider.value()].label if self._steps else "")
        self.canvas.call("setMarkers", layer.markers, fit)
        self.canvas.call("setLegend", layer.legend)

    # --- ползунок времени ---

    def _on_slider_moved(self) -> None:
        self._refresh()

    def _on_play_toggled(self, playing: bool) -> None:
        self.play_btn.setText("Остановить" if playing else "Проиграть")
        if not playing:
            self._play_timer.stop()
            return
        if self.time_slider.value() >= self.time_slider.maximum():
            self.time_slider.setValue(0)  # с конца — сначала
        self._play_timer.start()

    def _play_step(self) -> None:
        if self.time_slider.value() >= self.time_slider.maximum():
            self.play_btn.setChecked(False)
            return
        self.time_slider.setValue(self.time_slider.value() + 1)

    # --- переходы ---

    @property
    def dashboard_tab(self) -> str:
        """Вкладка дашборда для перехода из карточки станции: на карте
        показатель осадков — значит и в дашборде "Осадки"."""
        precipitation = self.mode.checkedId() == MODE_INDICATOR and INDICATORS[self.indicator].rules is PRECIPITATION
        return TAB_PRECIPITATION if precipitation else TAB_TEMPERATURE

    def _open_dashboard(self, wmo_index: int) -> None:
        self.play_btn.setChecked(False)
        exec_window_modal(DashboardDialog(
            self.engine, self, wmo_index=wmo_index, tab=self.dashboard_tab, open_guide=self._open_guide
        ))

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        minimize_owner_instead(self, event)
