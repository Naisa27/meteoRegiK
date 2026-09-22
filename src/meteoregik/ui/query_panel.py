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
устаревает, а пустое поле само по себе — удобное состояние "весь период"."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QStackedWidget,
    QTableWidget,
    QVBoxLayout,
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
from meteoregik.ui.aggregate_table_view import populate_table_widget
from meteoregik.ui.date_field import DateField, DateFieldError
from meteoregik.ui.station_combo import StationCombo

OutputMode = str  # "detailed" | "monthly" | "yearly"
ViewKind = str  # "table" | "chart"

_OUTPUT_MODE_LABELS: dict[OutputMode, str] = {
    "detailed": "Подробно по дням",
    "monthly": "Помесячно",
    "yearly": "По годам",
}


def _encode_param(key: ParamKey) -> str:
    return f"{key[0]}:{key[1]}"


def _decode_param(text: str) -> ParamKey | None:
    parameter, _, stat_type = text.partition(":")
    if not stat_type:
        return None
    return (parameter, stat_type)


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

    def _set_all(self, state: Qt.CheckState) -> None:
        for i in range(self.list_widget.count()):
            self.list_widget.item(i).setCheckState(state)

    def selected_parameters(self) -> list[ParamKey]:
        return [
            self.list_widget.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self.list_widget.count())
            if self.list_widget.item(i).checkState() == Qt.CheckState.Checked
        ]


class AggregateViewDialog(QDialog):
    def __init__(self, engine: Engine, parent=None, preferences: Preferences | None = None) -> None:
        super().__init__(parent)
        self.engine = engine
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

        self._build_ui()
        self._reload_station_choices()
        self._restore_preferences()

    # --- построение интерфейса ---

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        layout.addWidget(self._build_filter_card())

        export_row = QHBoxLayout()
        export_row.addStretch(1)
        self.export_btn = QPushButton("Экспорт в Excel")
        self.export_btn.setObjectName("skySoft")
        self.export_btn.setEnabled(False)
        self.export_btn.clicked.connect(self._export_to_excel)
        export_row.addWidget(self.export_btn)
        layout.addLayout(export_row)

        self._station_label = QLabel("")
        self._station_label.setStyleSheet("font-weight: 600; font-size: 14px;")
        layout.addWidget(self._station_label)

        self._stack = QStackedWidget()
        self.table = QTableWidget()
        self._stack.addWidget(self.table)
        self.chart_view = AggregateChartView()
        self._stack.addWidget(self.chart_view)
        layout.addWidget(self._stack, stretch=1)

        close_row = QHBoxLayout()
        close_row.addStretch(1)
        close_btn = QPushButton("Закрыть")
        close_btn.setObjectName("secondary")
        close_btn.clicked.connect(self.accept)
        close_row.addWidget(close_btn)
        layout.addLayout(close_row)

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
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._selected_parameter_keys = set(dialog.selected_parameters())
        self._refresh_params_label()

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

        output_mode = self._preferences.get("aggregate_view.output_mode")
        if output_mode in _OUTPUT_MODE_LABELS:
            self._set_output_mode(output_mode)

        view_kind = self._preferences.get("aggregate_view.view_kind")
        if view_kind == "chart":
            self.chart_radio.setChecked(True)

    def _save_preferences(self, parameters: list[ParamKey]) -> None:
        # wmo_index сюда не попадает — фильтр "Метеостанция" не запоминается
        # между открытиями экрана (см. _restore_preferences).
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

        parameters = self._selected_parameters()
        if not parameters:
            QMessageBox.warning(self, "Фильтр", "Выберите хотя бы один показатель.")
            return

        output_mode = self._output_mode()
        view_kind = self._view_kind()
        station_title = self.station_combo.currentText()

        grid, chart_data = self._build_results(wmo_index, date_from, date_to, parameters, output_mode)

        self._station_label.setText(station_title)
        self._last_grid = grid
        self._last_chart_data = chart_data
        self._last_parameters = parameters
        self._last_view_kind = view_kind
        self._last_station_title = station_title
        self._save_preferences(parameters)

        if view_kind == "table":
            populate_table_widget(self.table, grid)
            self._stack.setCurrentWidget(self.table)
        else:
            self.chart_view.plot(chart_data, parameters)
            self._stack.setCurrentWidget(self.chart_view)

        self.export_btn.setEnabled(bool(grid.rows) or bool(chart_data.x_labels))
        if not grid.rows and not chart_data.x_labels:
            QMessageBox.information(self, "Просмотр агрегатных данных", "По указанным фильтрам данных не найдено.")

    def _build_results(
        self, wmo_index: int, date_from: date, date_to: date, parameters: list[ParamKey], output_mode: OutputMode
    ) -> tuple[Grid, ChartData]:
        if output_mode == "detailed":
            rows = fetch_rows(self.engine, wmo_index, date_from, date_to, parameters, DETAILED_PERIOD_TYPES)
            years = group_detailed(rows)
            return build_detailed_grid(years, parameters), chart_data_from_detailed(years, parameters)
        if output_mode == "monthly":
            rows = fetch_rows(self.engine, wmo_index, date_from, date_to, parameters, MONTHLY_PERIOD_TYPES)
            years = group_monthly(rows)
            return build_monthly_grid(years, parameters), chart_data_from_monthly(years, parameters)

        rows = fetch_rows(self.engine, wmo_index, date_from, date_to, parameters, YEARLY_PERIOD_TYPES)
        year_values = group_yearly(rows)
        years_range = list(range(date_from.year, date_to.year + 1))
        return (
            build_yearly_grid(year_values, years_range, parameters),
            chart_data_from_yearly(year_values, years_range, parameters),
        )

    # --- экспорт ---

    def _export_to_excel(self) -> None:
        if self._last_grid is None and self._last_chart_data is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Экспорт в Excel", "", "Excel файлы (*.xlsx)"
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
        QMessageBox.information(self, "Экспорт в Excel", "Файл сохранён.")
