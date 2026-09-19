"""Экран "Просмотр данных": таблица с пагинацией, фильтр по станции
(wmo_index)/формату(rp5|ГГ)/периоду, inline-редактирование ячеек,
добавление/удаление строк -> триггерит calculations/recompute.py для
затронутых дат (см. plan.md, "Экраны UI", п.2).

Редактируемы только сами значения наблюдений (дата/время у rp5 и параметры) —
wmo_index/station_name/source_file только для чтения: смена станции у уже
залитой строки и правка кэшированного имени — вне сценария этого экрана
(имя станции правится в будущем "Справочнике станций").

Пересчёт после правки/удаления запускается БЕЗ conflict_resolver (новое
значение всегда побеждает) — это прямая правка пользователем, а не
расхождение источников при импорте (см. calculations/recompute.py),
переспрашивать здесь нечего."""

from __future__ import annotations

from datetime import date, datetime
from datetime import time as dt_time

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QTableView,
    QVBoxLayout,
)
from sqlalchemy import Date, Float, Integer, Time, delete, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from meteoregik.calculations.recompute import recompute_range
from meteoregik.db.schema import RP5_PARAMETER_COLUMNS, gg_readings, rp5_readings
from meteoregik.importers.rp5_importer import RP5_COLUMN_MAP
from meteoregik.ui import theme

PAGE_SIZE = 100

_INVALID = object()

# db-имя -> исходный код столбца rp5.ru (для заголовков таблицы), напр.
# "T_temperature" -> "T", "E2_soil_state_snow" -> "E'".
_RP5_HEADER_BY_COLUMN = {db_col: src_col for src_col, db_col in RP5_COLUMN_MAP.items()}


def _rp5_columns() -> list[tuple[str, str, bool]]:
    columns: list[tuple[str, str, bool]] = [
        ("wmo_index", "WMO", False),
        ("station_name", "Станция", False),
        ("obs_date", "Дата", True),
        ("obs_time", "Время", True),
    ]
    columns += [
        (name, _RP5_HEADER_BY_COLUMN.get(name, name), True) for name, _ in RP5_PARAMETER_COLUMNS
    ]
    columns.append(("source_file", "Файл", False))
    return columns


RP5_COLUMNS = _rp5_columns()
RP5_TEMP_COLUMNS = {"T_temperature"}

GG_COLUMNS: list[tuple[str, str, bool]] = [
    ("wmo_index", "WMO", False),
    ("station_name", "Станция", False),
    ("obs_date", "Дата", True),
    ("t_avg", "T ср.", True),
    ("t_min", "T мин.", True),
    ("t_max", "T макс.", True),
    ("rrr_precipitation", "RRR", True),
    ("source_file", "Файл", False),
]
GG_TEMP_COLUMNS = {"t_avg", "t_min", "t_max"}


def _parse_cell_value(col_type, raw_value):
    text = raw_value.strip() if isinstance(raw_value, str) else raw_value
    if text in (None, ""):
        return None
    if isinstance(col_type, Float):
        return float(str(text).replace(",", "."))
    if isinstance(col_type, Integer):
        return int(text)
    if isinstance(col_type, Date):
        return text if isinstance(text, date) else datetime.strptime(str(text), "%Y-%m-%d").date()
    if isinstance(col_type, Time):
        if isinstance(text, dt_time):
            return text
        fmt = "%H:%M:%S" if str(text).count(":") == 2 else "%H:%M"
        return datetime.strptime(str(text), fmt).time()
    return str(text)


