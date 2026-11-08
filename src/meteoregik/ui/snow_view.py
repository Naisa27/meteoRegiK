"""Таблицы снежного покрова в отдельном окне расчёта показателей."""

from __future__ import annotations

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, QEvent
from PySide6.QtWidgets import (
    QFileDialog, QHBoxLayout, QHeaderView, QLabel, QMessageBox,
    QPushButton, QTableView, QTabWidget, QVBoxLayout, QWidget, QDialog,
)

from meteoregik.export.snow_excel import (
    HEADERS, HELP_FROST, HELP_SNOW, coverage_text, export_snow, period_values,
)
from meteoregik.reports.snow_data import SnowData, SnowPeriod, build_snow, station_day_bounds
from meteoregik.ui import theme
from meteoregik.ui.background import run_in_background
from meteoregik.ui.buttons import reserve_focus_width
from meteoregik.ui.dashboard_widgets import HELP_QSS, tooltip_html
from meteoregik.ui.date_field import DateField, DateFieldError
from meteoregik.ui.dialogs import enable_min_max_buttons, minimize_owner_instead
from meteoregik.ui.station_combo import StationCombo
from meteoregik.stations.choices import load_stations_in_table
from meteoregik.db.schema import rp5_readings
from sqlalchemy.engine import Engine


