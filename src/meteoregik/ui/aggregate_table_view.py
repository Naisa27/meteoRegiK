"""Qt-рендерер reports.grid.Grid в QTableView — единственное, что здесь
Qt-специфично поверх "сырых" данных Grid: форматирование чисел, выравнивание,
жирность, цвета текста/заливки и линии. Сам Grid ничего не знает про Qt (см.
reports/grid.py).

Встроенная сетка таблицы выключена (setShowGrid(False)) — она рисуется
по всему полю, включая пустые строки-разделители и колонки за краем таблицы
(правка 2026-09-25, п.2: "это же не Excel"). Линии рисует делегат, и только
у ячеек, входящих в таблицу: тонкая сетка + жирный контур по сторонам из
Cell.thick. QSS тут не помогает — он не умеет задавать границы отдельным
ячейкам.

Модель (GridTableModel) отдаёт ячейки Grid по запросу, а не QTableWidget с
отдельным QTableWidgetItem на каждую ячейку (правка 2026-09-25, п.6): на
"Подробно" за 20 лет это ~50 тыс. объектов и ~1 с заморозки интерфейса
только на их создание — модель показывает те же данные мгновенно."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QPersistentModelIndex, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QStyleOptionViewItem, QStyledItemDelegate, QTableView

from meteoregik.reports import table_palette
from meteoregik.reports.grid import Align, Cell, Fill, Grid

# Ширина дневных/декадных колонок в режиме "Подробно" достаточно узкая, чтобы
# все 36 колонок не растягивали диалог до абсурдной ширины — минимум, не
# максимум (в отличие от MAX_COLUMN_WIDTH в data_browser.py: там текстовые
# ячейки могут быть длинными, здесь наоборот, значения короткие).
MIN_COLUMN_WIDTH = 46
THICK_WIDTH = 2
CELL_PADDING = 6

IN_TABLE_ROLE = Qt.ItemDataRole.UserRole + 1
THICK_ROLE = Qt.ItemDataRole.UserRole + 2  # строка из букв сторон: "t", "b", "l", "r"

_ALIGN_MAP: dict[Align, Qt.AlignmentFlag] = {
    "left": Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
    "center": Qt.AlignmentFlag.AlignCenter,
    "right": Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
}

_FILL_COLORS: dict[Fill, str] = {
    "decade": table_palette.DECADE_FILL,
    "total": table_palette.TOTAL_FILL,
}


def format_cell_value(value: float | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


class _TableLinesDelegate(QStyledItemDelegate):
    """Дорисовывает поверх обычной отрисовки ячейки её линии: тонкую сетку
    (IN_TABLE_ROLE) и жирные стороны контура (THICK_ROLE).

    Тонкую линию каждая ячейка рисует справа и снизу, а верх/лево — только
    если соседняя ячейка сверху/слева не входит в таблицу (иначе общая линия
    двух соседей рисовалась бы дважды, толщиной 2px). Жирную сторону рисует сама ячейка-владелец, внутри своего
    прямоугольника: у соседа по этой стороне такой же линии нет (Grid задаёт
    контур ровно один раз), поэтому толщина не удваивается."""

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> None:
        super().paint(painter, option, index)
        if not index.data(IN_TABLE_ROLE):
            return
        rect = option.rect
        painter.save()
        painter.setPen(QPen(QColor(table_palette.THIN_BORDER), 1))
        painter.drawLine(rect.topRight(), rect.bottomRight())
        painter.drawLine(rect.bottomLeft(), rect.bottomRight())
        if index.row() == 0 or not index.siblingAtRow(index.row() - 1).data(IN_TABLE_ROLE):
            painter.drawLine(rect.topLeft(), rect.topRight())
        if index.column() == 0 or not index.siblingAtColumn(index.column() - 1).data(IN_TABLE_ROLE):
            painter.drawLine(rect.topLeft(), rect.bottomLeft())

        sides = index.data(THICK_ROLE) or ""
        if sides:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(table_palette.THICK_BORDER))
            w = THICK_WIDTH
            if "t" in sides:
                painter.drawRect(rect.left(), rect.top(), rect.width(), w)
            if "b" in sides:
                painter.drawRect(rect.left(), rect.bottom() - w + 1, rect.width(), w)
            if "l" in sides:
                painter.drawRect(rect.left(), rect.top(), w, rect.height())
            if "r" in sides:
                painter.drawRect(rect.right() - w + 1, rect.top(), w, rect.height())
        painter.restore()


class GridTableModel(QAbstractTableModel):
    """Только чтение: ячейки Grid с их оформлением через роли Qt."""

    def __init__(self, grid: Grid, parent=None) -> None:
        super().__init__(parent)
        self._grid = grid
        self._bold_font = QFont()
        self._bold_font.setBold(True)
        self._colors = {
            None: QBrush(QColor(table_palette.TEXT)),
            "accent": QBrush(QColor(table_palette.ACCENT_TEXT)),
        }
        self._fills = {fill: QBrush(QColor(color)) for fill, color in _FILL_COLORS.items()}

    @property
    def grid(self) -> Grid:
        return self._grid

    def rowCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._grid.rows)

    def columnCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else self._grid.col_count

    def flags(self, index: QModelIndex | QPersistentModelIndex) -> Qt.ItemFlag:
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    def data(self, index: QModelIndex | QPersistentModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None
        cell = self._grid.rows[index.row()].cells[index.column()]
        if role == Qt.ItemDataRole.DisplayRole:
            return format_cell_value(cell.value)
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return _ALIGN_MAP[cell.align]
        if role == Qt.ItemDataRole.FontRole:
            return self._bold_font if cell.bold else None
        if role == Qt.ItemDataRole.ForegroundRole:
            return self._colors[cell.color]
        if role == Qt.ItemDataRole.BackgroundRole:
            return self._fills[cell.fill] if cell.fill is not None else None
        if role == IN_TABLE_ROLE:
            return True if cell.in_table else None
        if role == THICK_ROLE:
            return "".join(side[0] for side in sorted(cell.thick)) if cell.thick else None
        return None


def _column_widths(view: QTableView, grid: Grid) -> list[int]:
    """Ширины колонок по самому длинному тексту — вместо
    resizeColumnsToContents(), который опрашивает модель по каждой ячейке и
    на больших выборках заметно дольше прямого подсчёта по Grid."""
    normal = QFontMetrics(view.font())
    bold_font = QFont(view.font())
    bold_font.setBold(True)
    bold = QFontMetrics(bold_font)
    widths = [MIN_COLUMN_WIDTH] * grid.col_count
    for row in grid.rows:
        for col, cell in enumerate(row.cells):
            if cell.value is None:
                continue
            metrics = bold if cell.bold else normal
            width = metrics.horizontalAdvance(format_cell_value(cell.value)) + 2 * CELL_PADDING + 8
            if width > widths[col]:
                widths[col] = width
    return widths


def show_grid(view: QTableView, grid: Grid) -> None:
    """Показывает grid в view (заменяя прежнюю модель) — вызывающая сторона
    решает, когда перерисовывать (после "Применить" или смены вида/типа
    вывода/показателей)."""
    view.setShowGrid(False)
    if not isinstance(view.itemDelegate(), _TableLinesDelegate):
        view.setItemDelegate(_TableLinesDelegate(view))
    # Отступ текста от линий: без него числа, выровненные вправо, упирались в
    # жирный контур итогового столбца.
    view.setStyleSheet(
        "QTableView { background: #FFFFFF; border: none; }"
        f"QTableView::item {{ padding: 0 {CELL_PADDING}px; }}"
    )
    view.horizontalHeader().hide()
    view.verticalHeader().hide()

    old_model = view.model()
    view.setModel(GridTableModel(grid, view))
    if old_model is not None:
        old_model.deleteLater()

    header = view.horizontalHeader()
    for section, width in enumerate(_column_widths(view, grid)):
        header.resizeSection(section, width)