class ReadingsTableModel(QAbstractTableModel):
    """Одна страница строк rp5_readings/gg_readings с фильтрами и inline-
    редактированием. reload() каждый раз перечитывает текущую страницу из
    БД — таблицы большие (десятки тысяч строк), в памяти держим только
    PAGE_SIZE строк."""

    def __init__(self, engine: Engine, table, columns, temp_columns: set[str], parent=None) -> None:
        super().__init__(parent)
        self.engine = engine
        self.table = table
        self.columns = columns
        self.temp_columns = temp_columns
        self.rows: list[dict] = []
        self.total_count = 0
        self.page = 0
        self.wmo_filter: int | None = None
        self.date_from: date | None = None
        self.date_to: date | None = None

    def set_filters(self, wmo_filter: int | None, date_from: date | None, date_to: date | None) -> None:
        self.wmo_filter = wmo_filter
        self.date_from = date_from
        self.date_to = date_to
        self.page = 0
        self.reload()

    def _where_clauses(self):
        clauses = []
        if self.wmo_filter is not None:
            clauses.append(self.table.c.wmo_index == self.wmo_filter)
        if self.date_from is not None:
            clauses.append(self.table.c.obs_date >= self.date_from)
        if self.date_to is not None:
            clauses.append(self.table.c.obs_date <= self.date_to)
        return clauses

    def reload(self) -> None:
        self.beginResetModel()
        with self.engine.connect() as conn:
            clauses = self._where_clauses()

            count_stmt = select(func.count()).select_from(self.table)
            for clause in clauses:
                count_stmt = count_stmt.where(clause)
            self.total_count = conn.execute(count_stmt).scalar_one()

            select_cols = [self.table.c.id] + [self.table.c[name] for name, _, _ in self.columns]
            stmt = select(*select_cols)
            for clause in clauses:
                stmt = stmt.where(clause)
            order_cols = [self.table.c.obs_date]
            if "obs_time" in self.table.c:
                order_cols.append(self.table.c.obs_time)
            order_cols.append(self.table.c.id)
            stmt = stmt.order_by(*order_cols).limit(PAGE_SIZE).offset(self.page * PAGE_SIZE)
            self.rows = [dict(r) for r in conn.execute(stmt).mappings().all()]
        self.endResetModel()

    @property
    def total_pages(self) -> int:
        if self.total_count == 0:
            return 1
        return (self.total_count - 1) // PAGE_SIZE + 1

    def go_to_page(self, page: int) -> None:
        page = max(0, min(page, self.total_pages - 1))
        if page != self.page:
            self.page = page
            self.reload()

    # --- интерфейс QAbstractTableModel ---

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.columns)

    def headerData(self, section: int, orientation: Qt.Orientation, role=Qt.ItemDataRole.DisplayRole):
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal:
            return self.columns[section][1]
        return str(self.page * PAGE_SIZE + section + 1)

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or role not in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            return None
        value = self.rows[index.row()].get(self.columns[index.column()][0])
        return "" if value is None else str(value)

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        base = Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled
        if self.columns[index.column()][2]:
            base |= Qt.ItemFlag.ItemIsEditable
        return base

    def setData(self, index: QModelIndex, value, role=Qt.ItemDataRole.EditRole) -> bool:
        if role != Qt.ItemDataRole.EditRole or not index.isValid():
            return False
        col_name = self.columns[index.column()][0]
        row = self.rows[index.row()]

        try:
            parsed = _parse_cell_value(self.table.c[col_name].type, value)
        except ValueError:
            return False

        old_date = row.get("obs_date")
        try:
            with self.engine.begin() as conn:
                conn.execute(
                    self.table.update()
                    .where(self.table.c.id == row["id"])
                    .values(**{col_name: parsed, "updated_at": func.current_timestamp()})
                )
        except IntegrityError:
            return False

        row[col_name] = parsed
        self.dataChanged.emit(index, index)

        if col_name in self.temp_columns:
            recompute_range(self.engine, row["wmo_index"], row["obs_date"], row["obs_date"])
        elif col_name == "obs_date" and old_date is not None and old_date != row["obs_date"]:
            recompute_range(self.engine, row["wmo_index"], old_date, old_date)
            recompute_range(self.engine, row["wmo_index"], row["obs_date"], row["obs_date"])

        return True

    # --- добавление/удаление ---

    def insert_row(self, values: dict) -> None:
        with self.engine.begin() as conn:
            conn.execute(self.table.insert().values(**values))
        self.reload()

    def delete_row(self, row_index: int) -> None:
        row = self.rows[row_index]
        with self.engine.begin() as conn:
            conn.execute(delete(self.table).where(self.table.c.id == row["id"]))
        recompute_range(self.engine, row["wmo_index"], row["obs_date"], row["obs_date"])
        self.reload()


