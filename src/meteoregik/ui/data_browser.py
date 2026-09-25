"""Экран "Просмотр исходных данных": таблица с пагинацией, фильтр по станции
(wmo_index)/формату(rp5|ГГ)/периоду, выбор отображаемых полей, inline-
редактирование ячеек, добавление/удаление строк -> триггерит
calculations/recompute.py для затронутых дат (см. plan.md, "Экраны UI", п.2).

Таблица при открытии экрана пустая: данные подгружаются только по кнопке
"Применить" (таблицы большие, и пользователь всё равно начинает с фильтров).
Фильтр станции — по индексу ВМО (число), а не по названию: индекс уникален,
не зависит от написания/регистра и сравнивается быстрее строки; название
("Название, Регион (индекс)") показывается только в выпадающем списке.

Период — две даты, задаваемые только вместе: обе пусты (весь период,
имеющийся в таблице) либо обе заполнены (начало не позже конца, будущие даты
запрещены).

Всегда показываются "WMO", "Станция", "Дата" (и "Срок наблюдения" у rp5) — они не входят
в фильтр полей. Остальные поля пользователь выбирает в модальном окне; выбор
запоминается отдельно для каждого формата (preferences.py) и применяется
автоматически при следующих запросах, в том числе после перезапуска.

Редактируемы только сами значения наблюдений (дата/время у rp5 и параметры) —
wmo_index/station_name/source_file только для чтения: смена станции у уже
залитой строки и правка кэшированного имени — вне сценария этого экрана
(имя станции правится в будущем "Справочнике станций").

Пересчёт после правки/удаления запускается БЕЗ conflict_resolver (новое
значение всегда побеждает) — это прямая правка пользователем, а не
расхождение источников при импорте (см. calculations/recompute.py),
переспрашивать здесь нечего."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from datetime import time as dt_time

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QRegularExpression, Qt
from PySide6.QtGui import QRegularExpressionValidator
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QTableView,
    QVBoxLayout,
)
from sqlalchemy import Date, Float, Integer, Table, Time, delete, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from meteoregik.calculations.recompute import recompute_range
from meteoregik.db.schema import RP5_PARAMETER_COLUMNS, gg_readings, rp5_readings
from meteoregik.importers.rp5_importer import RP5_COLUMN_MAP
from meteoregik.preferences import Preferences
from meteoregik.stations.choices import StationChoice, load_directory_stations, load_stations_in_table
from meteoregik.ui import theme
from meteoregik.ui.date_field import DateField, DateFieldError
from meteoregik.ui.dialogs import ask_yes_no
from meteoregik.ui.station_combo import ALL_STATIONS_TEXT, StationCombo

PAGE_SIZE = 100
MAX_COLUMN_WIDTH = 260
DATE_PY_FORMAT = "%d.%m.%Y"
# Сроки наблюдения rp5 — каждые 3 часа; другие значения при ручном вводе недопустимы
OBSERVATION_HOURS = (0, 3, 6, 9, 12, 15, 18, 21)
MANUAL_ENTRY_SOURCE = "ручной ввод"  # source_file строк, добавленных через "Новая строка"
STATION_NOT_FOUND_MESSAGE = (
    "Такой станции не существует в текущем справочнике метеостанций. "
    "Проверьте правильность выбранной станции или внесите новую станцию в справочник "
    "(кнопка «Справочник станций» на главном экране) и повторите попытку."
)

# db-имя -> исходный код столбца rp5.ru (для заголовков таблицы), напр.
# "T_temperature" -> "T", "E2_soil_state_snow" -> "E'".
_RP5_HEADER_BY_COLUMN = {db_col: src_col for src_col, db_col in RP5_COLUMN_MAP.items()}


def _rp5_columns() -> list[tuple[str, str, bool]]:
    columns: list[tuple[str, str, bool]] = [
        ("wmo_index", "WMO", False),
        ("station_name", "Станция", False),
        ("obs_date", "Дата", True),
        ("obs_time", "Срок наблюдения", True),
    ]
    columns += [
        (name, _RP5_HEADER_BY_COLUMN.get(name, name), True) for name, _ in RP5_PARAMETER_COLUMNS
    ]
    columns.append(("source_file", "Файл", False))
    return columns


RP5_COLUMNS = _rp5_columns()
RP5_TEMP_COLUMNS = {"T_temperature"}
# Всегда на экране, в фильтр полей не входят.
RP5_FIXED_COLUMNS = ("wmo_index", "station_name", "obs_date", "obs_time")

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
GG_FIXED_COLUMNS = ("wmo_index", "station_name", "obs_date")

# Расшифровки для окна выбора полей (db-имя колонки -> описание); заголовок
# рядом с расшифровкой — тот, что и в самой таблице (код столбца как в Excel).
RP5_FIELD_DESCRIPTIONS = {
    "T_temperature": "Температура воздуха, °C",
    "Po_pressure_station": "Давление на уровне станции, мм рт.ст.",
    "P_pressure_sealevel": "Давление, приведённое к уровню моря, мм рт.ст.",
    "Pa_pressure_tendency": "Барическая тенденция за 3 часа, мм рт.ст.",
    "U_humidity": "Относительная влажность, %",
    "DD_wind_direction": "Направление ветра",
    "Ff_wind_speed": "Скорость ветра, м/с",
    "ff10_wind_gust": "Максимальный порыв ветра, м/с",
    "ff3_wind_gust": "Максимальный порыв ветра между сроками, м/с",
    "N_cloudiness_total": "Общая облачность",
    "WW_weather_present": "Погода в срок наблюдения",
    "W1_weather_past1": "Погода между сроками наблюдения (1)",
    "W2_weather_past2": "Погода между сроками наблюдения (2)",
    "Tn_temperature_min": "Минимальная температура за период между сроками, °C",
    "Tx_temperature_max": "Максимальная температура за период между сроками, °C",
    "Cl_clouds_low": "Облака нижнего яруса (Cl)",
    "Nh_cloud_amount": "Количество облаков (Nh)",
    "H_cloud_base_height": "Высота основания облаков, м",
    "Cm_clouds_mid": "Облака среднего яруса (Cm)",
    "Ch_clouds_high": "Облака верхнего яруса (Ch)",
    "VV_visibility": "Горизонтальная видимость, км",
    "Td_dewpoint": "Точка росы, °C",
    "RRR_precipitation": "Количество осадков, мм",
    "tR_precipitation_period": "Период накопления осадков, ч",
    "E_soil_state": "Состояние поверхности почвы без снега",
    "Tg_soil_temp_min": "Минимальная температура поверхности почвы, °C",
    "E2_soil_state_snow": "Состояние поверхности почвы со снегом",
    "sss_snow_depth": "Высота снежного покрова, см",
    "source_file": "Файл, из которого загружена строка",
}
GG_FIELD_DESCRIPTIONS = {
    "t_avg": "Среднесуточная температура, °C",
    "t_min": "Минимальная температура за сутки, °C",
    "t_max": "Максимальная температура за сутки, °C",
    "rrr_precipitation": "Количество осадков, мм",
    "source_file": "Файл, из которого загружена строка",
}


@dataclass(frozen=True)
class _FormatSpec:
    key: str
    table: Table
    columns: list[tuple[str, str, bool]]
    fixed: tuple[str, ...]
    temp_columns: set[str]
    descriptions: dict[str, str]

    def optional_fields(self) -> list[tuple[str, str, str]]:
        """(db-колонка, заголовок, расшифровка) всех полей, кроме постоянных."""
        return [
            (name, header, self.descriptions.get(name, ""))
            for name, header, _ in self.columns
            if name not in self.fixed
        ]

    def columns_for(self, selected: list[str]) -> list[tuple[str, str, bool]]:
        """Постоянные + выбранные поля — в исходном порядке столбцов."""
        chosen = set(selected)
        return [c for c in self.columns if c[0] in self.fixed or c[0] in chosen]


RP5_SPEC = _FormatSpec("rp5", rp5_readings, RP5_COLUMNS, RP5_FIXED_COLUMNS, RP5_TEMP_COLUMNS, RP5_FIELD_DESCRIPTIONS)
GG_SPEC = _FormatSpec("gg", gg_readings, GG_COLUMNS, GG_FIXED_COLUMNS, GG_TEMP_COLUMNS, GG_FIELD_DESCRIPTIONS)


def _parse_date_text(text: str) -> date:
    """дд.мм.гггг (как на экране) или гггг-мм-дд."""
    for fmt in (DATE_PY_FORMAT, "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Неверный формат даты: {text!r}")


def _parse_time_text(text: str) -> dt_time:
    """"9"/"09" (только час, как на экране), "09:00" или "09:00:00"."""
    if ":" not in text:
        return dt_time(int(text))
    fmt = "%H:%M:%S" if text.count(":") == 2 else "%H:%M"
    return datetime.strptime(text, fmt).time()


def _parse_cell_value(col_type, raw_value):
    text = raw_value.strip() if isinstance(raw_value, str) else raw_value
    if text in (None, ""):
        return None
    if isinstance(col_type, Float):
        return float(str(text).replace(",", "."))
    if isinstance(col_type, Integer):
        return int(text)
    if isinstance(col_type, Date):
        return text if isinstance(text, date) else _parse_date_text(str(text))
    if isinstance(col_type, Time):
        return text if isinstance(text, dt_time) else _parse_time_text(str(text))
    return str(text)


_NUMBER_RE = re.compile(r"^[+-]?\d+(?:[.,]\d+)?$")


def _display_text(value) -> str:
    """Дата — дд.мм.гггг, время — только часы, остальное как есть."""
    if value is None:
        return ""
    if isinstance(value, date):
        return value.strftime(DATE_PY_FORMAT)
    if isinstance(value, dt_time):
        return f"{value.hour:02d}"
    return str(value)


def _edit_text(value) -> str:
    """Текст в редакторе ячейки: время — с минутами (на экране только час, но
    молча обнулять минуты при правке нельзя)."""
    if isinstance(value, dt_time):
        return value.strftime("%H:%M")
    return _display_text(value)


def _is_numeric(value) -> bool:
    """Только цифры без букв (числа, даты, время; строка вроде "12.5") —
    такие значения выравниваются по правому краю, как в Excel."""
    if value is None or isinstance(value, bool):
        return False
    if isinstance(value, (int, float, date, dt_time)):
        return True
    return isinstance(value, str) and bool(_NUMBER_RE.match(value.strip()))


class ReadingsTableModel(QAbstractTableModel):
    """Одна страница строк rp5_readings/gg_readings с фильтрами и inline-
    редактированием. reload() каждый раз перечитывает текущую страницу из
    БД — таблицы большие (десятки тысяч строк), в памяти держим только
    PAGE_SIZE строк.

    Пока не вызван set_filters() (`loaded` == False), модель пуста: данные
    подгружаются только по явному запросу пользователя."""

    def __init__(self, engine: Engine, table, columns, temp_columns: set[str], parent=None) -> None:
        super().__init__(parent)
        self.engine = engine
        self.table = table
        self.columns = columns
        self.temp_columns = temp_columns
        self.rows: list[dict] = []
        self.total_count = 0
        self.page = 0
        self.loaded = False
        self.wmo_filter: int | None = None
        self.date_from: date | None = None
        self.date_to: date | None = None

    def set_columns(self, columns) -> None:
        self.beginResetModel()
        self.columns = columns
        self.endResetModel()

    def set_filters(self, wmo_filter: int | None, date_from: date | None, date_to: date | None) -> None:
        self.wmo_filter = wmo_filter
        self.date_from = date_from
        self.date_to = date_to
        self.page = 0
        self.loaded = True
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
            # после удаления последней строки страницы текущая могла исчезнуть
            self.page = max(0, min(self.page, self.total_pages - 1))

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
        if not index.isValid():
            return None
        value = self.rows[index.row()].get(self.columns[index.column()][0])
        if role == Qt.ItemDataRole.DisplayRole:
            return _display_text(value)
        if role == Qt.ItemDataRole.EditRole:
            return _edit_text(value)
        if role == Qt.ItemDataRole.TextAlignmentRole and _is_numeric(value):
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return None

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
        if self.loaded:
            self.reload()

    def delete_row(self, row_index: int) -> None:
        row = self.rows[row_index]
        with self.engine.begin() as conn:
            conn.execute(delete(self.table).where(self.table.c.id == row["id"]))
        recompute_range(self.engine, row["wmo_index"], row["obs_date"], row["obs_date"])
        self.reload()


class _AddRowDialog(QDialog):
    """Мини-форма для новой строки — только ключевые поля (станция, дата[, срок
    наблюдения]); остальные параметры пользователь заполнит потом inline в
    таблице. Срок наблюдения — только из OBSERVATION_HOURS, иначе сообщение и
    данные не сохраняются.

    Станция выбирается из справочника (не только из тех, что уже есть в
    таблице — строку можно добавить и для станции, по которой данных ещё нет),
    по умолчанию подставляется станция из фильтра. Введённый текст, которого
    нет в справочнике, не принимается. station_name берётся из справочника,
    source_file — "ручной ввод"."""

    def __init__(
        self,
        is_rp5: bool,
        choices: list[StationChoice],
        preselected_wmo: int | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.is_rp5 = is_rp5
        self.result_values: dict | None = None
        self.setWindowTitle("Новая строка")
        self.setStyleSheet(theme.DIALOG_QSS)
        self._build_ui(choices, preselected_wmo)

    def _build_ui(self, choices: list[StationChoice], preselected_wmo: int | None) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)

        layout.addWidget(QLabel("Метеостанция:"))
        self.station_combo = StationCombo()
        self.station_combo.set_choices(choices)
        self.station_combo.select_wmo(preselected_wmo)
        layout.addWidget(self.station_combo)

        layout.addWidget(QLabel("Дата:"))
        self.date_edit = DateField(allow_empty=False)
        self.date_edit.set_date(date.today())
        layout.addWidget(self.date_edit)

        if self.is_rp5:
            layout.addWidget(QLabel("Срок наблюдения (час):"))
            self.hour_edit = QLineEdit()
            # QIntValidator пропускал бы "24" как промежуточный ввод, поэтому строгая
            # маска: только 0–23, без знака и букв
            self.hour_edit.setValidator(
                QRegularExpressionValidator(QRegularExpression(r"(?:[01]?[0-9]|2[0-3])"), self)
            )
            self.hour_edit.setMaxLength(2)
            self.hour_edit.setPlaceholderText("0, 3, 6 … 21")
            self.hour_edit.setToolTip("Допустимые значения: " + ", ".join(str(h) for h in OBSERVATION_HOURS))
            self.hour_edit.setFixedWidth(140)
            layout.addWidget(self.hour_edit)

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

    def _warn(self, text: str) -> None:
        QMessageBox.warning(self, "Новая строка", text)

    def _try_accept(self) -> None:
        try:
            wmo_index = self.station_combo.selected_wmo()
        except ValueError:
            if self.station_combo.currentText().strip():
                self._warn(STATION_NOT_FOUND_MESSAGE)
            else:
                self._warn("Выберите метеостанцию.")
            return

        try:
            obs_date = self.date_edit.value()
        except DateFieldError as exc:
            self._warn(str(exc))
            return

        values = {
            "wmo_index": wmo_index,
            "station_name": self.station_combo.name_of(wmo_index),
            "obs_date": obs_date,
            "source_file": MANUAL_ENTRY_SOURCE,
        }
        if self.is_rp5:
            hour_text = self.hour_edit.text().strip()
            allowed = ", ".join(str(h) for h in OBSERVATION_HOURS)
            if not hour_text:
                self._warn(f"Укажите срок наблюдения. Допустимые значения: {allowed}.")
                return
            if int(hour_text) not in OBSERVATION_HOURS:
                self._warn(
                    f"Недопустимый срок наблюдения: {hour_text}. Допустимые значения: {allowed}. "
                    "Данные не сохранены."
                )
                return
            values["obs_time"] = dt_time(int(hour_text))
        self.result_values = values
        self.accept()


class _FieldsDialog(QDialog):
    """Модальное окно множественного выбора отображаемых полей. Именно окно,
    а не выпадашка: у rp5 около 30 полей с расшифровками — в выпадающем списке
    они не поместятся и пришлось бы прокручивать."""

    def __init__(
        self,
        fields: list[tuple[str, str, str]],
        selected: set[str],
        parent=None,
        fixed_headers: tuple[str, ...] = (),
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Выбрать поля для отображения")
        self.resize(560, 580)
        self.setStyleSheet(theme.DIALOG_QSS)
        self._build_ui(fields, selected, fixed_headers)

    def _build_ui(self, fields, selected, fixed_headers) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        hint = QLabel(f"Всегда показываются: {', '.join(fixed_headers)}." if fixed_headers else "")
        hint.setStyleSheet(f"color: {theme.TEXT_SECONDARY};")
        layout.addWidget(hint)

        self.list_widget = QListWidget()
        for column, header, description in fields:
            text = f"{header} — {description}" if description else header
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, column)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if column in selected else Qt.CheckState.Unchecked)
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

    def selected_columns(self) -> list[str]:
        return [
            self.list_widget.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self.list_widget.count())
            if self.list_widget.item(i).checkState() == Qt.CheckState.Checked
        ]


class DataBrowserDialog(QDialog):
    def __init__(self, engine: Engine, parent=None, preferences: Preferences | None = None) -> None:
        super().__init__(parent)
        self.engine = engine
        self._preferences = preferences or Preferences.next_to_database(engine.url.database)
        self.setWindowTitle("Просмотр исходных данных")
        self.resize(1100, 640)
        self.setStyleSheet(theme.DIALOG_QSS)

        self._spec = RP5_SPEC
        self._selected_fields = self._load_selected_fields(self._spec)
        self._model = ReadingsTableModel(
            engine, self._spec.table, self._spec.columns_for(self._selected_fields), self._spec.temp_columns
        )
        self._build_ui()
        self._reload_station_choices()
        self._refresh_fields_label()
        self._refresh_status()

    # --- настройки: выбранные поля по форматам ---

    def _preference_key(self, spec: _FormatSpec) -> str:
        return f"data_browser.{spec.key}_fields"

    def _load_selected_fields(self, spec: _FormatSpec) -> list[str]:
        """По умолчанию (пока пользователь ничего не выбирал) — все поля;
        сохранённые имена, которых больше нет в схеме, отбрасываются."""
        available = [name for name, _, _ in spec.optional_fields()]
        saved = self._preferences.get(self._preference_key(spec))
        if not isinstance(saved, list):
            return available
        return [name for name in available if name in saved]

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
        self.add_btn = QPushButton("Добавить строку")
        self.add_btn.setObjectName("add")
        self.add_btn.clicked.connect(self._add_row)
        toolbar.addWidget(self.add_btn)
        self.delete_btn = QPushButton("Удалить строку")
        self.delete_btn.setObjectName("danger")
        self.delete_btn.clicked.connect(self._delete_row)
        toolbar.addWidget(self.delete_btn)
        toolbar.addStretch(1)

        self.prev_btn = QPushButton("◀ Пред.")
        self.prev_btn.setObjectName("secondary")
        self.prev_btn.clicked.connect(self._prev_page)
        toolbar.addWidget(self.prev_btn)
        self.page_label = QLabel("")
        toolbar.addWidget(self.page_label)
        self.next_btn = QPushButton("След. ▶")
        self.next_btn.setObjectName("secondary")
        self.next_btn.clicked.connect(self._next_page)
        toolbar.addWidget(self.next_btn)
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
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(16, 12, 16, 12)
        card_layout.setSpacing(10)

        top_row = QHBoxLayout()
        top_row.setSpacing(10)

        self.rp5_radio = QRadioButton("rp5")
        self.gg_radio = QRadioButton("ГГ")
        self.rp5_radio.setChecked(True)
        self._format_group = QButtonGroup(self)
        self._format_group.addButton(self.rp5_radio)
        self._format_group.addButton(self.gg_radio)
        self._format_group.buttonClicked.connect(self._on_format_changed)
        top_row.addWidget(QLabel("Формат:"))
        top_row.addWidget(self.rp5_radio)
        top_row.addWidget(self.gg_radio)
        top_row.addSpacing(10)

        top_row.addWidget(QLabel("Метеостанция:"))
        self.station_combo = StationCombo(all_text=ALL_STATIONS_TEXT)
        top_row.addWidget(self.station_combo, stretch=1)
        card_layout.addLayout(top_row)

        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(10)

        # "Период" — одна визуальная группа из двух дат; пусты обе или заполнены обе
        period_group = QFrame()
        period_group.setObjectName("group")
        period_layout = QHBoxLayout(period_group)
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
        bottom_row.addWidget(period_group)

        self.fields_btn = QPushButton("Выбрать поля для отображения")
        self.fields_btn.setObjectName("skySoft")
        self.fields_btn.clicked.connect(self._choose_fields)
        bottom_row.addWidget(self.fields_btn)
        self.fields_label = QLabel("")
        self.fields_label.setStyleSheet(f"color: {theme.TEXT_SECONDARY};")
        bottom_row.addWidget(self.fields_label)
        bottom_row.addStretch(1)

        self.apply_btn = QPushButton("Применить")
        self.apply_btn.setObjectName("primary")
        self.apply_btn.clicked.connect(self._apply_filters)
        bottom_row.addWidget(self.apply_btn)
        card_layout.addLayout(bottom_row)

        return card

    # --- фильтры: станции, период, поля ---

    def _reload_station_choices(self) -> None:
        """Станции, реально присутствующие в таблице выбранного формата."""
        self.station_combo.set_choices(load_stations_in_table(self.engine, self._spec.table))

    def _refresh_fields_label(self) -> None:
        total = len(self._spec.optional_fields())
        self.fields_label.setText(f"Выбрано полей: {len(self._selected_fields)} из {total}")

    def _choose_fields(self) -> None:
        fixed_headers = tuple(header for name, header, _ in self._spec.columns if name in self._spec.fixed)
        dialog = _FieldsDialog(
            self._spec.optional_fields(), set(self._selected_fields), self, fixed_headers=fixed_headers
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._selected_fields = dialog.selected_columns()
        self._preferences.set(self._preference_key(self._spec), self._selected_fields)
        self._refresh_fields_label()

    def _on_format_changed(self) -> None:
        spec = RP5_SPEC if self.rp5_radio.isChecked() else GG_SPEC
        if spec is self._spec:
            return
        self._spec = spec
        self._selected_fields = self._load_selected_fields(spec)
        self._model = ReadingsTableModel(
            self.engine, spec.table, spec.columns_for(self._selected_fields), spec.temp_columns
        )
        self.table.setModel(self._model)
        self._reload_station_choices()
        self._refresh_fields_label()
        self._refresh_status()

    def _apply_filters(self) -> None:
        try:
            wmo_filter = self.station_combo.selected_wmo()
        except ValueError:
            QMessageBox.warning(self, "Фильтр", "Выберите метеостанцию из списка или очистите поле.")
            return

        dates = []
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
        if date_from is not None and date_from > date_to:
            QMessageBox.warning(self, "Фильтр", "Начало периода позже его конца.")
            return

        self._model.set_columns(self._spec.columns_for(self._selected_fields))
        self._model.set_filters(wmo_filter, date_from, date_to)
        self._refresh_status()
        if self._model.total_count == 0:
            QMessageBox.information(self, "Просмотр исходных данных", "По указанным фильтрам данных не найдено.")

    # --- состояние кнопок под таблицей ---

    def _refresh_status(self) -> None:
        """Подпись пагинации, доступность кнопок (пока в выводе ничего нет,
        "Удалить строку"/"Пред."/"След." недоступны) и ширина столбцов (после
        перезагрузки модели Qt сбрасывает её к умолчанию)."""
        model = self._model
        has_rows = model.rowCount() > 0
        if model.loaded:
            self.page_label.setText(
                f"Страница {model.page + 1} из {model.total_pages} (всего {model.total_count})"
            )
        else:
            self.page_label.setText("")
        self.delete_btn.setEnabled(has_rows)
        self.prev_btn.setEnabled(has_rows and model.page > 0)
        self.next_btn.setEnabled(has_rows and model.page < model.total_pages - 1)
        if has_rows:
            self._fit_columns()

    def _fit_columns(self) -> None:
        """По содержимому и заголовку ("Срок наблюдения" длиннее ширины по
        умолчанию), но не шире MAX_COLUMN_WIDTH — длинные тексты обрезаются."""
        self.table.resizeColumnsToContents()
        header = self.table.horizontalHeader()
        for section in range(header.count()):
            if header.sectionSize(section) > MAX_COLUMN_WIDTH:
                header.resizeSection(section, MAX_COLUMN_WIDTH)

    def _prev_page(self) -> None:
        self._model.go_to_page(self._model.page - 1)
        self._refresh_status()

    def _next_page(self) -> None:
        self._model.go_to_page(self._model.page + 1)
        self._refresh_status()

    def _add_row(self) -> None:
        try:
            preselected_wmo = self.station_combo.selected_wmo()
        except ValueError:
            preselected_wmo = None
        dialog = _AddRowDialog(
            self.rp5_radio.isChecked(), load_directory_stations(self.engine), preselected_wmo, self
        )
        if dialog.exec() != QDialog.DialogCode.Accepted or dialog.result_values is None:
            return
        try:
            self._model.insert_row(dialog.result_values)
        except IntegrityError:
            QMessageBox.warning(self, "Новая строка", "Запись с такими ключевыми полями уже существует.")
            return
        self._reload_station_choices()  # в таблице могла появиться новая станция
        self._refresh_status()
        if not self._model.loaded:
            QMessageBox.information(
                self, "Новая строка", "Строка добавлена. Нажмите «Применить», чтобы увидеть её в таблице."
            )

    def _delete_row(self) -> None:
        selected = self.table.selectionModel().selectedRows()
        if not selected:
            QMessageBox.information(self, "Удаление", "Сначала выберите строку.")
            return
        if not ask_yes_no(
            self, "Удаление строки",
            "Удалить выбранную строку? Агрегаты для этой даты будут пересчитаны.",
        ):
            return
        self._model.delete_row(selected[0].row())
        self._refresh_status()
