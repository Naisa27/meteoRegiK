"""Вкладка "Исходные данные" экрана "Дашборд" (корректировка 2026-10-10):
что залито в базу, за какие периоды и где пропуски. По всей базе, без
фильтра "станция + период" (он остался у вкладки "Температурный режим").

Сверху — три карточки-сводки по всем станциям. Ниже — таблица: по строке на
станцию (или на залитый файл — переключатель "По станциям / По файлам").
Вместо трёх отдельных кнопок "подробнее" из исходной постановки —
подробности по станции выбранной строки под таблицей: периоды с пропусками
(дни / сроки) и график (календарь полноты / распределение по срокам /
заполненность параметров rp5). Сумма по всем станциям не показывает, у
какой станции проблема, а таблица периодов с пропусками сразу по всем
станциям была бы в тысячи строк — поэтому "сводка -> станция -> её пропуски".

Все расчёты — в reports/source_data.py (без Qt). Данные грузятся в фоне при
первом открытии вкладки (ensure_loaded) и по кнопке "Обновить".
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QPersistentModelIndex,
    QRect,
    QSize,
    QSortFilterProxyModel,
    Qt,
)
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import (
    QAbstractItemView,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QStyle,
    QStyleOptionHeader,
    QTableView,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy.engine import Engine

from meteoregik.reports.source_data import (
    FORMAT_GG,
    FORMAT_RP5,
    OBS_PER_DAY,
    DayGap,
    LoadedFile,
    ObsGap,
    Period,
    SourceOverview,
    StationSourceDetail,
    StationSourceRow,
    fetch_source_overview,
    fetch_station_detail,
)
from meteoregik.ui.background import run_in_background
from meteoregik.ui.buttons import reserve_focus_width
from meteoregik.ui.dashboard_source_charts import FillCalendar, ObsHoursChart, ParameterFillChart
from meteoregik.ui.dashboard_widgets import CardText, GrooveDivider, StatCard, format_count, format_day, mode_switch, scrollable
from meteoregik.ui.loading_overlay import LoadingOverlay

_NO_VALUE = "—"
_EMPTY_HINT = (
    "В базе пока нет залитых данных.\n"
    "Загрузите файлы rp5 или ГГ через «Импорт данных» на главном экране."
)
_SORT_ROLE = Qt.ItemDataRole.UserRole
# Строк основной таблицы видно без прокрутки: станций обычно немного, а
# место под таблицей нужнее подробностям выбранной станции.
_MAX_VISIBLE_ROWS = 4

_DAYS_RULE = (
    "Пропущенный день — день между первым и последним днём станции в расчётной "
    "таблице, за который нет средней суточной температуры. Дни до начала и после "
    "конца архива станции пропусками не считаются."
)
_OBS_RULE = (
    f"Пропущенный срок — срок наблюдения rp5 без температуры: строки на этот срок "
    f"нет или температура в ней пустая. Сроков {OBS_PER_DAY} в сутки, через 3 часа. "
    "Считаются только дни, где есть хотя бы один срок: день без единого срока — "
    "это пропущенный день, а не восемь пропущенных сроков."
    "\n\nЕсли станция в какие-то годы наблюдала 4 раза в сутки, каждый такой день "
    "даёт 4 пропущенных срока — это видно на графике «Сроки»."
)
HELP_STATIONS = (
    "Сколько разных метеостанций залито в базу. Станция считается один раз, даже "
    "если по ней загружено несколько файлов или оба формата."
    "\n\nrp5 — станции с наблюдениями по срокам (выгрузки rp5.ru), ГГ — станции с "
    "суточными данными формата ГГ."
)
HELP_PERIODS = (
    "Крупно — от самого раннего до самого позднего дня в расчётной таблице (в ней "
    "суточные значения, посчитанные по залитым данным) по всем станциям сразу. "
    "Ниже — то же по исходным таблицам rp5 и ГГ."
    "\n\nУ каждой станции период свой — он в таблице ниже."
)
HELP_GAPS = _DAYS_RULE + "\n\n" + _OBS_RULE + "\n\nЗдесь — сумма по всем станциям; по каждой станции — в таблице ниже."

_CHART_TITLES = ("Полнота по месяцам", "Наблюдения по срокам", "Заполненность параметров rp5")
_CHART_NOTES = (
    "Чем темнее клетка, тем больше дней месяца с данными. Число — процент в неполном "
    "месяце, штриховка — месяц без данных, пустая клетка — вне архива станции.",
    "Столбик — в скольких днях на этот срок есть температура. Пунктир — все дни "
    "с наблюдениями: столбик ниже пунктира — срок пропускался.",
    "Доля строк rp5, где параметр заполнен. Осадки, Tn, Tx, снег и состояние почвы "
    "передаются 1–2 раза в сутки — у них низкая доля это норма.",
)


def plural(count: int, one: str, few: str, many: str) -> str:
    """Форма слова по числу: 1 день, 2 дня, 5 дней, 11 дней, 21 день."""
    tail = abs(count) % 100
    if 11 <= tail <= 14:
        return many
    last = tail % 10
    if last == 1:
        return one
    return few if 2 <= last <= 4 else many


def _days_text(count: int) -> str:
    return f"{format_count(count)} {plural(count, 'день', 'дня', 'дней')}"


def _obs_text(count: int) -> str:
    return f"{format_count(count)} {plural(count, 'срок', 'срока', 'сроков')}"


def _stations_text(count: int) -> str:
    return f"{count} {plural(count, 'станции', 'станций', 'станций')}"


def format_period(period: Period | None) -> str:
    return f"{format_day(period[0])}–{format_day(period[1])}" if period else _NO_VALUE


def _format_average(value: float | None) -> str:
    """Среднее число пропусков: два знака у малых значений (0.02 в день не
    должно превращаться в "0.0"), один — у остальных."""
    if value is None:
        return _NO_VALUE
    return f"{value:.2f}" if 0 < value < 1 else f"{value:.1f}"


def _format_moment(moment: datetime) -> str:
    return f"{moment:%d.%m.%Y %H:%M}"


def source_card_texts(overview: SourceOverview) -> list[CardText]:
    both = overview.stations_with(FORMAT_RP5, FORMAT_GG)
    stations = CardText(
        "Метеостанции с данными",
        str(len(overview.stations)),
        f"rp5 — {overview.stations_with(FORMAT_RP5)}, ГГ — {overview.stations_with(FORMAT_GG)}, "
        f"в обоих форматах — {both}",
        HELP_STATIONS,
    )
    periods = CardText(
        "Залитые периоды",
        format_period(overview.computed_period),
        f"rp5: {format_period(overview.rp5_period)}\nГГ: {format_period(overview.gg_period)}",
        HELP_PERIODS,
    )
    gaps = CardText(
        "Пропуски данных",
        f"{_days_text(overview.days_missing)}, {_obs_text(overview.obs_missing)}",
        f"Пропущенные дни — у {_stations_text(overview.stations_with_day_gaps)}, "
        f"сроки — у {_stations_text(overview.stations_with_obs_gaps)}",
        HELP_GAPS,
    )
    return [stations, periods, gaps]


def day_gaps_note(gaps: Sequence[DayGap]) -> str:
    if not gaps:
        return "Пропущенных дней нет."
    longest = max(gaps, key=lambda gap: gap.days)
    return (
        f"{format_count(len(gaps))} {plural(len(gaps), 'период', 'периода', 'периодов')}, "
        f"всего {_days_text(sum(gap.days for gap in gaps))}. Самый длинный — {_days_text(longest.days)} "
        f"(с {format_day(longest.first)})."
    )


def obs_gaps_note(gaps: Sequence[ObsGap], detail: StationSourceDetail) -> str:
    if not detail.obs_days:
        return "Срочных наблюдений rp5 по станции нет (данные ГГ — уже суточные)."
    if not gaps:
        return "Пропущенных сроков нет."
    longest = max(gaps, key=lambda gap: gap.count)
    return (
        f"{format_count(len(gaps))} {plural(len(gaps), 'период', 'периода', 'периодов')}, "
        f"всего {_obs_text(sum(gap.count for gap in gaps))}. Самый длинный — {_obs_text(longest.count)} "
        f"(с {_format_moment(longest.first)})."
    )


# --- таблицы ---


@dataclass(frozen=True)
class Column:
    """Столбец таблицы: текст ячейки и ключ сортировки считаются из строки
    данных. Ключ — число или строка (не текст ячейки): "9" должно идти
    раньше "32", а даты — по календарю, а не по "дд.мм.гггг". Длинные
    заголовки — в две строки: столбцов много, а ширина нужнее названию станции."""

    title: str
    text: Callable[[Any], str]
    key: Callable[[Any], Any]
    numeric: bool = False
    tip: str = ""
    # Общий заголовок верхнего уровня для соседних столбцов (GroupedHeader)
    group: str = ""


def _period_key(period: Period | None) -> int:
    return period[0].toordinal() if period else 0


def _number_key(value: float | None) -> float:
    return -1.0 if value is None else value


GROUP_DAYS = "Пропущено дней"
GROUP_OBS = "Пропущено сроков"

STATION_COLUMNS = [
    Column("Станция", lambda r: r.label, lambda r: r.label.casefold()),
    Column("Форматы", lambda r: ", ".join(r.formats) or _NO_VALUE, lambda r: ", ".join(r.formats)),
    Column("Период rp5", lambda r: format_period(r.rp5_period), lambda r: _period_key(r.rp5_period),
           tip="Первый и последний день в исходной таблице rp5"),
    Column("Период ГГ", lambda r: format_period(r.gg_period), lambda r: _period_key(r.gg_period),
           tip="Первый и последний день в исходной таблице ГГ"),
    Column("Расчётная\nтаблица", lambda r: format_period(r.computed_period), lambda r: _period_key(r.computed_period),
           tip="Первый и последний день с посчитанной средней суточной температурой"),
    Column("всего", lambda r: format_count(r.days_missing) if r.days_total else _NO_VALUE,
           lambda r: r.days_missing if r.days_total else -1, numeric=True, tip=_DAYS_RULE, group=GROUP_DAYS),
    Column("среднее\nв год/в месяц",
           lambda r: f"{_format_average(r.days_missing_per_year)} / {_format_average(r.days_missing_per_month)}"
           if r.days_total else _NO_VALUE,
           lambda r: _number_key(r.days_missing_per_year), numeric=True,
           tip="Сколько дней пропущено в среднем за год и за месяц архива станции", group=GROUP_DAYS),
    Column("всего", lambda r: format_count(r.obs_missing) if r.obs_days else _NO_VALUE,
           lambda r: r.obs_missing if r.obs_days else -1, numeric=True, tip=_OBS_RULE, group=GROUP_OBS),
    Column("среднее\nв день", lambda r: _format_average(r.obs_missing_per_day),
           lambda r: _number_key(r.obs_missing_per_day), numeric=True,
           tip="Сколько сроков пропущено в среднем за день с наблюдениями", group=GROUP_OBS),
]

FILE_COLUMNS = [
    Column("Файл", lambda f: f.source_file or "(добавлено вручную)", lambda f: (f.source_file or "").casefold()),
    Column("Формат", lambda f: f.format, lambda f: f.format),
    Column("Станция", lambda f: f.station_label, lambda f: f.station_label.casefold()),
    Column("Строк", lambda f: format_count(f.rows), lambda f: f.rows, numeric=True,
           tip="Сколько строк из этого файла сейчас в базе"),
    Column("Период данных", lambda f: format_period(f.period), lambda f: _period_key(f.period)),
    Column("Загружен", lambda f: _format_moment(f.loaded_at) if f.loaded_at else _NO_VALUE,
           lambda f: f.loaded_at.timestamp() if f.loaded_at else 0.0,
           tip="Когда в базу попала первая строка из этого файла"),
]

DAY_GAP_COLUMNS = [
    Column("Первый день", lambda g: format_day(g.first), lambda g: g.first.toordinal()),
    Column("Последний день", lambda g: format_day(g.last), lambda g: g.last.toordinal()),
    Column("Дней", lambda g: format_count(g.days), lambda g: g.days, numeric=True),
]

OBS_GAP_COLUMNS = [
    Column("Первый срок", lambda g: _format_moment(g.first), lambda g: g.first.timestamp()),
    Column("Последний срок", lambda g: _format_moment(g.last), lambda g: g.last.timestamp()),
    Column("Сроков", lambda g: format_count(g.count), lambda g: g.count, numeric=True),
]

_Index = QModelIndex | QPersistentModelIndex


class RowsModel(QAbstractTableModel):
    """Таблица только для чтения поверх списка строк данных и описаний столбцов."""

    def __init__(self, columns: list[Column], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.columns = columns
        self.rows: list[Any] = []

    def set_rows(self, rows: Sequence[Any]) -> None:
        self.beginResetModel()
        self.rows = list(rows)
        self.endResetModel()

    def rowCount(self, parent: _Index = QModelIndex()) -> int:  # noqa: N802, B008 — имя и сигнатура Qt
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent: _Index = QModelIndex()) -> int:  # noqa: N802, B008
        return 0 if parent.isValid() else len(self.columns)

    def data(self, index: _Index, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None
        column, row = self.columns[index.column()], self.rows[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return column.text(row)
        if role == _SORT_ROLE:
            return column.key(row)
        if role == Qt.ItemDataRole.TextAlignmentRole and column.numeric:
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if role == Qt.ItemDataRole.ToolTipRole and not column.numeric:
            # В узком окне растянутый столбец (станция, файл) обрезается многоточием
            return column.text(row)
        return None

    def headerData(  # noqa: N802
        self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole
    ) -> Any:
        if orientation != Qt.Orientation.Horizontal:
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            return self.columns[section].title
        if role == Qt.ItemDataRole.ToolTipRole:
            return self.columns[section].tip or None
        return None


class GroupedHeader(QHeaderView):
    """Заголовок таблицы в два уровня (корректировка 2026-10-10): у соседних
    столбцов с одним Column.group — общая ячейка сверху ("Пропущено дней"),
    под ней их собственные подписи ("всего", "среднее в год/в месяц").
    Столбцы без группы занимают всю высоту. QHeaderView объединять ячейки
    не умеет, поэтому верхняя ячейка дорисовывается поверх своих столбцов
    тем же стилем, что и обычная секция."""

    # Поля и рамка секции из theme.DIALOG_QSS (padding 6px сверху и снизу + 1px)
    _SECTION_CHROME = 13

    def __init__(self, columns: list[Column], parent: QWidget | None = None) -> None:
        super().__init__(Qt.Orientation.Horizontal, parent)
        self._groups = [column.group for column in columns]
        # Свой заголовок, в отличие от созданного таблицей, по умолчанию не
        # реагирует на щелчки — без этого пропала бы сортировка по столбцу
        self.setSectionsClickable(True)
        self.sectionResized.connect(self.viewport().update)

    def _top_height(self) -> int:
        return self.fontMetrics().height() + self._SECTION_CHROME

    def sizeHint(self) -> QSize:  # noqa: N802 — имя метода Qt
        size = super().sizeHint()
        # Верхний уровень — строка, нижний — до двух строк
        size.setHeight(self._top_height() + 2 * self.fontMetrics().height() + self._SECTION_CHROME)
        return size

    def group_span(self, logical_index: int) -> tuple[int, int] | None:
        """Первый и последний столбец группы, в которую входит столбец."""
        group = self._groups[logical_index]
        if not group:
            return None
        first = last = logical_index
        while first > 0 and self._groups[first - 1] == group:
            first -= 1
        while last + 1 < len(self._groups) and self._groups[last + 1] == group:
            last += 1
        return first, last

    def paintSection(self, painter: QPainter, rect: QRect, logicalIndex: int) -> None:  # noqa: N802, N803
        span = self.group_span(logicalIndex)
        if span is None:
            super().paintSection(painter, rect, logicalIndex)
            return
        top_height = self._top_height()
        painter.save()
        super().paintSection(painter, rect.adjusted(0, top_height, 0, 0), logicalIndex)
        painter.restore()

        first, last = span
        option = QStyleOptionHeader()
        self.initStyleOption(option)
        option.rect = QRect(
            self.sectionViewportPosition(first), rect.top(),
            sum(self.sectionSize(column) for column in range(first, last + 1)), top_height,
        )
        option.text = self._groups[logicalIndex]
        option.textAlignment = Qt.AlignmentFlag.AlignCenter
        option.section = first
        option.position = QStyleOptionHeader.SectionPosition.Middle
        painter.save()
        self.style().drawControl(QStyle.ControlElement.CE_Header, option, painter, self)
        painter.restore()


def _make_table(
    columns: list[Column],
    parent: QWidget,
    *,
    sort_column: int = 0,
    descending: bool = False,
    stretch_column: int | None = None,
) -> tuple[QTableView, RowsModel]:
    """stretch_column — столбец, забирающий свободную ширину (остальные — по
    содержимому); None — все столбцы делят ширину поровну."""
    model = RowsModel(columns, parent)
    proxy = QSortFilterProxyModel(parent)
    proxy.setSourceModel(model)
    proxy.setSortRole(_SORT_ROLE)
    table = QTableView()
    table.setModel(proxy)
    if any(column.group for column in columns):
        table.setHorizontalHeader(GroupedHeader(columns, table))
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setAlternatingRowColors(True)
    table.setSortingEnabled(True)
    table.sortByColumn(sort_column, Qt.SortOrder.DescendingOrder if descending else Qt.SortOrder.AscendingOrder)
    table.verticalHeader().hide()
    header = table.horizontalHeader()
    if stretch_column is None:
        header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
    else:
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(stretch_column, QHeaderView.ResizeMode.Stretch)
    return table, model


def _selected_row(table: QTableView, model: RowsModel) -> Any | None:
    indexes = table.selectionModel().selectedRows()
    if not indexes:
        return None
    return model.rows[table.model().mapToSource(indexes[0]).row()]


def _title(text: str = "") -> QLabel:
    label = QLabel(text)
    label.setObjectName("chartTitle")
    return label


def _note(text: str = "") -> QLabel:
    label = QLabel(text)
    label.setObjectName("chartNote")
    label.setWordWrap(True)
    return label


class SourceDataTab(QWidget):
    def __init__(self, engine: Engine, parent: QWidget | None = None, run_async: bool = True) -> None:
        super().__init__(parent)
        self.engine = engine
        # run_async=False — расчёт в том же потоке (тесты).
        self._run_async = run_async
        self._loaded = False
        self._request_id = 0
        self._detail: StationSourceDetail | None = None
        self._detail_wmo: int | None = None
        self._plotted: dict[int, StationSourceDetail] = {}
        self._build_ui()

    # --- построение интерфейса ---

    def _build_ui(self) -> None:
        outer = QGridLayout(self)
        outer.setContentsMargins(0, 10, 0, 0)
        self._stack = QStackedWidget()
        self._hint = QLabel(_EMPTY_HINT)
        self._hint.setObjectName("emptyHint")
        self._hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._hint.setWordWrap(True)
        self._stack.addWidget(self._hint)
        self._content = self._build_content()
        self._stack.addWidget(self._content)
        outer.addWidget(scrollable(self._stack), 0, 0)
        self.loading_overlay = LoadingOverlay("Собираем сведения о данных…")
        outer.addWidget(self.loading_overlay, 0, 0)

    def _build_content(self) -> QWidget:
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        cards = QHBoxLayout()
        cards.setSpacing(10)
        self.cards = [StatCard() for _ in range(3)]
        for card in self.cards:
            cards.addWidget(card, stretch=1)
        layout.addLayout(cards)

        table_row = QHBoxLayout()
        table_row.setSpacing(12)
        table_row.addWidget(_title("Что залито"))
        switch, self.table_mode = mode_switch(["По станциям", "По файлам"], self)
        table_row.addWidget(switch)
        hint = _note("Щелчок по строке — подробности станции ниже, по заголовку столбца — сортировка.")
        hint.setWordWrap(False)
        table_row.addWidget(hint)
        table_row.addStretch(1)
        self.refresh_btn = QPushButton("Обновить")
        self.refresh_btn.setObjectName("secondary")
        self.refresh_btn.setToolTip("Пересчитать сведения — например, после импорта новых файлов")
        self.refresh_btn.clicked.connect(self.reload)
        reserve_focus_width(self.refresh_btn)
        table_row.addWidget(self.refresh_btn)
        layout.addLayout(table_row)

        self.stations_table, self.stations_model = _make_table(STATION_COLUMNS, self, stretch_column=0)
        # История загрузок — последние сверху
        self.files_table, self.files_model = _make_table(
            FILE_COLUMNS, self, sort_column=len(FILE_COLUMNS) - 1, descending=True, stretch_column=0
        )
        self._tables = QStackedWidget()
        self._tables.addWidget(self.stations_table)
        self._tables.addWidget(self.files_table)
        layout.addWidget(self._tables)
        self.table_mode.idToggled.connect(self._on_table_mode)
        for table, model in ((self.stations_table, self.stations_model), (self.files_table, self.files_model)):
            table.selectionModel().selectionChanged.connect(
                lambda *_, table=table, model=model: self._on_row_selected(_selected_row(table, model))
            )

        self._station_label = QLabel("")
        self._station_label.setStyleSheet("font-weight: 600; font-size: 14px;")
        layout.addWidget(self._station_label)

        detail = QHBoxLayout()
        detail.setSpacing(16)
        detail.addLayout(self._build_gaps_block(), stretch=2)
        detail.addWidget(GrooveDivider())
        detail.addLayout(self._build_chart_block(), stretch=3)
        layout.addLayout(detail, stretch=1)
        return content

    def _build_gaps_block(self) -> QVBoxLayout:
        block = QVBoxLayout()
        block.setSpacing(2)
        title_row = QHBoxLayout()
        title_row.setSpacing(12)
        title_row.addWidget(_title("Периоды с пропусками"))
        title_row.addStretch(1)
        switch, self.gaps_mode = mode_switch(["Дни", "Сроки"], self)
        title_row.addWidget(switch)
        block.addLayout(title_row)
        self._gaps_note = _note()
        block.addWidget(self._gaps_note)

        self.day_gaps_table, self.day_gaps_model = _make_table(DAY_GAP_COLUMNS, self)
        self.obs_gaps_table, self.obs_gaps_model = _make_table(OBS_GAP_COLUMNS, self)
        self._gap_tables = QStackedWidget()
        # Не ниже графика справа: иначе в прокручиваемой вкладке таблица
        # сжималась бы до пары строк
        self._gap_tables.setMinimumHeight(260)
        for table in (self.day_gaps_table, self.obs_gaps_table):
            self._gap_tables.addWidget(table)
        block.addWidget(self._gap_tables, stretch=1)
        self.gaps_mode.idToggled.connect(self._on_gaps_mode)
        return block

    def _build_chart_block(self) -> QVBoxLayout:
        block = QVBoxLayout()
        block.setSpacing(2)
        title_row = QHBoxLayout()
        title_row.setSpacing(12)
        self._chart_title = _title(_CHART_TITLES[0])
        title_row.addWidget(self._chart_title)
        title_row.addStretch(1)
        switch, self.chart_mode = mode_switch(["Полнота", "Сроки", "Параметры"], self)
        title_row.addWidget(switch)
        block.addLayout(title_row)
        self._chart_note = _note(_CHART_NOTES[0])
        block.addWidget(self._chart_note)

        self.fill_calendar = FillCalendar()
        self.hours_chart = ObsHoursChart()
        self.parameters_chart = ParameterFillChart()
        self.charts = (self.fill_calendar, self.hours_chart, self.parameters_chart)
        self._charts = QStackedWidget()
        for chart in self.charts:
            self._charts.addWidget(chart)
        block.addWidget(self._charts, stretch=1)
        self.chart_mode.idToggled.connect(self._on_chart_mode)
        return block

    # --- загрузка ---

    def ensure_loaded(self) -> None:
        """Первая загрузка — когда вкладку впервые открыли: сведения по всей
        базе не нужны тому, кто пришёл посмотреть температуру одной станции."""
        if not self._loaded:
            self.reload()

    def reload(self) -> None:
        self._loaded = True
        self._start(lambda: fetch_source_overview(self.engine), self._show_overview)

    def _start(self, task: Callable[[], Any], on_done: Callable[[Any], None]) -> None:
        self._request_id += 1
        request_id = self._request_id
        self._set_loading(True)

        def done(result: Any) -> None:
            if request_id != self._request_id:
                return
            self._set_loading(False)
            on_done(result)

        def failed(exc: BaseException) -> None:
            if request_id != self._request_id:
                return
            self._set_loading(False)
            QMessageBox.warning(self, "Дашборд", f"Не удалось загрузить данные: {exc}")

        if self._run_async:
            run_in_background(self, task, done, failed)
            return
        try:
            result = task()
        except Exception as exc:  # noqa: BLE001 — тот же путь, что и у фонового режима
            failed(exc)
        else:
            done(result)

    def _set_loading(self, loading: bool) -> None:
        self.loading_overlay.setVisible(loading)
        self.refresh_btn.setEnabled(not loading)
        if loading:
            self.loading_overlay.raise_()

    # --- показ ---

    def _show_overview(self, overview: SourceOverview) -> None:
        if not overview.has_data:
            self._stack.setCurrentWidget(self._hint)
            return
        for card, text in zip(self.cards, source_card_texts(overview)):
            card.set_text(text)
        selected = self._detail_wmo
        self._detail_wmo = None
        self.stations_model.set_rows(overview.stations)
        self.files_model.set_rows(overview.files)
        self._fit_tables_height()
        self._stack.setCurrentWidget(self._content)
        self._select_station(selected)

    def _fit_tables_height(self) -> None:
        """Высота основной таблицы — по числу строк, но не больше
        _MAX_VISIBLE_ROWS (дальше прокрутка). Одна на оба вида, чтобы
        переключение "По станциям / По файлам" не двигало всё, что ниже."""
        table = self.stations_table
        rows = max(len(self.stations_model.rows), len(self.files_model.rows))
        self._tables.setFixedHeight(
            table.horizontalHeader().sizeHint().height()
            + min(rows, _MAX_VISIBLE_ROWS) * table.verticalHeader().defaultSectionSize()
            + 2 * table.frameWidth()
        )

    def _select_station(self, wmo_index: int | None) -> None:
        """Выделить строку станции (прежней — после "Обновить", иначе первой)."""
        rows = self.stations_model.rows
        source_row = next((i for i, row in enumerate(rows) if row.wmo_index == wmo_index), 0)
        proxy = self.stations_table.model()
        self.stations_table.selectRow(proxy.mapFromSource(self.stations_model.index(source_row, 0)).row())

    def _on_table_mode(self, mode: int, checked: bool) -> None:
        if checked:
            self._tables.setCurrentIndex(mode)

    def _on_row_selected(self, row: StationSourceRow | LoadedFile | None) -> None:
        if row is None or row.wmo_index == self._detail_wmo:
            return
        wmo_index = row.wmo_index
        label = row.station_label if isinstance(row, LoadedFile) else row.label
        self._detail_wmo = wmo_index
        self._start(
            lambda: fetch_station_detail(self.engine, wmo_index),
            lambda detail: self._show_detail(label, detail),
        )

    def _show_detail(self, label: str, detail: StationSourceDetail) -> None:
        self._detail = detail
        self._station_label.setText(label)
        self.day_gaps_model.set_rows(detail.day_gaps)
        self.obs_gaps_model.set_rows(detail.obs_gaps)
        self._update_gaps_note()
        self._plot_current_chart()

    def _on_gaps_mode(self, mode: int, checked: bool) -> None:
        if checked:
            self._gap_tables.setCurrentIndex(mode)
            self._update_gaps_note()

    def _update_gaps_note(self) -> None:
        detail = self._detail
        if detail is None:
            return
        if self.gaps_mode.checkedId() == 0:
            self._gaps_note.setText(day_gaps_note(detail.day_gaps))
        else:
            self._gaps_note.setText(obs_gaps_note(detail.obs_gaps, detail))

    def _on_chart_mode(self, mode: int, checked: bool) -> None:
        if checked:
            self._charts.setCurrentIndex(mode)
            self._chart_title.setText(_CHART_TITLES[mode])
            self._chart_note.setText(_CHART_NOTES[mode])
            self._plot_current_chart()

    def _plot_current_chart(self) -> None:
        """Рисуется только видимый график — и только если он ещё не построен
        по текущей станции: три matplotlib-графика на каждый щелчок по
        таблице заметно тормозили бы выбор станции."""
        mode = self.chart_mode.checkedId()
        if self._detail is None or self._plotted.get(mode) is self._detail:
            return
        self._plotted[mode] = self._detail
        self.charts[mode].plot(self._detail)