class _AddRowDialog(QDialog):
    """Мини-форма для новой строки — только ключевые поля (wmo_index,
    дата[, время]); остальные параметры пользователь заполнит потом inline
    в таблице."""

    def __init__(self, is_rp5: bool, parent=None) -> None:
        super().__init__(parent)
        self.is_rp5 = is_rp5
        self.result_values: dict | None = None
        self.setWindowTitle("Новая строка")
        self.setStyleSheet(theme.DIALOG_QSS)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)

        layout.addWidget(QLabel("Индекс ВМО:"))
        self.wmo_edit = QLineEdit()
        layout.addWidget(self.wmo_edit)

        layout.addWidget(QLabel("Дата (ГГГГ-ММ-ДД):"))
        self.date_edit = QLineEdit()
        self.date_edit.setPlaceholderText("2020-01-31")
        layout.addWidget(self.date_edit)

        if self.is_rp5:
            layout.addWidget(QLabel("Время (ЧЧ:ММ):"))
            self.time_edit = QLineEdit()
            self.time_edit.setPlaceholderText("09:00")
            layout.addWidget(self.time_edit)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        cancel_btn = QPushButton("Отмена")
        cancel_btn.setObjectName("secondary")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        ok_btn = QPushButton("Добавить")
        ok_btn.setObjectName("primary")
        ok_btn.clicked.connect(self._try_accept)
        btn_row.addWidget(ok_btn)
        layout.addLayout(btn_row)

    def _try_accept(self) -> None:
        try:
            wmo_index = int(self.wmo_edit.text().strip())
            obs_date = datetime.strptime(self.date_edit.text().strip(), "%Y-%m-%d").date()
            values = {"wmo_index": wmo_index, "obs_date": obs_date}
            if self.is_rp5:
                values["obs_time"] = datetime.strptime(self.time_edit.text().strip(), "%H:%M").time()
        except ValueError:
            hint = "ВМО — число, дата — ГГГГ-ММ-ДД" + (", время — ЧЧ:ММ" if self.is_rp5 else "")
            QMessageBox.warning(self, "Новая строка", f"Проверьте формат полей: {hint}.")
            return
        self.result_values = values
        self.accept()


def _parse_optional_date(text: str):
    text = text.strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return _INVALID


