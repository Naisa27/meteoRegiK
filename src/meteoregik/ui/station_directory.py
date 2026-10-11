"""Экран "Справочники" (кнопка на главном окне; до 2026-09-30 — "Справочник
станций"): два таба.

(а) "Метеостанции" — фильтры (название, индекс ВМО, страна, регион РФ,
"только без координат"), таблица с правкой прямо в ячейках, добавление
станции и обновление справочника из файла (тот же импортёр, что заливал
исходный справочник, stations/directory_importer.py).
(б) "Регионы РФ" — таблица code/region с правкой названия, добавлением
региона и обновлением из файла.

Удаления нет (решение пользователя 2026-09-25). Проверки и запись — в
Qt-независимом stations/directory.py; здесь только показ и тексты ошибок.

Фильтры применяются сразу при вводе, без кнопки "Применить" (в отличие от
просмотра данных): справочник целиком (~3 тыс. строк) держится в памяти и
фильтруется мгновенно, а поиск станции "по мере набора" — основной сценарий
этого экрана. По той же причине нет постраничного вывода — хватает прокрутки.

Результат правки ячейки (ошибка или "Сохранено") пишется строкой под
таблицей, а не всплывающим окном: так он рядом с тем, что правили, и не
прерывает работу при серии правок.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QPersistentModelIndex,
    QSortFilterProxyModel,
    QStandardPaths,
    Qt,
    Signal,
)
from PySide6.QtGui import QBrush, QColor, QIntValidator
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableView,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy.engine import Engine

from meteoregik.stations.directory import (
    COUNTRY_NAMES,
    RUSSIA,
    DirectoryValidationError,
    RegionRecord,
    StationRecord,
    add_region,
    add_station,
    country_label,
    load_region_records,
    load_station_records,
    update_region_name,
    update_station_field,
)
from meteoregik.stations.directory_importer import (
    DirectoryFormatError,
    DirectoryImportReport,
    import_ru_regions,
    import_stations_directory,
)
from meteoregik.ui import theme
from meteoregik.ui.background import run_in_background
from meteoregik.ui.buttons import reserve_focus_width
from meteoregik.ui.dialogs import ask_yes_no, enable_min_max_buttons, exec_window_modal
from meteoregik.ui.loading_overlay import LoadingOverlay

ALL_COUNTRIES_TEXT = "Все страны"
DEFAULT_COUNTRY = RUSSIA
ALL_REGIONS_TEXT = "Все регионы"
NO_REGION_TEXT = "— не указан —"
WMO_LOCKED_TOOLTIP = "Индекс не меняется: по станции уже загружены данные, они привязаны к ней по индексу."
EXCEL_FILTER = "Excel файлы (*.xlsx *.xls)"


def format_timestamp(value: datetime | None) -> str:
    """updated_at пишется SQLite-функцией CURRENT_TIMESTAMP — это время UTC;
    на экране — местное."""
    if value is None:
        return ""
    return value.replace(tzinfo=timezone.utc).astimezone().strftime("%d.%m.%Y %H:%M")


def format_coordinate(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value:.4f}".rstrip("0").rstrip(".")


def _country_choices(current: str | None = None) -> list[tuple[str, str]]:
    """(код, название) — Россия первой, остальные по алфавиту; код, которого
    нет в списке (например, добавленный вручную), тоже доступен."""
    others = sorted(((code, name) for code, name in COUNTRY_NAMES.items() if code != RUSSIA), key=lambda c: c[1])
    choices = [(RUSSIA, COUNTRY_NAMES[RUSSIA]), *others]
    if current and current not in COUNTRY_NAMES:
        choices.append((current, current))
    return choices


def _region_label(code: int, region: str) -> str:
    return f"{region} ({code})"


# --- метеостанции: модель, фильтр, редакторы ---


class StationsTableModel(QAbstractTableModel):
    """Все станции справочника в памяти; правка ячейки сразу пишется в БД
    через stations/directory.py."""

    error = Signal(str)
    saved = Signal(str)

    COLUMNS: list[tuple[str, str]] = [
        ("wmo_index", "Индекс ВМО"),
        ("name", "Название"),
        ("country", "Страна"),
        ("state", "Регион"),
        ("lat", "Широта"),
        ("lon", "Долгота"),
        ("notes", "Примечание"),
        ("updated_at", "Изменено"),
    ]
    _NUMERIC = {"wmo_index", "lat", "lon"}

    def __init__(self, engine: Engine, parent=None) -> None:
        super().__init__(parent)
        self.engine = engine
        self.records: list[StationRecord] = []
        self.regions: dict[int, str] = {}
        self._secondary = QBrush(QColor(theme.TEXT_SECONDARY))

    def reload(self) -> None:
        self.beginResetModel()
        self.records = load_station_records(self.engine)
        self.regions = {r.code: r.region for r in load_region_records(self.engine)}
        self.endResetModel()

    def record_at(self, row: int) -> StationRecord:
        return self.records[row]

    def field_at(self, column: int) -> str:
        return self.COLUMNS[column][0]

    def rowCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.records)

    def columnCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.COLUMNS)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self.COLUMNS[section][1]
        return None

    def display_text(self, record: StationRecord, field: str) -> str:
        value = getattr(record, field)
        if field == "country":
            return country_label(value)
        if field == "state":
            if record.country == RUSSIA and value is not None:
                return record.region or f"код {value}"
            return value or ""
        if field in ("lat", "lon"):
            return format_coordinate(value)
        if field == "updated_at":
            return format_timestamp(value)
        return "" if value is None else str(value)

    def _wmo_locked(self, record: StationRecord) -> bool:
        return record.has_data

    def data(self, index: QModelIndex | QPersistentModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None
        record = self.records[index.row()]
        field = self.field_at(index.column())
        if role == Qt.ItemDataRole.DisplayRole:
            return self.display_text(record, field)
        if role == Qt.ItemDataRole.EditRole:
            if field in ("lat", "lon"):
                return format_coordinate(getattr(record, field))
            value = getattr(record, field)
            return "" if value is None else str(value)
        if role == Qt.ItemDataRole.TextAlignmentRole and field in self._NUMERIC:
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if field == "wmo_index" and self._wmo_locked(record):
            # Нередактируемый индекс — серым и с объяснением в подсказке:
            # иначе двойной щелчок "ничего не делает" без причины.
            if role == Qt.ItemDataRole.ForegroundRole:
                return self._secondary
            if role == Qt.ItemDataRole.ToolTipRole:
                return WMO_LOCKED_TOOLTIP
        if field == "updated_at" and role == Qt.ItemDataRole.ForegroundRole:
            return self._secondary
        return None

    def flags(self, index: QModelIndex | QPersistentModelIndex) -> Qt.ItemFlag:
        base = Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled
        field = self.field_at(index.column())
        if field == "updated_at":
            return base
        if field == "wmo_index" and self._wmo_locked(self.records[index.row()]):
            return base
        return base | Qt.ItemFlag.ItemIsEditable

    def setData(self, index: QModelIndex | QPersistentModelIndex, value: Any, role: int = Qt.ItemDataRole.EditRole) -> bool:
        if role != Qt.ItemDataRole.EditRole or not index.isValid():
            return False
        record = self.records[index.row()]
        field = self.field_at(index.column())
        try:
            changes = update_station_field(self.engine, record.id, field, value)
        except DirectoryValidationError as exc:
            self.error.emit(str(exc))
            return False
        if not changes:
            return True
        for key, new_value in changes.items():
            setattr(record, key, new_value)
        record.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
        record.region = (
            self.regions.get(int(record.state))
            if record.country == RUSSIA and record.state is not None and record.state.isdigit()
            else None
        )
        self.dataChanged.emit(self.index(index.row(), 0), self.index(index.row(), self.columnCount() - 1))
        self.saved.emit(f"Сохранено: {record.name} ({record.wmo_index}).")
        return True

    def sort_key(self, row: int, column: int) -> tuple[int, Any]:
        """Ключ сортировки: пустые значения — всегда в конце, числа — как
        числа, текст — без учёта регистра."""
        record = self.records[row]
        field = self.field_at(column)
        value = getattr(record, field)
        if value is None or value == "":
            return (1, 0)
        if field in self._NUMERIC or field == "updated_at":
            return (0, value)
        return (0, self.display_text(record, field).casefold())


class StationsFilterProxy(QSortFilterProxyModel):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.name_part = ""
        self.wmo_part = ""
        self.country: str | None = None
        self.region_code: int | None = None
        self.only_missing_coordinates = False

    def set_filters(
        self,
        *,
        name_part: str,
        wmo_part: str,
        country: str | None,
        region_code: int | None,
        only_missing_coordinates: bool,
    ) -> None:
        self.beginFilterChange()
        self.name_part = name_part.strip().casefold()
        self.wmo_part = wmo_part.strip()
        self.country = country
        self.region_code = region_code
        self.only_missing_coordinates = only_missing_coordinates
        self.endFilterChange(QSortFilterProxyModel.Direction.Rows)

    def _source(self) -> StationsTableModel:
        return self.sourceModel()  # type: ignore[return-value]

    def accepts(self, record: StationRecord) -> bool:
        if self.name_part and self.name_part not in record.name.casefold():
            return False
        if self.wmo_part and self.wmo_part not in str(record.wmo_index or ""):
            return False
        if self.country is not None and record.country != self.country:
            return False
        if self.region_code is not None and (record.country != RUSSIA or record.state != str(self.region_code)):
            return False
        if self.only_missing_coordinates and record.has_coordinates:
            return False
        return True

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex | QPersistentModelIndex) -> bool:
        return self.accepts(self._source().record_at(source_row))

    def lessThan(self, left: QModelIndex | QPersistentModelIndex, right: QModelIndex | QPersistentModelIndex) -> bool:
        source = self._source()
        return source.sort_key(left.row(), left.column()) < source.sort_key(right.row(), right.column())


class RegionsSortProxy(QSortFilterProxyModel):
    """Сортировка таблицы регионов по щелчку на заголовке (правка
    2026-10-01): по коду, названию и дате изменения."""

    def lessThan(self, left: QModelIndex | QPersistentModelIndex, right: QModelIndex | QPersistentModelIndex) -> bool:
        source = self.sourceModel()
        return source.sort_key(left.row(), left.column()) < source.sort_key(right.row(), right.column())  # type: ignore[attr-defined]


class StationsDelegate(QStyledItemDelegate):
    """Страна — выбор из списка; регион российской станции — выбор из
    справочника регионов (код вручную не набирается); остальное — обычное
    текстовое поле."""

    def _source(self, index: QModelIndex | QPersistentModelIndex) -> tuple[StationsTableModel, QModelIndex]:
        proxy = index.model()
        source_index = proxy.mapToSource(index)  # type: ignore[attr-defined]
        return proxy.sourceModel(), source_index  # type: ignore[attr-defined]

    def createEditor(self, parent: QWidget, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex) -> QWidget:
        model, source_index = self._source(index)
        record = model.record_at(source_index.row())
        field = model.field_at(source_index.column())
        if field == "country":
            combo = QComboBox(parent)
            for code, name in _country_choices(record.country):
                combo.addItem(name, code)
            return combo
        if field == "state" and record.country == RUSSIA:
            combo = QComboBox(parent)
            combo.addItem(NO_REGION_TEXT, "")
            for code, region in sorted(model.regions.items(), key=lambda item: item[1]):
                combo.addItem(_region_label(code, region), str(code))
            return combo
        return super().createEditor(parent, option, index)

    def setEditorData(self, editor: QWidget, index: QModelIndex | QPersistentModelIndex) -> None:
        if isinstance(editor, QComboBox):
            current = index.data(Qt.ItemDataRole.EditRole) or ""
            position = editor.findData(current)
            editor.setCurrentIndex(max(position, 0))
            return
        super().setEditorData(editor, index)

    def setModelData(self, editor: QWidget, model: Any, index: QModelIndex | QPersistentModelIndex) -> None:
        if isinstance(editor, QComboBox):
            model.setData(index, editor.currentData(), Qt.ItemDataRole.EditRole)
            return
        super().setModelData(editor, model, index)


# --- регионы: модель ---


class RegionsTableModel(QAbstractTableModel):
    error = Signal(str)
    saved = Signal(str)

    COLUMNS = [("code", "Код"), ("region", "Регион"), ("updated_at", "Изменено")]

    def __init__(self, engine: Engine, parent=None) -> None:
        super().__init__(parent)
        self.engine = engine
        self.records: list[RegionRecord] = []
        self._secondary = QBrush(QColor(theme.TEXT_SECONDARY))

    def reload(self) -> None:
        self.beginResetModel()
        self.records = load_region_records(self.engine)
        self.endResetModel()

    def rowCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.records)

    def columnCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.COLUMNS)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self.COLUMNS[section][1]
        return None

    def data(self, index: QModelIndex | QPersistentModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None
        record = self.records[index.row()]
        field = self.COLUMNS[index.column()][0]
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            if field == "updated_at":
                return format_timestamp(record.updated_at)
            return str(getattr(record, field))
        if role == Qt.ItemDataRole.TextAlignmentRole and field == "code":
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if role == Qt.ItemDataRole.ForegroundRole and field != "region":
            return self._secondary
        if role == Qt.ItemDataRole.ToolTipRole and field == "code":
            return "Код не меняется: по нему к региону привязаны станции."
        return None

    def sort_key(self, row: int, column: int) -> tuple:
        """Ключ сортировки по колонке: код — числом (а не строкой, иначе
        "10" < "9"), название — без учёта регистра, "Изменено" — по времени,
        никогда не менявшиеся регионы в конце."""
        record = self.records[row]
        field = self.COLUMNS[column][0]
        if field == "code":
            return (record.code,)
        if field == "region":
            return (record.region.casefold(), record.code)
        return (record.updated_at is None, record.updated_at or datetime.min, record.code)

    def flags(self, index: QModelIndex | QPersistentModelIndex) -> Qt.ItemFlag:
        base = Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled
        if self.COLUMNS[index.column()][0] == "region":
            return base | Qt.ItemFlag.ItemIsEditable
        return base

    def setData(self, index: QModelIndex | QPersistentModelIndex, value: Any, role: int = Qt.ItemDataRole.EditRole) -> bool:
        if role != Qt.ItemDataRole.EditRole or not index.isValid():
            return False
        record = self.records[index.row()]
        try:
            changed = update_region_name(self.engine, record.code, value)
        except DirectoryValidationError as exc:
            self.error.emit(str(exc))
            return False
        if changed:
            record.region = str(value).strip()
            record.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
            self.dataChanged.emit(self.index(index.row(), 0), self.index(index.row(), self.columnCount() - 1))
            self.saved.emit(f"Сохранено: {record.region} ({record.code}).")
        return True


# --- формы добавления ---


class _FormDialog(QDialog):
    """Общая основа форм добавления: поля формы, строка ошибки под ними,
    "Отмена"/"Добавить". Ошибка — текстом рядом с полями, окно не
    закрывается, введённое не теряется."""

    def __init__(self, title: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setStyleSheet(theme.DIALOG_QSS)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(20, 20, 20, 20)
        self._layout.setSpacing(12)
        self.form = QFormLayout()
        self.form.setHorizontalSpacing(12)
        self.form.setVerticalSpacing(10)
        self._layout.addLayout(self.form)

        self.error_label = QLabel("")
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet(f"color: {theme.ERROR_TEXT};")
        self.error_label.hide()
        self._layout.addWidget(self.error_label)

    def _finish(self) -> None:
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        cancel_btn = QPushButton("Отмена")
        cancel_btn.setObjectName("secondary")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        ok_btn = QPushButton("Добавить")
        ok_btn.setObjectName("primary")
        ok_btn.setDefault(True)
        ok_btn.clicked.connect(self._try_accept)
        btn_row.addWidget(ok_btn)
        self._layout.addLayout(btn_row)
        reserve_focus_width(cancel_btn, ok_btn)

    def show_error(self, text: str) -> None:
        self.error_label.setText(text)
        self.error_label.show()

    def _try_accept(self) -> None:
        raise NotImplementedError


class AddStationDialog(_FormDialog):
    def __init__(self, engine: Engine, regions: dict[int, str], parent=None) -> None:
        super().__init__("Новая станция", parent)
        self.engine = engine
        self.added_wmo: int | None = None

        self.wmo_edit = QLineEdit()
        self.wmo_edit.setValidator(QIntValidator(1, 99999, self))
        self.wmo_edit.setPlaceholderText("пятизначное число, например 27704")
        self.form.addRow("Индекс ВМО *", self.wmo_edit)

        self.name_edit = QLineEdit()
        self.form.addRow("Название *", self.name_edit)

        self.country_combo = QComboBox()
        for code, name in _country_choices():
            self.country_combo.addItem(name, code)
        self.country_combo.currentIndexChanged.connect(self._on_country_changed)
        self.form.addRow("Страна *", self.country_combo)

        # Регион: для России — список регионов, для других стран — свой текст.
        self.region_stack = QStackedWidget()
        self.region_combo = QComboBox()
        self.region_combo.addItem(NO_REGION_TEXT, "")
        for code, region in sorted(regions.items(), key=lambda item: item[1]):
            self.region_combo.addItem(_region_label(code, region), str(code))
        self.region_stack.addWidget(self.region_combo)
        self.region_edit = QLineEdit()
        self.region_edit.setPlaceholderText("необязательно")
        self.region_stack.addWidget(self.region_edit)
        self.form.addRow("Регион", self.region_stack)

        self.lat_edit = QLineEdit()
        self.lat_edit.setPlaceholderText("например 54.08")
        self.form.addRow("Широта", self.lat_edit)
        self.lon_edit = QLineEdit()
        self.lon_edit.setPlaceholderText("например 35.35")
        self.form.addRow("Долгота", self.lon_edit)
        self.notes_edit = QLineEdit()
        self.form.addRow("Примечание", self.notes_edit)

        hint = QLabel("* — обязательные поля")
        hint.setStyleSheet(f"color: {theme.TEXT_SECONDARY};")
        self._layout.insertWidget(0, hint)
        self._finish()
        self.resize(460, self.sizeHint().height())

    def _on_country_changed(self) -> None:
        is_russia = self.country_combo.currentData() == RUSSIA
        self.region_stack.setCurrentWidget(self.region_combo if is_russia else self.region_edit)

    def _state_value(self) -> str:
        if self.country_combo.currentData() == RUSSIA:
            return self.region_combo.currentData() or ""
        return self.region_edit.text()

    def _try_accept(self) -> None:
        try:
            add_station(
                self.engine,
                wmo_index=self.wmo_edit.text(),
                name=self.name_edit.text(),
                country=self.country_combo.currentData(),
                state=self._state_value(),
                lat=self.lat_edit.text(),
                lon=self.lon_edit.text(),
                notes=self.notes_edit.text(),
            )
        except DirectoryValidationError as exc:
            self.show_error(str(exc))
            return
        self.added_wmo = int(self.wmo_edit.text())
        self.accept()


class AddRegionDialog(_FormDialog):
    def __init__(self, engine: Engine, parent=None) -> None:
        super().__init__("Новый регион", parent)
        self.engine = engine
        self.added_code: int | None = None
        self.code_edit = QLineEdit()
        self.code_edit.setValidator(QIntValidator(0, 9999, self))
        self.form.addRow("Код *", self.code_edit)
        self.region_edit = QLineEdit()
        self.form.addRow("Название *", self.region_edit)
        self._finish()
        self.resize(420, self.sizeHint().height())

    def _try_accept(self) -> None:
        try:
            self.added_code = add_region(self.engine, code=self.code_edit.text(), region=self.region_edit.text())
        except DirectoryValidationError as exc:
            self.show_error(str(exc))
            return
        self.accept()


# --- экран ---


def _status_label() -> QLabel:
    label = QLabel("")
    label.setWordWrap(True)
    return label


def _configure_table(table: QTableView, stretch_column: int) -> None:
    """Свободная ширина уходит в колонку с самым длинным текстом, а не в
    последнюю ("Изменено" — короткая дата)."""
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    table.setAlternatingRowColors(True)
    table.verticalHeader().hide()
    header = table.horizontalHeader()
    header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    header.setSectionResizeMode(stretch_column, QHeaderView.ResizeMode.Stretch)


class StationDirectoryDialog(QDialog):
    def __init__(self, engine: Engine, parent=None, run_async: bool = True) -> None:
        super().__init__(parent)
        self.engine = engine
        self._run_async = run_async
        # Внутри два справочника — станции и регионы РФ (правка 2026-09-30).
        self.setWindowTitle("Справочники")
        enable_min_max_buttons(self)
        if parent is not None:
            self.resize(parent.size())
        else:
            self.resize(1100, 750)
        self.setStyleSheet(theme.DIALOG_QSS)

        self.stations_model = StationsTableModel(engine, self)
        self.stations_proxy = StationsFilterProxy(self)
        self.stations_proxy.setSourceModel(self.stations_model)
        self.regions_model = RegionsTableModel(engine, self)
        self.regions_proxy = RegionsSortProxy(self)
        self.regions_proxy.setSourceModel(self.regions_model)

        self._build_ui()
        self._reload_all()

    # --- построение интерфейса ---

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_stations_tab(), "Метеостанции")
        self.tabs.addTab(self._build_regions_tab(), "Регионы РФ")

        # Лоадер на время обновления из файла — поверх вкладок.
        area = QWidget()
        area_layout = QGridLayout(area)
        area_layout.setContentsMargins(0, 0, 0, 0)
        area_layout.addWidget(self.tabs, 0, 0)
        self.loading_overlay = LoadingOverlay("Обновление справочника…")
        area_layout.addWidget(self.loading_overlay, 0, 0)
        layout.addWidget(area, stretch=1)

        close_row = QHBoxLayout()
        close_row.addStretch(1)
        close_btn = QPushButton("Закрыть")
        close_btn.setObjectName("secondary")
        close_btn.clicked.connect(self.accept)
        close_row.addWidget(close_btn)
        layout.addLayout(close_row)

        reserve_focus_width(*self.findChildren(QPushButton))

    def _build_stations_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)
        layout.addWidget(self._build_filter_card())

        hint = QLabel(
            "Дважды щёлкните по ячейке, чтобы изменить значение. Индекс ВМО меняется только "
            "у станций, по которым ещё нет загруженных данных."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {theme.TEXT_SECONDARY};")
        layout.addWidget(hint)

        self.stations_table = QTableView()
        self.stations_table.setModel(self.stations_proxy)
        self.stations_table.setItemDelegate(StationsDelegate(self.stations_table))
        self.stations_table.setSortingEnabled(True)
        # По умолчанию Qt сортирует по первой колонке по убыванию — а
        # станцию ищут по названию, поэтому по алфавиту.
        self.stations_table.sortByColumn(1, Qt.SortOrder.AscendingOrder)
        _configure_table(self.stations_table, stretch_column=6)  # "Примечание"
        layout.addWidget(self.stations_table, stretch=1)

        self.stations_status = _status_label()
        layout.addWidget(self.stations_status)
        self.stations_model.error.connect(lambda text: self._show_status(self.stations_status, text, error=True))
        self.stations_model.saved.connect(lambda text: self._show_status(self.stations_status, text))

        toolbar = QHBoxLayout()
        self.add_station_btn = QPushButton("Добавить станцию")
        self.add_station_btn.setObjectName("add")
        self.add_station_btn.clicked.connect(self._add_station)
        toolbar.addWidget(self.add_station_btn)
        self.import_stations_btn = QPushButton("Обновить из файла…")
        self.import_stations_btn.setObjectName("skySoft")
        self.import_stations_btn.clicked.connect(self._import_stations)
        toolbar.addWidget(self.import_stations_btn)
        toolbar.addStretch(1)
        self.count_label = QLabel("")
        self.count_label.setStyleSheet(f"color: {theme.TEXT_SECONDARY};")
        toolbar.addWidget(self.count_label)
        layout.addLayout(toolbar)
        return tab

    def _build_filter_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("card")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(16, 12, 16, 12)
        card_layout.setSpacing(10)

        top_row = QHBoxLayout()
        top_row.setSpacing(10)
        top_row.addWidget(QLabel("Название:"))
        self.name_filter = QLineEdit()
        self.name_filter.setPlaceholderText("часть названия")
        self.name_filter.setClearButtonEnabled(True)
        top_row.addWidget(self.name_filter, stretch=2)
        top_row.addWidget(QLabel("Индекс ВМО:"))
        self.wmo_filter = QLineEdit()
        self.wmo_filter.setPlaceholderText("цифры индекса")
        self.wmo_filter.setValidator(QIntValidator(0, 99999, self))
        self.wmo_filter.setClearButtonEnabled(True)
        self.wmo_filter.setMaximumWidth(160)
        top_row.addWidget(self.wmo_filter)
        top_row.addWidget(QLabel("Страна:"))
        self.country_filter = QComboBox()
        self.country_filter.addItem(ALL_COUNTRIES_TEXT, None)
        for code, name in _country_choices():
            self.country_filter.addItem(name, code)
        # При открытии — только станции России (правка 2026-09-30): с ними
        # работают чаще всего, остальные страны — выбором в этом же фильтре.
        self.country_filter.setCurrentIndex(self.country_filter.findData(DEFAULT_COUNTRY))
        top_row.addWidget(self.country_filter)
        card_layout.addLayout(top_row)

        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(10)
        bottom_row.addWidget(QLabel("Регион РФ:"))
        self.region_filter = QComboBox()
        self.region_filter.setMinimumWidth(280)
        bottom_row.addWidget(self.region_filter)
        self.missing_coordinates_check = QCheckBox("Только без координат")
        bottom_row.addWidget(self.missing_coordinates_check)
        bottom_row.addStretch(1)
        self.reset_filters_btn = QPushButton("Сбросить фильтры")
        self.reset_filters_btn.setObjectName("secondary")
        # lambda: clicked передаёт checked=False, он попал бы в country
        self.reset_filters_btn.clicked.connect(lambda: self._reset_filters())
        bottom_row.addWidget(self.reset_filters_btn)
        card_layout.addLayout(bottom_row)

        self.name_filter.textChanged.connect(self._apply_filters)
        self.wmo_filter.textChanged.connect(self._apply_filters)
        self.country_filter.currentIndexChanged.connect(self._on_country_filter_changed)
        self.region_filter.currentIndexChanged.connect(self._on_region_filter_changed)
        self.missing_coordinates_check.toggled.connect(self._apply_filters)
        return card

    def _build_regions_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        hint = QLabel(
            "Дважды щёлкните по названию региона, чтобы изменить его. Код не меняется: "
            "по нему к региону привязаны станции. Щелчок по заголовку столбца — сортировка."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {theme.TEXT_SECONDARY};")
        layout.addWidget(hint)

        self.regions_table = QTableView()
        self.regions_table.setModel(self.regions_proxy)
        self.regions_table.setSortingEnabled(True)
        self.regions_table.sortByColumn(0, Qt.SortOrder.AscendingOrder)  # по коду, как в файле
        _configure_table(self.regions_table, stretch_column=1)  # "Регион"
        layout.addWidget(self.regions_table, stretch=1)

        self.regions_status = _status_label()
        layout.addWidget(self.regions_status)
        self.regions_model.error.connect(lambda text: self._show_status(self.regions_status, text, error=True))
        self.regions_model.saved.connect(lambda text: self._show_status(self.regions_status, text))

        toolbar = QHBoxLayout()
        self.add_region_btn = QPushButton("Добавить регион")
        self.add_region_btn.setObjectName("add")
        self.add_region_btn.clicked.connect(self._add_region)
        toolbar.addWidget(self.add_region_btn)
        self.import_regions_btn = QPushButton("Обновить из файла…")
        self.import_regions_btn.setObjectName("skySoft")
        self.import_regions_btn.clicked.connect(self._import_regions)
        toolbar.addWidget(self.import_regions_btn)
        toolbar.addStretch(1)
        layout.addLayout(toolbar)
        return tab

    # --- данные ---

    def _reload_all(self) -> None:
        self.stations_model.reload()
        self.regions_model.reload()
        self._reload_region_filter()
        self._apply_filters()
        self._fit_columns()

    def _reload_region_filter(self) -> None:
        current = self.region_filter.currentData()
        self.region_filter.blockSignals(True)
        self.region_filter.clear()
        self.region_filter.addItem(ALL_REGIONS_TEXT, None)
        for code, region in sorted(self.stations_model.regions.items(), key=lambda item: item[1]):
            self.region_filter.addItem(_region_label(code, region), code)
        position = self.region_filter.findData(current)
        self.region_filter.setCurrentIndex(max(position, 0))
        self.region_filter.blockSignals(False)

    def _fit_columns(self) -> None:
        for table in (self.stations_table, self.regions_table):
            table.resizeColumnsToContents()
            header = table.horizontalHeader()
            for section in range(header.count()):
                header.resizeSection(section, min(max(header.sectionSize(section) + 16, 80), 320))

    # --- фильтры ---

    def _on_country_filter_changed(self) -> None:
        # Регион РФ имеет смысл только для России: при выборе другой страны
        # фильтр региона сбрасывается и недоступен.
        country = self.country_filter.currentData()
        allow_region = country in (None, RUSSIA)
        if not allow_region and self.region_filter.currentData() is not None:
            self.region_filter.blockSignals(True)
            self.region_filter.setCurrentIndex(0)
            self.region_filter.blockSignals(False)
        self.region_filter.setEnabled(allow_region)
        self._apply_filters()

    def _on_region_filter_changed(self) -> None:
        # Выбран регион — значит, страна Россия: показать это и в фильтре страны.
        if self.region_filter.currentData() is not None and self.country_filter.currentData() is None:
            self.country_filter.blockSignals(True)
            self.country_filter.setCurrentIndex(self.country_filter.findData(RUSSIA))
            self.country_filter.blockSignals(False)
        self._apply_filters()

    def _apply_filters(self) -> None:
        self.stations_proxy.set_filters(
            name_part=self.name_filter.text(),
            wmo_part=self.wmo_filter.text(),
            country=self.country_filter.currentData(),
            region_code=self.region_filter.currentData(),
            only_missing_coordinates=self.missing_coordinates_check.isChecked(),
        )
        self._refresh_counts()

    def _reset_filters(self, country: str | None = DEFAULT_COUNTRY) -> None:
        """Возврат к фильтрам как при открытии экрана (только Россия);
        country=None — показать вообще все станции."""
        for widget in (self.name_filter, self.wmo_filter, self.country_filter, self.region_filter, self.missing_coordinates_check):
            widget.blockSignals(True)
        self.name_filter.clear()
        self.wmo_filter.clear()
        self.country_filter.setCurrentIndex(max(self.country_filter.findData(country), 0))
        self.region_filter.setCurrentIndex(0)
        self.region_filter.setEnabled(True)
        self.missing_coordinates_check.setChecked(False)
        for widget in (self.name_filter, self.wmo_filter, self.country_filter, self.region_filter, self.missing_coordinates_check):
            widget.blockSignals(False)
        self._apply_filters()

    def _refresh_counts(self) -> None:
        total = self.stations_model.rowCount()
        shown = self.stations_proxy.rowCount()
        self.count_label.setText(f"Показано {shown} из {total}")
        missing = sum(1 for record in self.stations_model.records if not record.has_coordinates)
        self.missing_coordinates_check.setText(f"Только без координат ({missing})")

    # --- статус под таблицей ---

    @staticmethod
    def _show_status(label: QLabel, text: str, error: bool = False) -> None:
        color = theme.ERROR_TEXT if error else theme.SUCCESS
        label.setStyleSheet(f"color: {color};")
        label.setText(text)

    # --- добавление ---

    def _add_station(self) -> None:
        dialog = AddStationDialog(self.engine, self.stations_model.regions, self)
        if exec_window_modal(dialog) != QDialog.DialogCode.Accepted or dialog.added_wmo is None:
            return
        self.stations_model.reload()
        self._apply_filters()
        self._select_station(dialog.added_wmo)
        self._show_status(self.stations_status, f"Станция {dialog.added_wmo} добавлена.")

    def _select_station(self, wmo_index: int) -> None:
        """Показывает добавленную станцию: если фильтры её скрывают — они
        сбрасываются, иначе пользователь не увидел бы результат."""
        row = next((i for i, r in enumerate(self.stations_model.records) if r.wmo_index == wmo_index), None)
        if row is None:
            return
        proxy_index = self.stations_proxy.mapFromSource(self.stations_model.index(row, 0))
        if not proxy_index.isValid():
            # Все страны: добавленная станция может быть и не российской.
            self._reset_filters(country=None)
            proxy_index = self.stations_proxy.mapFromSource(self.stations_model.index(row, 0))
        self.stations_table.selectRow(proxy_index.row())
        self.stations_table.scrollTo(proxy_index)

    def _add_region(self) -> None:
        dialog = AddRegionDialog(self.engine, self)
        if exec_window_modal(dialog) != QDialog.DialogCode.Accepted or dialog.added_code is None:
            return
        self._reload_all()
        row = next(i for i, r in enumerate(self.regions_model.records) if r.code == dialog.added_code)
        proxy_index = self.regions_proxy.mapFromSource(self.regions_model.index(row, 0))
        self.regions_table.selectRow(proxy_index.row())
        self.regions_table.scrollTo(proxy_index)
        self._show_status(self.regions_status, f"Регион {dialog.added_code} добавлен.")

    # --- обновление из файла ---

    def _import_stations(self) -> None:
        self._import_from_file(
            what="справочник станций",
            confirm=(
                "Станции из файла сопоставляются по индексу ВМО: совпавшие обновятся "
                "(название, страна, регион, координаты), новые добавятся. Станции, которых "
                "нет в файле, и примечания останутся без изменений. Продолжить?"
            ),
            columns="wmo, name, country, state, lat, lon",
            importer=import_stations_directory,
            status=self.stations_status,
        )

    def _import_regions(self) -> None:
        self._import_from_file(
            what="справочник регионов",
            confirm=(
                "Регионы из файла сопоставляются по коду: совпавшие получат название из "
                "файла, новые добавятся. Регионы, которых нет в файле, останутся. Продолжить?"
            ),
            columns="code, region",
            importer=import_ru_regions,
            status=self.regions_status,
        )

    def _import_from_file(
        self,
        *,
        what: str,
        confirm: str,
        columns: str,
        importer: Callable[[Engine, str], DirectoryImportReport],
        status: QLabel,
    ) -> None:
        downloads = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DownloadLocation)
        path, _ = QFileDialog.getOpenFileName(
            self, f"Обновить {what} из файла (столбцы: {columns})", downloads, EXCEL_FILTER
        )
        if not path:
            return
        if not ask_yes_no(self, "Обновление справочника", confirm):
            return

        engine = self.engine
        self.loading_overlay.show()
        self.loading_overlay.raise_()

        def on_done(report: DirectoryImportReport) -> None:
            self.loading_overlay.hide()
            self._reload_all()
            self._show_status(
                status,
                f"Файл «{Path(path).name}»: добавлено {report.inserted}, обновлено {report.updated}.",
            )

        def on_error(exc: BaseException) -> None:
            self.loading_overlay.hide()
            if isinstance(exc, DirectoryFormatError):
                QMessageBox.warning(self, "Обновление справочника", f"{exc}\n\nСправочник не изменён.")
            else:
                QMessageBox.warning(
                    self, "Обновление справочника", f"Не удалось прочитать файл: {exc}\n\nСправочник не изменён."
                )

        task = lambda: importer(engine, path)  # noqa: E731
        if self._run_async:
            run_in_background(self, task, on_done, on_error)
            return
        try:
            report = task()
        except Exception as exc:  # noqa: BLE001 — тот же путь, что у фонового режима
            on_error(exc)
        else:
            on_done(report)
