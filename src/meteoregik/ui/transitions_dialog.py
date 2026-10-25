"""Окно "Переходы температуры" раздела "Расчёт показателей" (корректировка
2026-10-25, решения пользователя 2026-10-26): даты устойчивого перехода
средней суточной температуры через 0, 5, 10, 15 °C весной и осенью и
длительность периодов выше этих порогов.

Сверху — выбор станции и "Экспорт в Excel", под ними три вкладки:
- "Таблица переходов" и "Таблица периодов" — выбранной станции, раскладка
  по примеру пользователя (reports/transitions_view.py);
- "Состояние расчёта" — по всем станциям: когда посчитано, сколько лет с
  датами, сколько сезонов "нет данных" и что пересчитано при последнем
  открытии.

Кнопок расчёта нет (решение пользователя): при открытии окна в фоне
досчитываются все сезоны, требующие расчёта (calculations/transitions.py:
импорт и правка только помечают сезоны), — под лоадером, затем окно
показывает результат. Сезон считается за миллисекунды, ждать заметно
приходится только после больших импортов.

Оформление — общая тема (theme.DIALOG_QSS). Один акцент — цвет сезонов в
шапке: весна светло-зелёная, осень янтарная; пороги идут как годовой ход —
вверх весной, вниз осенью. Ячейка без даты — с текстом ("нет данных" со
штриховкой, "не было"), не одним цветом, причина — в подсказке."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import QAbstractTableModel, QEvent, QModelIndex, QPersistentModelIndex, QRect, QSize, QStandardPaths, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
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

from meteoregik.calculations import transitions as tr
from meteoregik.export.transitions_excel import AUTUMN_FILL, HATCH_COLOR, SPRING_FILL, export_transitions
from meteoregik.preferences import Preferences
from meteoregik.reports import table_palette
from meteoregik.reports.transitions_view import (
    KIND_NO_DATA,
    KIND_NONE,
    YEAR_TITLE,
    Table,
    periods_table,
    transitions_table,
)
from meteoregik.stations.choices import load_stations_with_computed_indicators
from meteoregik.ui import theme
from meteoregik.ui.background import run_in_background
from meteoregik.ui.buttons import reserve_focus_width
from meteoregik.ui.dashboard_source_tab import Column, _make_table, format_period
from meteoregik.ui.dialogs import enable_min_max_buttons, minimize_owner_instead
from meteoregik.ui.empty_state import NO_DATA_HINT, hint_label
from meteoregik.ui.loading_overlay import LoadingOverlay
from meteoregik.ui.query_panel import _FORBIDDEN_FILENAME_CHARS
from meteoregik.ui.station_combo import StationCombo

TITLE = "Переходы температуры"
TAB_TRANSITIONS = "Таблица переходов"
TAB_PERIODS = "Таблица периодов"
TAB_STATE = "Состояние расчёта"
_INTRO = (
    "Даты устойчивого перехода средней суточной температуры через 0, 5, 10 и 15 °C весной и осенью "
    "(метод сумм отклонений Д. А. Педя) и длительность периодов выше этих порогов. "
    "После импорта или правки данных пересчитываются только затронутые сезоны — при открытии этого окна."
)
_START_HINT = "Выберите метеостанцию — здесь появятся её даты переходов по годам."
_NO_VALUE = "—"
_EXPORT_DIR_KEY = "transitions.export_dir"

TRANSITIONS_QSS = f"""
QLabel#calcIntro {{ color: {theme.TEXT_SECONDARY}; font-size: 12px; }}
QLabel#stationTitle {{ font-weight: 600; font-size: 14px; }}
QLabel#calcSummary {{ color: {theme.TEXT_PRIMARY}; font-size: 13px; font-weight: 600; }}
"""

_MEAN_FILL = "#EEF2F4"


# --- таблицы станции ---


class TableModel(QAbstractTableModel):
    """Таблица reports.transitions_view.Table: первый столбец — год."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.table = Table([])

    def set_table(self, table: Table) -> None:
        self.beginResetModel()
        self.table = table
        self.endResetModel()

    def rowCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:  # noqa: N802, B008
        return 0 if parent.isValid() else len(self.table.rows)

    def columnCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:  # noqa: N802, B008
        return 0 if parent.isValid() else len(self.table.columns) + 1

    def data(self, index: QModelIndex | QPersistentModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None
        row = self.table.rows[index.row()]
        if index.column() == 0:
            if role == Qt.ItemDataRole.DisplayRole:
                return row.label
            if role == Qt.ItemDataRole.ForegroundRole:
                return QColor(table_palette.ACCENT_TEXT)
            if role == Qt.ItemDataRole.FontRole:
                return _bold()
            if role == Qt.ItemDataRole.TextAlignmentRole:
                return int(Qt.AlignmentFlag.AlignCenter)
            return None
        cell = row.cells[index.column() - 1]
        if role == Qt.ItemDataRole.DisplayRole:
            return cell.text
        if role == Qt.ItemDataRole.ToolTipRole:
            return cell.tip or None
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return int(Qt.AlignmentFlag.AlignCenter)
        if role == Qt.ItemDataRole.ForegroundRole and cell.kind in (KIND_NONE, KIND_NO_DATA):
            return QColor(theme.TEXT_SECONDARY)
        if role == Qt.ItemDataRole.FontRole and row.mean:
            return _bold()
        return None

    def headerData(  # noqa: N802
        self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole
    ) -> Any:
        if orientation != Qt.Orientation.Horizontal:
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            return YEAR_TITLE if section == 0 else self.table.columns[section - 1].title
        if role == Qt.ItemDataRole.ToolTipRole and section > 0:
            return self.table.columns[section - 1].tip or None
        return None

    def is_mean(self, row: int) -> bool:
        return self.table.rows[row].mean

    def is_no_data(self, row: int, column: int) -> bool:
        return column > 0 and self.table.rows[row].cells[column - 1].kind == KIND_NO_DATA


def _bold() -> QFont:
    font = QFont()
    font.setBold(True)
    return font


class CellDelegate(QStyledItemDelegate):
    """Штриховка "нет данных", заливка и черта над строкой средних — как
    черта между годовыми блоками у таблиц агрегатов."""

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        model: TableModel = index.model()
        if model.is_mean(index.row()):
            painter.fillRect(option.rect, QColor(_MEAN_FILL))
        elif model.is_no_data(index.row(), index.column()):
            painter.fillRect(option.rect, QBrush(QColor(HATCH_COLOR), Qt.BrushStyle.BDiagPattern))
        super().paint(painter, option, index)
        if model.is_mean(index.row()):
            painter.save()
            painter.setPen(QPen(QColor(table_palette.THICK_BORDER), 2))
            painter.drawLine(option.rect.topLeft(), option.rect.topRight())
            painter.restore()


class SeasonHeader(QHeaderView):
    """Шапка в два уровня: сверху группа ("Весна", "Осень", "Длительность,
    дни"), снизу порог; столбец "Год" — во всю высоту. Рисуется целиком
    вручную: стиль Qt не умеет ни объединённых ячеек, ни своего цвета у
    отдельной секции, а цвет сезона — главный ориентир таблицы."""

    _PADDING = 8

    def __init__(self, model: TableModel, parent: QWidget | None = None) -> None:
        super().__init__(Qt.Orientation.Horizontal, parent)
        self._model = model
        self.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.sectionResized.connect(self.viewport().update)

    def _line(self) -> int:
        return self.fontMetrics().height() + 2 * self._PADDING

    def sizeHint(self) -> QSize:  # noqa: N802 — имя метода Qt
        size = super().sizeHint()
        lines = max((col.title.count("\n") + 1 for col in self._model.table.columns), default=1)
        size.setHeight(self._line() + lines * self.fontMetrics().height() + 2 * self._PADDING)
        return size

    def _fill(self, section: int) -> QColor:
        if section == 0:
            return QColor(theme.HEADER_BG)
        season = self._model.table.columns[section - 1].season
        if season == tr.SPRING:
            return QColor(SPRING_FILL)
        if season == tr.AUTUMN:
            return QColor(AUTUMN_FILL)
        return QColor(theme.HEADER_BG)

    def _cell(self, painter: QPainter, rect: QRect, text: str, fill: QColor) -> None:
        painter.fillRect(rect, fill)
        painter.setPen(QPen(QColor(theme.HEADER_BORDER), 1))
        painter.drawLine(rect.topRight(), rect.bottomRight())
        painter.drawLine(rect.bottomLeft(), rect.bottomRight())
        painter.setPen(QColor(theme.TEXT_PRIMARY))
        painter.drawText(rect.adjusted(4, 0, -4, 0), int(Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap), text)

    def paintSection(self, painter: QPainter, rect: QRect, logicalIndex: int) -> None:  # noqa: N802, N803
        painter.save()
        painter.setFont(_bold())
        title = self._model.headerData(logicalIndex, Qt.Orientation.Horizontal) or ""
        if logicalIndex == 0:
            self._cell(painter, rect, title, self._fill(0))
            painter.restore()
            return
        columns = self._model.table.columns
        group = columns[logicalIndex - 1].group
        top = self._line()
        self._cell(painter, rect.adjusted(0, top, 0, 0), title, self._fill(logicalIndex))
        # Ячейка группы — над первой секцией группы на всю её ширину
        first = logicalIndex
        while first > 1 and columns[first - 2].group == group:
            first -= 1
        last = logicalIndex
        while last < len(columns) and columns[last].group == group:
            last += 1
        group_rect = QRect(
            self.sectionViewportPosition(first), rect.top(),
            sum(self.sectionSize(section) for section in range(first, last + 1)), top,
        )
        painter.setClipRect(rect)
        self._cell(painter, group_rect, group, self._fill(logicalIndex))
        painter.restore()


def _station_table() -> tuple[QTableView, TableModel]:
    model = TableModel()
    table = QTableView()
    table.setModel(model)
    table.setHorizontalHeader(SeasonHeader(model, table))
    table.setItemDelegate(CellDelegate(table))
    table.verticalHeader().hide()
    table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    table.setAlternatingRowColors(True)
    table.verticalHeader().setDefaultSectionSize(30)
    return table, model


# --- состояние расчёта ---


@dataclass(frozen=True)
class _StateRow:
    label: str
    state: tr.StationState


def _moment(moment: datetime | None) -> str:
    return f"{moment:%d.%m.%Y %H:%M}" if moment else "не рассчитано"


STATE_COLUMNS = [
    Column("Станция", lambda r: r.label, lambda r: r.label.casefold()),
    Column("Период данных", lambda r: format_period(r.state.period), lambda r: r.state.period[0].toordinal(),
           tip="Первый и последний день со средней суточной температурой"),
    Column("Рассчитано", lambda r: _moment(r.state.calculated_at),
           lambda r: r.state.calculated_at.timestamp() if r.state.calculated_at else 0.0,
           tip="Когда переходы станции пересчитывались в последний раз"),
    Column("Лет с датами", lambda r: str(r.state.years_with_dates), lambda r: r.state.years_with_dates,
           numeric=True, tip="Лет, где посчитана хотя бы одна дата перехода"),
    Column("«Нет данных»\nвёсен", lambda r: str(r.state.no_data_seasons.get(tr.SPRING, 0)),
           lambda r: r.state.no_data_seasons.get(tr.SPRING, 0), numeric=True,
           tip="Сезонов, где в окне поиска есть неполный месяц — дата не считается"),
    Column("«Нет данных»\nосеней", lambda r: str(r.state.no_data_seasons.get(tr.AUTUMN, 0)),
           lambda r: r.state.no_data_seasons.get(tr.AUTUMN, 0), numeric=True,
           tip="Сезонов, где в окне поиска есть неполный месяц — дата не считается"),
    Column("Пересчитано в последний раз", lambda r: r.state.recalculated or _NO_VALUE,
           lambda r: r.state.recalculated,
           tip="Какие сезоны пересчитаны при последнем расчёте станции"),
]


def summary_text(states: list[tr.StationState], recalculated: int) -> str:
    text = f"Станций с данными: {len(states)}."
    if recalculated:
        return text + f" При этом открытии пересчитаны сезоны {recalculated} из них."
    return text + " Все даты уже были посчитаны."


# --- окно ---


class TransitionsDialog(QDialog):
    def __init__(self, engine: Engine, parent: QWidget | None = None, run_async: bool = True) -> None:
        super().__init__(parent)
        enable_min_max_buttons(self)
        self.engine = engine
        self._run_async = run_async  # False — расчёт в том же потоке (тесты)
        self._preferences = Preferences.next_to_database(engine.url.database)
        self._calculated = False
        self._recalculated = 0
        self.setWindowTitle(TITLE)
        self.resize(parent.size() if parent is not None else QSize(1100, 750))
        self.setStyleSheet(theme.DIALOG_QSS + TRANSITIONS_QSS)
        self._build_ui()

        choices = load_stations_with_computed_indicators(engine)
        self.station_combo.set_choices(choices)
        self.station_combo.currentIndexChanged.connect(self._show_station)
        if not choices:
            for hint in self._hints:
                hint.setText(NO_DATA_HINT)
        self._calculate()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 14, 20, 16)
        layout.setSpacing(10)

        intro = QLabel(_INTRO)
        intro.setObjectName("calcIntro")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        card = QFrame()
        card.setObjectName("card")
        row = QHBoxLayout(card)
        row.setContentsMargins(16, 12, 16, 12)
        row.setSpacing(10)
        row.addWidget(QLabel("Метеостанция:"))
        self.station_combo = StationCombo()
        self.station_combo.setEnabled(False)  # до конца расчёта
        row.addWidget(self.station_combo, stretch=1)
        self.export_btn = QPushButton("Экспорт в Excel")
        self.export_btn.setObjectName("secondary")
        self.export_btn.setToolTip("Обе таблицы выбранной станции — на двух листах")
        self.export_btn.setEnabled(False)
        self.export_btn.clicked.connect(self._export)
        row.addWidget(self.export_btn)
        layout.addWidget(card)

        self.station_title = QLabel("")
        self.station_title.setObjectName("stationTitle")
        layout.addWidget(self.station_title)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self._hints: list[QLabel] = []
        self.transitions_view, self.transitions_model = _station_table()
        self.periods_view, self.periods_model = _station_table()
        self._stacks: list[QStackedWidget] = []
        for view, title in ((self.transitions_view, TAB_TRANSITIONS), (self.periods_view, TAB_PERIODS)):
            stack = QStackedWidget()
            hint = hint_label(_START_HINT)
            stack.addWidget(hint)
            stack.addWidget(view)
            page = QWidget()
            page_layout = QVBoxLayout(page)
            page_layout.setContentsMargins(0, 10, 0, 0)
            page_layout.addWidget(stack)
            self.tabs.addTab(page, title)
            self._hints.append(hint)
            self._stacks.append(stack)
        self.tabs.addTab(self._build_state_tab(), TAB_STATE)

        results = QWidget()
        grid = QGridLayout(results)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.addWidget(self.tabs, 0, 0)
        self.loading_overlay = LoadingOverlay("Считаем даты переходов…")
        grid.addWidget(self.loading_overlay, 0, 0)
        layout.addWidget(results, stretch=1)

        close_row = QHBoxLayout()
        close_row.addStretch(1)
        self.close_btn = QPushButton("Закрыть")
        self.close_btn.setObjectName("secondary")
        self.close_btn.clicked.connect(self.accept)
        close_row.addWidget(self.close_btn)
        layout.addLayout(close_row)
        reserve_focus_width(self.export_btn, self.close_btn)

    def _build_state_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 10, 0, 0)
        layout.setSpacing(8)
        self.summary = QLabel("")
        self.summary.setObjectName("calcSummary")
        layout.addWidget(self.summary)
        self.state_table, self.state_model = _make_table(STATE_COLUMNS, page, stretch_column=6)
        layout.addWidget(self.state_table, stretch=1)
        return page

    # --- расчёт при открытии ---

    def _calculate(self) -> None:
        self.loading_overlay.show()
        self.loading_overlay.raise_()
        if self._run_async:
            run_in_background(self, lambda: tr.ensure_fresh(self.engine), self._on_calculated, self._on_failed)
        else:
            try:
                done = tr.ensure_fresh(self.engine)
            except Exception as exc:  # noqa: BLE001 — показать причину, как в фоне
                self._on_failed(exc)
            else:
                self._on_calculated(done)

    def _on_calculated(self, done: list[tr.StaleStation]) -> None:
        self.loading_overlay.hide()
        self._calculated = True
        self._recalculated = len(done)
        self.station_combo.setEnabled(self.station_combo.count() > 0)
        self._reload_state()
        self._show_station()

    def _on_failed(self, exc: BaseException) -> None:
        self.loading_overlay.hide()
        QMessageBox.warning(self, TITLE, f"Не удалось посчитать даты переходов: {exc}")

    def _reload_state(self) -> None:
        states = tr.station_states(self.engine)
        labels = {choice.wmo_index: choice.label for choice in load_stations_with_computed_indicators(self.engine)}
        self.state_model.set_rows([_StateRow(labels.get(s.wmo_index, str(s.wmo_index)), s) for s in states])
        self.summary.setText(summary_text(states, self._recalculated) if states else NO_DATA_HINT)

    # --- выбранная станция ---

    def _selected(self) -> int | None:
        try:
            return self.station_combo.selected_wmo()
        except ValueError:
            return None

    def _show_station(self) -> None:
        wmo = self._selected() if self._calculated else None
        self.export_btn.setEnabled(wmo is not None)
        if wmo is None:
            self.station_title.setText("")
            for stack in self._stacks:
                stack.setCurrentIndex(0)
            return
        data = tr.load_station_transitions(self.engine, wmo)
        days = tr.fetch_daily_means(self.engine, wmo)
        self.transitions_model.set_table(transitions_table(data, days))
        self.periods_model.set_table(periods_table(data, days))
        for view in (self.transitions_view, self.periods_view):
            view.horizontalHeader().updateGeometry()
        self.station_title.setText(self.station_combo.currentText())
        for stack in self._stacks:
            stack.setCurrentIndex(1)

    # --- экспорт ---

    def _export_dir(self) -> Path:
        saved = self._preferences.get(_EXPORT_DIR_KEY)
        if isinstance(saved, str) and Path(saved).is_dir():
            return Path(saved)
        return Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DownloadLocation))

    def _export(self) -> None:
        wmo = self._selected()
        if wmo is None:
            return
        name = self.station_combo.name_of(wmo) or str(wmo)
        filename = _FORBIDDEN_FILENAME_CHARS.sub("_", f"{name} ({wmo}) — переходы температуры") + ".xlsx"
        path, _ = QFileDialog.getSaveFileName(self, "Экспорт в Excel", str(self._export_dir() / filename),
                                              "Excel файлы (*.xlsx)")
        if not path:
            return
        if not path.lower().endswith(".xlsx"):
            path += ".xlsx"
        try:
            export_transitions(Path(path), self.station_combo.currentText(),
                               self.transitions_model.table, self.periods_model.table)
        except OSError as exc:
            QMessageBox.warning(self, "Экспорт в Excel", f"Не удалось сохранить файл: {exc}")
            return
        self._preferences.set(_EXPORT_DIR_KEY, str(Path(path).parent))
        QMessageBox.information(self, "Экспорт в Excel", "Файл сохранён.")

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        minimize_owner_instead(self, event)