class DataBrowserDialog(QDialog):
    def __init__(self, engine: Engine, parent=None) -> None:
        super().__init__(parent)
        self.engine = engine
        self.setWindowTitle("Просмотр данных")
        self.resize(1000, 620)
        self.setStyleSheet(theme.DIALOG_QSS)

        self._model = ReadingsTableModel(engine, rp5_readings, RP5_COLUMNS, RP5_TEMP_COLUMNS)
        self._build_ui()
        self._model.reload()
        self._refresh_pagination_label()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        layout.addWidget(self._build_filter_card())

        self.table = QTableView()
        self.table.setModel(self._model)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        layout.addWidget(self.table, stretch=1)

        toolbar = QHBoxLayout()
        add_btn = QPushButton("Добавить строку")
        add_btn.setObjectName("secondary")
        add_btn.clicked.connect(self._add_row)
        toolbar.addWidget(add_btn)
        delete_btn = QPushButton("Удалить строку")
        delete_btn.setObjectName("secondary")
        delete_btn.clicked.connect(self._delete_row)
        toolbar.addWidget(delete_btn)
        toolbar.addStretch(1)

        prev_btn = QPushButton("◀ Пред.")
        prev_btn.setObjectName("secondary")
        prev_btn.clicked.connect(self._prev_page)
        toolbar.addWidget(prev_btn)
        self.page_label = QLabel("")
        toolbar.addWidget(self.page_label)
        next_btn = QPushButton("След. ▶")
        next_btn.setObjectName("secondary")
        next_btn.clicked.connect(self._next_page)
        toolbar.addWidget(next_btn)
        layout.addLayout(toolbar)

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
        card_layout = QHBoxLayout(card)
        card_layout.setContentsMargins(16, 12, 16, 12)
        card_layout.setSpacing(10)

        self.rp5_radio = QRadioButton("rp5")
        self.gg_radio = QRadioButton("ГГ")
        self.rp5_radio.setChecked(True)
        self._format_group = QButtonGroup(self)
        self._format_group.addButton(self.rp5_radio)
        self._format_group.addButton(self.gg_radio)
        self._format_group.buttonClicked.connect(self._on_format_changed)
        card_layout.addWidget(QLabel("Формат:"))
        card_layout.addWidget(self.rp5_radio)
        card_layout.addWidget(self.gg_radio)

        card_layout.addWidget(QLabel("ВМО:"))
        self.wmo_filter_edit = QLineEdit()
        self.wmo_filter_edit.setFixedWidth(80)
        card_layout.addWidget(self.wmo_filter_edit)

        card_layout.addWidget(QLabel("с:"))
        self.date_from_edit = QLineEdit()
        self.date_from_edit.setPlaceholderText("ГГГГ-ММ-ДД")
        self.date_from_edit.setFixedWidth(100)
        card_layout.addWidget(self.date_from_edit)

        card_layout.addWidget(QLabel("по:"))
        self.date_to_edit = QLineEdit()
        self.date_to_edit.setPlaceholderText("ГГГГ-ММ-ДД")
        self.date_to_edit.setFixedWidth(100)
        card_layout.addWidget(self.date_to_edit)

        apply_btn = QPushButton("Показать")
        apply_btn.setObjectName("primary")
        apply_btn.clicked.connect(self._apply_filters)
        card_layout.addWidget(apply_btn)
        card_layout.addStretch(1)

        return card

    def _on_format_changed(self) -> None:
        is_rp5 = self.rp5_radio.isChecked()
        table = rp5_readings if is_rp5 else gg_readings
        columns = RP5_COLUMNS if is_rp5 else GG_COLUMNS
        temp_columns = RP5_TEMP_COLUMNS if is_rp5 else GG_TEMP_COLUMNS
        self._model = ReadingsTableModel(self.engine, table, columns, temp_columns)
        self.table.setModel(self._model)
        self._apply_filters()

    def _apply_filters(self) -> None:
        wmo_text = self.wmo_filter_edit.text().strip()
        try:
            wmo_filter = int(wmo_text) if wmo_text else None
        except ValueError:
            QMessageBox.warning(self, "Фильтр", "Индекс ВМО должен быть числом.")
            return

        date_from = _parse_optional_date(self.date_from_edit.text())
        date_to = _parse_optional_date(self.date_to_edit.text())
        if date_from is _INVALID or date_to is _INVALID:
            QMessageBox.warning(self, "Фильтр", "Дата должна быть в формате ГГГГ-ММ-ДД.")
            return

        self._model.set_filters(wmo_filter, date_from, date_to)
        self._refresh_pagination_label()

    def _refresh_pagination_label(self) -> None:
        self.page_label.setText(
            f"Страница {self._model.page + 1} из {self._model.total_pages} "
            f"(всего {self._model.total_count})"
        )

    def _prev_page(self) -> None:
        self._model.go_to_page(self._model.page - 1)
        self._refresh_pagination_label()

    def _next_page(self) -> None:
        self._model.go_to_page(self._model.page + 1)
        self._refresh_pagination_label()

    def _add_row(self) -> None:
        dialog = _AddRowDialog(self.rp5_radio.isChecked(), self)
        if dialog.exec() != QDialog.DialogCode.Accepted or dialog.result_values is None:
            return
        try:
            self._model.insert_row(dialog.result_values)
        except IntegrityError:
            QMessageBox.warning(self, "Новая строка", "Запись с такими ключевыми полями уже существует.")
            return
        self._refresh_pagination_label()

    def _delete_row(self) -> None:
        selected = self.table.selectionModel().selectedRows()
        if not selected:
            QMessageBox.information(self, "Удаление", "Сначала выберите строку.")
            return
        confirm = QMessageBox.question(
            self, "Удаление строки",
            "Удалить выбранную строку? Агрегаты для этой даты будут пересчитаны.",
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        self._model.delete_row(selected[0].row())
        self._refresh_pagination_label()