class SnowTableModel(QAbstractTableModel):
    """Готовые данные отчёта; модель не выполняет запросов к БД."""

    def __init__(self, parent: QWidget | None = None, *, seasonal: bool = False) -> None:
        super().__init__(parent)
        self.periods: tuple[SnowPeriod, ...] = ()
        self.seasonal = seasonal

    def set_periods(self, periods: tuple[SnowPeriod, ...]) -> None:
        self.beginResetModel()
        self.periods = tuple(periods)
        self.endResetModel()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.periods)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(HEADERS)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> object:
        if not index.isValid() or not 0 <= index.row() < len(self.periods):
            return None
        period = self.periods[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            value = period_values(period, self.seasonal)[index.column()]
            return f"{value:g}" if isinstance(value, float) else str(value)
        if role == Qt.ItemDataRole.ToolTipRole:
            return tooltip_html(coverage_text(period) + "\n\n" + (HELP_FROST if index.column() == 4 else HELP_SNOW))
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return int(Qt.AlignmentFlag.AlignTop | (Qt.AlignmentFlag.AlignHCenter
                       if index.column() in (1, 2, 4) else Qt.AlignmentFlag.AlignLeft))
        return None


    def headerData(self, section: int, orientation: Qt.Orientation,
                   role: int = Qt.ItemDataRole.DisplayRole) -> object:
        if role == Qt.ItemDataRole.DisplayRole:
            if orientation == Qt.Orientation.Horizontal and 0 <= section < len(HEADERS):
                if section == 4:
                    return "Морозы при\nмалоснежье, дни"
                if section == 5:
                    return "Доступная часть /\nоценка температуры"
                return HEADERS[section]
            return str(section + 1)
        return None



class SnowDialog(QDialog):
    """Станция и период — общий фильтр месячных и сезонных таблиц."""

    def __init__(self, engine: Engine, parent: QWidget | None = None, *, run_async: bool = True) -> None:
        super().__init__(parent)
        self.engine, self._run_async = engine, run_async
        self._data: SnowData | None = None
        self._title = ""
        self._request_id = 0
        enable_min_max_buttons(self)
        self.setWindowTitle("Снежный покров")
        # Windows назначает масштаб монитора при создании native window.
        # Размер задаём после этого, чтобы 125 % не увеличивали его повторно.
        self.winId()
        if parent is not None:
            self.resize(parent.size())
        else:
            self.resize(1100, 750)
        self.setStyleSheet(theme.DIALOG_QSS + HELP_QSS)
        layout = QVBoxLayout(self)
        filters = QHBoxLayout()
        filters.addWidget(QLabel("Метеостанция"))
        self.station_combo = StationCombo()
        self.station_combo.set_choices(load_stations_in_table(engine, rp5_readings))
        filters.addWidget(self.station_combo, 1)
        layout.addLayout(filters)
        period_row = QHBoxLayout()
        period_row.addWidget(QLabel("Период"))
        self.date_from = DateField()
        self.date_to = DateField()
        for label, field in (("с", self.date_from), ("по", self.date_to)):
            period_row.addWidget(QLabel(label))
            period_row.addWidget(field)
        period_row.addStretch()
        self.apply_btn = QPushButton("Применить")
        self.apply_btn.setObjectName("primary")
        self.apply_btn.clicked.connect(self._apply)
        period_row.addWidget(self.apply_btn)
        layout.addLayout(period_row)
        self.title_label = QLabel("Выберите метеостанцию и нажмите «Применить». Пустой период — весь архив rp5.")
        self.title_label.setWordWrap(True)
        layout.addWidget(self.title_label)
        self.note = QLabel("Пропуски высоты внутри архива rp5 приняты за 0. Дни без температуры не оценены для морозов.")
        self.note.setWordWrap(True)
        self.note.setMaximumWidth(680)
        self.note.setToolTip(tooltip_html(HELP_SNOW + "\n\n" + HELP_FROST))
        layout.addWidget(self.note)
        self.period_tabs = QTabWidget()
        self.month_model = SnowTableModel(self)
        self.season_model = SnowTableModel(self, seasonal=True)
        self.month_table = self._table(self.month_model)
        self.season_table = self._table(self.season_model)
        self.period_tabs.addTab(self.month_table, "По месяцам")
        self.period_tabs.addTab(self.season_table, "Зимние сезоны (01.07–30.06)")
        layout.addWidget(self.period_tabs, 1)
        actions = QHBoxLayout()
        self.export_btn = QPushButton("Экспорт в Excel")
        self.export_btn.setEnabled(False)
        self.export_btn.clicked.connect(self._export)
        actions.addWidget(self.export_btn)
        actions.addStretch()
        close = QPushButton("Закрыть")
        close.setObjectName("secondary")
        close.clicked.connect(self.accept)
        actions.addWidget(close)
        layout.addLayout(actions)
        reserve_focus_width(self.apply_btn, self.export_btn, close)

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        minimize_owner_instead(self, event)

    def _table(self, model: SnowTableModel) -> QTableView:
        table = QTableView()
        table.setModel(model)
        table.setWordWrap(True)
        table.setTextElideMode(Qt.TextElideMode.ElideNone)
        table.setAlternatingRowColors(True)
        table.verticalHeader().hide()
        for column, width in enumerate((95, 110, 110, 260, 155, 270)):
            table.setColumnWidth(column, width)
        table.horizontalHeader().setMinimumHeight(42)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        table.horizontalHeader().sectionResized.connect(lambda *_: table.resizeRowsToContents())
        return table

    def _apply(self) -> None:
        try:
            wmo = self.station_combo.selected_wmo()
            start, end = self.date_from.value(), self.date_to.value()
            if (start is None) != (end is None):
                raise ValueError("Заполните обе даты или очистите обе для всего архива.")
            if start is not None and end is not None and start > end:
                raise ValueError("Начало периода позже его конца.")
        except (ValueError, DateFieldError) as exc:
            QMessageBox.warning(self, "Фильтр", str(exc))
            return
        self._request_id += 1
        request = self._request_id
        self.apply_btn.setEnabled(False)
        self.export_btn.setEnabled(False)
        self.title_label.setText("Загружаем снежные показатели…")
        title = self.station_combo.currentText()

        def task() -> SnowData:
            bounds = (start, end) if start is not None and end is not None else station_day_bounds(self.engine, wmo)
            return build_snow(self.engine, wmo, *bounds) if bounds else SnowData(None)

        def done(data: SnowData) -> None:
            if request != self._request_id:
                return
            self.apply_btn.setEnabled(True)
            self.show_data(title, data)

        def failed(exc: BaseException) -> None:
            self.apply_btn.setEnabled(True)
            self.show_data("Не удалось загрузить данные", SnowData(None))
            QMessageBox.warning(self, "Снежный покров", str(exc))

        if self._run_async:
            run_in_background(self, task, done, failed)
        else:
            try:
                done(task())
            except Exception as exc:
                failed(exc)

    def show_data(self, title: str, data: SnowData) -> None:
        self._data, self._title = data, title
        self.title_label.setText(title if data.has_data else title + ": данных rp5 за выбранный период нет.")
        self.month_model.set_periods(data.months)
        self.season_model.set_periods(data.seasons)
        self.month_table.resizeRowsToContents()
        self.season_table.resizeRowsToContents()
        self.export_btn.setEnabled(data.has_data)

    def _export(self) -> None:
        if self._data is None or not self._data.has_data:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Экспорт снежного покрова", "Снежный_покров.xlsx", "Excel (*.xlsx)")
        if not path:
            return
        if not path.lower().endswith(".xlsx"):
            path += ".xlsx"
        data, title = self._data, self._title
        self.export_btn.setEnabled(False)

        def done(_: object) -> None:
            self.export_btn.setEnabled(self._data is not None and self._data.has_data)
            QMessageBox.information(self, "Экспорт", f"Файл сохранён:\n{path}")

        def failed(exc: BaseException) -> None:
            self.export_btn.setEnabled(self._data is not None and self._data.has_data)
            QMessageBox.warning(self, "Экспорт", str(exc))

        run_in_background(self, lambda: export_snow(path, title, data), done, failed)

