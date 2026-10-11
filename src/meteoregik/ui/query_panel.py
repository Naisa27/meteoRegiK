"""Экран "Просмотр агрегатных данных": выбор станции, периода, вида вывода
(таблица/график), для таблицы — уровня детализации (подробно по дням/
помесячно/по годам), показателей -> результат из computed_indicators,
раскладка которого один в один повторяет
.claude/docs/пример_вывода_агрегатных_данных.xlsx (см. reports/grid.py).

Только одна станция за раз (решение пользователя, 2026-09-22) — StationCombo
без all_text, станция обязательна. Раскладка таблицы строится общим слоем
reports/ (не дублируется между экраном и export/aggregate_excel.py).

Пустой период при "Применить" означает "весь период, для которого у станции
есть посчитанные агрегаты" (aggregate_query.station_period_bounds) — тем же
паттерном, что и data_browser.py. В отличие от выбранных полей/станции/
режима период НЕ запоминается в Preferences: "период по умолчанию" быстро
устаревает, а пустое поле само по себе — удобное состояние "весь период".

Когда какой фильтр применяется (правка 2026-09-25, task.md "корректировки"
п.7): "Применить" фиксирует только станцию и период (_AppliedFilter);
показатели применяются по "Готово" в окне выбора показателей, а "Вид" и "Тип
вывода" — сразу при переключении. Всё, кроме станции/периода, перестраивает
результат поверх уже зафиксированных станции и периода (_refresh_results);
до первого "Применить" перестраивать нечего — выбор просто запоминается."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QEvent, QStandardPaths, Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QStackedWidget,
    QTableView,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy.engine import Engine

from meteoregik.export.aggregate_excel import export_aggregate_view
from meteoregik.preferences import Preferences
from meteoregik.reports.aggregate_query import (
    DETAILED_PERIOD_TYPES,
    MONTHLY_PERIOD_TYPES,
    YEARLY_PERIOD_TYPES,
    fetch_rows,
    group_detailed,
    group_monthly,
    group_yearly,
    station_period_bounds,
)
from meteoregik.reports.chart_data import (
    ChartData,
    chart_data_from_detailed,
    chart_data_from_monthly,
    chart_data_from_yearly,
)
from meteoregik.reports.grid import Grid, build_detailed_grid, build_monthly_grid, build_yearly_grid
from meteoregik.reports.parameters import PARAMETER_LABELS, ParamKey
from meteoregik.stations.choices import load_stations_with_computed_indicators
from meteoregik.ui import theme
from meteoregik.ui.aggregate_chart_view import AggregateChartView
from meteoregik.ui.aggregate_table_view import show_grid
from meteoregik.ui.background import run_in_background
from meteoregik.ui.buttons import reserve_focus_width
from meteoregik.ui.date_field import DateField, DateFieldError
from meteoregik.ui.dialogs import enable_min_max_buttons, exec_window_modal, minimize_owner_instead
from meteoregik.ui.loading_overlay import LoadingOverlay
from meteoregik.ui.station_combo import StationCombo

OutputMode = str  # "detailed" | "monthly" | "yearly"
ViewKind = str  # "table" | "chart"

_OUTPUT_MODE_LABELS: dict[OutputMode, str] = {
    "detailed": "Подробно по дням",
    "monthly": "Помесячно",
    "yearly": "По годам",
}


_FORBIDDEN_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*]')


def suggest_export_filename(
    station_name: str, wmo_index: int, view_kind: ViewKind, output_mode: OutputMode, date_from: date, date_to: date
) -> str:
    """Имя файла экспорта по умолчанию (правка 2026-09-25, п.4) — из того,
    что на экране: станция, вид и тип вывода, период. Например
    "Мосальск (27704) — таблица, помесячно — 01.01.2020–31.12.2021.xlsx":
    по имени понятно, что внутри, и выгрузки разных станций/периодов в одной
    папке не перезаписывают друг друга. Недопустимые в именах файлов Windows
    символы заменяются на "_"."""
    view = "таблица" if view_kind == "table" else "график"
    mode = _OUTPUT_MODE_LABELS[output_mode].lower()
    period = f"{date_from:%d.%m.%Y}–{date_to:%d.%m.%Y}"
    name = f"{station_name} ({wmo_index}) — {view}, {mode} — {period}"
    return _FORBIDDEN_FILENAME_CHARS.sub("_", name) + ".xlsx"


def _encode_param(key: ParamKey) -> str:
    return f"{key[0]}:{key[1]}"


def _decode_param(text: str) -> ParamKey | None:
    parameter, _, stat_type = text.partition(":")
    if not stat_type:
        return None
    return (parameter, stat_type)


@dataclass(frozen=True)
class _AppliedFilter:
    """Станция и период, зафиксированные последним "Применить"."""

    wmo_index: int
    date_from: date
    date_to: date
    station_title: str
    station_name: str


def _group_frame() -> tuple[QFrame, QHBoxLayout]:
    """Рамка-группа для визуального разделения фильтров в одной строке (тот
    же паттерн, что и "Период" в data_browser.py — QFrame#group из
    DIALOG_QSS, тонкая рамка, без отличающегося фона)."""
    frame = QFrame()
    frame.setObjectName("group")
    layout = QHBoxLayout(frame)
    layout.setContentsMargins(12, 6, 12, 6)
    layout.setSpacing(8)
    return frame, layout


class _ParametersDialog(QDialog):
    """Множественный выбор показателей — по аналогии с _FieldsDialog в
    data_browser.py (список с чекбоксами + "Выбрать все"/"Снять все"), не
    общий класс: тянуть приватный класс другого экрана через импорт не
    стоит ради всего одного потребителя, паттерн копируется явно."""

    def __init__(self, selected: set[ParamKey], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Выбрать показатели")
        self.resize(360, 420)
        self.setStyleSheet(theme.DIALOG_QSS)
        self._build_ui(selected)

    def _build_ui(self, selected: set[ParamKey]) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        self.list_widget = QListWidget()
        for key, label in PARAMETER_LABELS.items():
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, key)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if key in selected else Qt.CheckState.Unchecked)
            self.list_widget.addItem(item)
        layout.addWidget(self.list_widget, stretch=1)

        bulk_row = QHBoxLayout()
        select_all_btn = QPushButton("Выбрать все")
        select_all_btn.setObjectName("secondary")
        select_all_btn.clicked.connect(lambda: self._set_all(Qt.CheckState.Checked))
        bulk_row.addWidget(select_all_btn)
        clear_btn = QPushButton("Снять все")
        clear_btn.setObjectName("secondary")
        clear_btn.clicked.connect(lambda: self._set_all(Qt.CheckState.Unchecked))
        bulk_row.addWidget(clear_btn)
        bulk_row.addStretch(1)
        layout.addLayout(bulk_row)

        # Ошибка — рядом со списком, а не отдельным окном поверх окна
        # (ui-ux-pro-max: error near field); текстом, не только цветом.
        self.error_label = QLabel("Выберите хотя бы один показатель.")
        self.error_label.setStyleSheet(f"color: {theme.ERROR_TEXT};")
        self.error_label.hide()
        layout.addWidget(self.error_label)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        cancel_btn = QPushButton("Отмена")
        cancel_btn.setObjectName("secondary")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        ok_btn = QPushButton("Готово")
        ok_btn.setObjectName("primary")
        ok_btn.clicked.connect(self.accept)
        btn_row.addWidget(ok_btn)
        layout.addLayout(btn_row)
        reserve_focus_width(*self.findChildren(QPushButton))

    def _set_all(self, state: Qt.CheckState) -> None:
        for i in range(self.list_widget.count()):
            self.list_widget.item(i).setCheckState(state)

    def accept(self) -> None:
        # "Готово" с пустым выбором не закрывает окно: показатели применяются
        # к таблице сразу по "Готово", и пустой выбор дал бы пустой результат.
        if not self.selected_parameters():
            self.error_label.show()
            return
        self.error_label.hide()
        super().accept()

    def selected_parameters(self) -> list[ParamKey]:
        return [
            self.list_widget.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self.list_widget.count())
            if self.list_widget.item(i).checkState() == Qt.CheckState.Checked
        ]


def build_results(
    engine: Engine, wmo_index: int, date_from: date, date_to: date, parameters: list[ParamKey], output_mode: OutputMode
) -> tuple[Grid, ChartData]:
    """Выборка + раскладка таблицы и графика. Не трогает виджеты —
    выполняется в фоновом потоке (ui/background.py)."""
    years_range = list(range(date_from.year, date_to.year + 1))
    if output_mode == "detailed":
        rows = fetch_rows(engine, wmo_index, date_from, date_to, parameters, DETAILED_PERIOD_TYPES)
        years = group_detailed(rows)
        return build_detailed_grid(years, parameters), chart_data_from_detailed(years, parameters)
    if output_mode == "monthly":
        rows = fetch_rows(engine, wmo_index, date_from, date_to, parameters, MONTHLY_PERIOD_TYPES)
        years = group_monthly(rows)
        return build_monthly_grid(years, parameters, years_range), chart_data_from_monthly(years, parameters)

    rows = fetch_rows(engine, wmo_index, date_from, date_to, parameters, YEARLY_PERIOD_TYPES)
    year_values = group_yearly(rows)
    return (
        build_yearly_grid(year_values, years_range, parameters),
        chart_data_from_yearly(year_values, years_range, parameters),
    )


class AggregateViewDialog(QDialog):
    def __init__(
        self,
        engine: Engine,
        parent=None,
        preferences: Preferences | None = None,
        run_async: bool = True,
    ) -> None:
        super().__init__(parent)
        # Кнопки "свернуть"/"развернуть" (правка 2026-09-25, п.3).
        enable_min_max_buttons(self)
        self.engine = engine
        # run_async=False — выборка в том же потоке (тесты: результат нужен
        # сразу после щелчка, без ожидания пула потоков).
        self._run_async = run_async
        self._request_id = 0
        self._preferences = preferences or Preferences.next_to_database(engine.url.database)
        self._selected_parameter_keys: set[ParamKey] = set(PARAMETER_LABELS)
        self.setWindowTitle("Просмотр агрегатных данных")
        # Тот же размер, что у главного окна на момент открытия (2026-09-23,
        # по замечанию пользователя — модалка была тесновата под "Подробно"
        # с её 36 колонками); без parent (тесты, автономный запуск) — тот же
        # размер по умолчанию, что и у MainWindow (main_window.py).
        if parent is not None:
            self.resize(parent.size())
        else:
            self.resize(1100, 750)
        self.setStyleSheet(theme.DIALOG_QSS)

        self._last_grid: Grid | None = None
        self._last_chart_data: ChartData | None = None
        self._last_parameters: list[ParamKey] = []
        self._last_view_kind: ViewKind = "table"
        self._last_station_title: str = ""
        self._last_output_mode: OutputMode = "detailed"
        self._applied: _AppliedFilter | None = None

        self._build_ui()
        self._reload_station_choices()
        self._restore_preferences()

    # --- построение интерфейса ---

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        layout.addWidget(self._build_filter_card())

        # Название станции и "Экспорт в Excel" — в одной строке (правка
        # 2026-09-25, п.1): две отдельные строки съедали высоту у таблицы.
        result_row = QHBoxLayout()
        self._station_label = QLabel("")
        self._station_label.setStyleSheet("font-weight: 600; font-size: 14px;")
        result_row.addWidget(self._station_label, stretch=1)
        self.export_btn = QPushButton("Экспорт в Excel")
        self.export_btn.setObjectName("skySoft")
        self.export_btn.setEnabled(False)
        self.export_btn.clicked.connect(self._export_to_excel)
        result_row.addWidget(self.export_btn)
        layout.addLayout(result_row)

        self._stack = QStackedWidget()
        self.table = QTableView()
        self._stack.addWidget(self.table)
        self.chart_view = AggregateChartView()
        self._stack.addWidget(self.chart_view)
        # Лоадер в той же ячейке сетки, что и результат, — поверх него.
        results_area = QWidget()
        results_layout = QGridLayout(results_area)
        results_layout.setContentsMargins(0, 0, 0, 0)
        results_layout.addWidget(self._stack, 0, 0)
        self.loading_overlay = LoadingOverlay()
        results_layout.addWidget(self.loading_overlay, 0, 0)
        layout.addWidget(results_area, stretch=1)

        close_row = QHBoxLayout()
        close_row.addStretch(1)
        close_btn = QPushButton("Закрыть")
        close_btn.setObjectName("secondary")
        close_btn.clicked.connect(self.accept)
        close_row.addWidget(close_btn)
        layout.addLayout(close_row)

        # Все кнопки уже в раскладке окна со стилем DIALOG_QSS — теперь их
        # sizeHint посчитан по верным отступам (см. ui/buttons.py).
        reserve_focus_width(*self.findChildren(QPushButton))

    def _build_filter_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("card")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(16, 12, 16, 12)
        card_layout.setSpacing(10)

        # Строка 1: станция + период (в один ряд).
        top_row = QHBoxLayout()
        top_row.setSpacing(10)
        top_row.addWidget(QLabel("Метеостанция:"))
        self.station_combo = StationCombo()
        top_row.addWidget(self.station_combo, stretch=1)

        period_group, period_layout = _group_frame()
        period_title = QLabel("Период:")
        period_title.setStyleSheet("font-weight: 600;")
        period_layout.addWidget(period_title)
        period_layout.addWidget(QLabel("с"))
        self.date_from_edit = DateField()
        period_layout.addWidget(self.date_from_edit)
        period_layout.addWidget(QLabel("по"))
        self.date_to_edit = DateField()
        period_layout.addWidget(self.date_to_edit)
        top_row.addWidget(period_group)
        card_layout.addLayout(top_row)

        # Строка 2: вид / тип вывода / показатели (каждый в своей рамке) + "Применить".
        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(10)

        view_group, view_layout = _group_frame()
        view_title = QLabel("Вид:")
        view_title.setStyleSheet("font-weight: 600;")
        view_layout.addWidget(view_title)
        self.table_radio = QRadioButton("Таблица")
        self.chart_radio = QRadioButton("График")
        self.table_radio.setChecked(True)
        self._view_group = QButtonGroup(self)
        self._view_group.addButton(self.table_radio)
        self._view_group.addButton(self.chart_radio)
        view_layout.addWidget(self.table_radio)
        view_layout.addWidget(self.chart_radio)
        self._view_group.buttonToggled.connect(self._on_display_option_toggled)
        bottom_row.addWidget(view_group)

        # "Тип вывода" виден и доступен всегда — в т.ч. для вида "график"
        # (решение 2026-09-23): гранулярность точек графика тоже определяется
        # этим фильтром (день/месяц/год), см. _build_results.
        output_group, output_layout = _group_frame()
        output_title = QLabel("Тип вывода:")
        output_title.setStyleSheet("font-weight: 600;")
        output_layout.addWidget(output_title)
        self.detailed_radio = QRadioButton(_OUTPUT_MODE_LABELS["detailed"])
        self.monthly_radio = QRadioButton(_OUTPUT_MODE_LABELS["monthly"])
        self.yearly_radio = QRadioButton(_OUTPUT_MODE_LABELS["yearly"])
        self.detailed_radio.setChecked(True)
        self._output_mode_group = QButtonGroup(self)
        for radio in (self.detailed_radio, self.monthly_radio, self.yearly_radio):
            self._output_mode_group.addButton(radio)
            output_layout.addWidget(radio)
        self._output_mode_group.buttonToggled.connect(self._on_display_option_toggled)
        bottom_row.addWidget(output_group)

        params_group, params_layout = _group_frame()
        params_title = QLabel("Показатели:")
        params_title.setStyleSheet("font-weight: 600;")
        params_layout.addWidget(params_title)
        self.params_btn = QPushButton("Выбрать показатели")
        self.params_btn.setObjectName("skySoft")
        self.params_btn.clicked.connect(self._choose_parameters)
        params_layout.addWidget(self.params_btn)
        self.params_label = QLabel("")
        self.params_label.setStyleSheet(f"color: {theme.TEXT_SECONDARY};")
        params_layout.addWidget(self.params_label)
        bottom_row.addWidget(params_group)

        bottom_row.addStretch(1)
        self.apply_btn = QPushButton("Применить")
        self.apply_btn.setObjectName("primary")
        self.apply_btn.clicked.connect(self._apply_filters)
        bottom_row.addWidget(self.apply_btn)
        card_layout.addLayout(bottom_row)

        self._refresh_params_label()
        return card

    # --- станции / настройки ---

    def _reload_station_choices(self) -> None:
        self.station_combo.set_choices(load_stations_with_computed_indicators(self.engine))

    def _output_mode(self) -> OutputMode:
        if self.monthly_radio.isChecked():
            return "monthly"
        if self.yearly_radio.isChecked():
            return "yearly"
        return "detailed"

    def _set_output_mode(self, mode: OutputMode) -> None:
        {"detailed": self.detailed_radio, "monthly": self.monthly_radio, "yearly": self.yearly_radio}[
            mode
        ].setChecked(True)

    def _view_kind(self) -> ViewKind:
        return "chart" if self.chart_radio.isChecked() else "table"

    def _selected_parameters(self) -> list[ParamKey]:
        # порядок — как в реестре PARAMETER_LABELS, не порядок выбора в диалоге
        return [key for key in PARAMETER_LABELS if key in self._selected_parameter_keys]

    def _refresh_params_label(self) -> None:
        total = len(PARAMETER_LABELS)
        self.params_label.setText(f"Выбрано: {len(self._selected_parameter_keys)} из {total}")

    def _choose_parameters(self) -> None:
        dialog = _ParametersDialog(self._selected_parameter_keys, self)
        if exec_window_modal(dialog) != QDialog.DialogCode.Accepted:
            return
        self._selected_parameter_keys = set(dialog.selected_parameters())
        self._refresh_params_label()
        self._save_preferences()
        self._refresh_results()

    def _on_display_option_toggled(self, _button: QRadioButton, checked: bool) -> None:
        # buttonToggled приходит дважды (снятая и выбранная кнопка) — реагируем
        # только на выбранную, чтобы не перестраивать результат два раза.
        if not checked:
            return
        self._save_preferences()
        self._refresh_results()

    def _restore_preferences(self) -> None:
        # Станция НЕ восстанавливается (решение 2026-09-23) — при каждом
        # открытии экрана фильтр "Метеостанция" должен быть пустым
        # (плейсхолдер "Выберите метеостанцию" из StationCombo), а не
        # предзаполненным станцией из прошлого раза.
        saved_params = self._preferences.get("aggregate_view.parameters")
        if isinstance(saved_params, list):
            decoded = {key for text in saved_params if (key := _decode_param(text)) is not None}
            if decoded:
                self._selected_parameter_keys = decoded
                self._refresh_params_label()

        # Оба значения читаются ДО переключения радиокнопок: переключение сразу
        # сохраняет текущий выбор (_on_display_option_toggled) и иначе затёрло
        # бы ещё не восстановленный вид.
        output_mode = self._preferences.get("aggregate_view.output_mode")
        view_kind = self._preferences.get("aggregate_view.view_kind")
        if output_mode in _OUTPUT_MODE_LABELS:
            self._set_output_mode(output_mode)
        if view_kind == "chart":
            self.chart_radio.setChecked(True)

    def _save_preferences(self) -> None:
        # wmo_index сюда не попадает — фильтр "Метеостанция" не запоминается
        # между открытиями экрана (см. _restore_preferences).
        parameters = self._selected_parameters()
        self._preferences.set("aggregate_view.parameters", [_encode_param(key) for key in parameters])
        self._preferences.set("aggregate_view.output_mode", self._output_mode())
        self._preferences.set("aggregate_view.view_kind", self._view_kind())

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
                "(тогда будут показаны данные за весь имеющийся период).",
            )
            return
        if date_from is not None and date_to is not None and date_from > date_to:
            QMessageBox.warning(self, "Фильтр", "Начало периода позже его конца.")
            return

        if date_from is None or date_to is None:
            bounds = station_period_bounds(self.engine, wmo_index)
            if bounds is None:
                QMessageBox.information(
                    self, "Просмотр агрегатных данных", "По этой станции ещё нет посчитанных агрегатов."
                )
                return
            date_from, date_to = bounds

        if not self._selected_parameters():
            QMessageBox.warning(self, "Фильтр", "Выберите хотя бы один показатель.")
            return

        self._applied = _AppliedFilter(
            wmo_index,
            date_from,
            date_to,
            station_title=self.station_combo.currentText(),
            station_name=self.station_combo.name_of(wmo_index) or str(wmo_index),
        )
        self._refresh_results(notify_empty=True)

    def _refresh_results(self, notify_empty: bool = False) -> None:
        """Перестраивает таблицу/график по зафиксированным станции и периоду и
        текущим показателям/виду/типу вывода. Сообщение "данных не найдено"
        — только по явному "Применить": при переключении вида/типа вывода
        всплывающее окно на каждый щелчок мешало бы."""
        applied = self._applied
        parameters = self._selected_parameters()
        if applied is None or not parameters:
            return

        self._save_preferences()
        output_mode = self._output_mode()
        view_kind = self._view_kind()
        # Номер запроса: пока грузится один, пользователь может переключить
        # вид/тип вывода — показывается только результат последнего запроса.
        self._request_id += 1
        request_id = self._request_id
        self._set_loading(True)

        def task() -> tuple[Grid, ChartData]:
            return build_results(
                self.engine, applied.wmo_index, applied.date_from, applied.date_to, parameters, output_mode
            )

        def on_done(result: tuple[Grid, ChartData]) -> None:
            if request_id != self._request_id:
                return
            grid, chart_data = result
            self._show_results(applied, parameters, output_mode, view_kind, grid, chart_data, notify_empty)
            self._set_loading(False)

        def on_error(exc: BaseException) -> None:
            if request_id != self._request_id:
                return
            self._set_loading(False)
            QMessageBox.warning(self, "Просмотр агрегатных данных", f"Не удалось загрузить данные: {exc}")

        self._run(task, on_done, on_error)

    def _run(
        self,
        task: Callable[[], tuple[Grid, ChartData]],
        on_done: Callable[[tuple[Grid, ChartData]], None],
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
        if loading:
            self.loading_overlay.raise_()
            self.export_btn.setEnabled(False)

    def _show_results(
        self,
        applied: _AppliedFilter,
        parameters: list[ParamKey],
        output_mode: OutputMode,
        view_kind: ViewKind,
        grid: Grid,
        chart_data: ChartData,
        notify_empty: bool,
    ) -> None:
        self._station_label.setText(applied.station_title)
        self._last_grid = grid
        self._last_chart_data = chart_data
        self._last_parameters = parameters
        self._last_view_kind = view_kind
        self._last_station_title = applied.station_title
        self._last_output_mode = output_mode

        if view_kind == "table":
            show_grid(self.table, grid)
            self._stack.setCurrentWidget(self.table)
        else:
            self.chart_view.plot(chart_data, parameters)
            self._stack.setCurrentWidget(self.chart_view)

        has_data = bool(grid.rows) or bool(chart_data.x_labels)
        self.export_btn.setEnabled(has_data)
        if notify_empty and not has_data:
            QMessageBox.information(self, "Просмотр агрегатных данных", "По указанным фильтрам данных не найдено.")

    # --- экспорт ---

    def _export_to_excel(self) -> None:
        applied = self._applied
        if applied is None or (self._last_grid is None and self._last_chart_data is None):
            return
        filename = suggest_export_filename(
            applied.station_name,
            applied.wmo_index,
            self._last_view_kind,
            self._last_output_mode,
            applied.date_from,
            applied.date_to,
        )
        path, _ = QFileDialog.getSaveFileName(
            self, "Экспорт в Excel", str(self._export_dir() / filename), "Excel файлы (*.xlsx)"
        )
        if not path:
            return
        if not path.lower().endswith(".xlsx"):
            path += ".xlsx"

        primary = "table" if self._last_view_kind == "table" else "chart"
        try:
            export_aggregate_view(
                out_path=Path(path),
                station_title=self._last_station_title,
                primary=primary,
                grid=self._last_grid,
                chart_data=self._last_chart_data or ChartData(),
                parameters=self._last_parameters,
            )
        except OSError as exc:
            QMessageBox.warning(self, "Экспорт в Excel", f"Не удалось сохранить файл: {exc}")
            return
        self._preferences.set("aggregate_view.export_dir", str(Path(path).parent))
        QMessageBox.information(self, "Экспорт в Excel", "Файл сохранён.")

    def _export_dir(self) -> Path:
        """Папка, куда сохраняли в прошлый раз, иначе "Загрузки" (правка 2026-09-25)."""
        saved = self._preferences.get("aggregate_view.export_dir")
        if isinstance(saved, str) and Path(saved).is_dir():
            return Path(saved)
        return Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DownloadLocation))

    # --- свернуть окно ---

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        minimize_owner_instead(self, event)
