"""Qt-рендерер reports.grid.Grid в QTableWidget — единственное, что здесь
Qt-специфично поверх "сырых" данных Grid: форматирование чисел, выравнивание,
жирность и черта-разделитель под годовым блоком (через QStyledItemDelegate,
т.к. QSS не даёт надёжно раскрасить border-bottom только у конкретных строк
QTableWidgetItem). Сам Grid ничего не знает про Qt (см. reports/grid.py)."""

from __future__ import annotations

from PySide6.QtCore import QModelIndex, QPersistentModelIndex, Qt
from PySide6.QtGui import QFont, QPainter, QPen
from PySide6.QtWidgets import QStyleOptionViewItem, QStyledItemDelegate, QTableWidget, QTableWidgetItem

from meteoregik.reports.grid import Align, Cell, Grid
from meteoregik.ui import theme

# Ширина дневных/декадных колонок в режиме "Подробно" достаточно узкая, чтобы
# все 36 колонок не растягивали диалог до абсурдной ширины — минимум, не
# максимум (в отличие от MAX_COLUMN_WIDTH в data_browser.py: там текстовые
# ячейки могут быть длинными, здесь наоборот, значения короткие).
MIN_COLUMN_WIDTH = 46

BORDER_ROLE = Qt.ItemDataRole.UserRole + 1

_ALIGN_MAP: dict[Align, Qt.AlignmentFlag] = {
    "left": Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
    "center": Qt.AlignmentFlag.AlignCenter,
    "right": Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
}


def format_cell_value(value: float | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


class _BottomBorderDelegate(QStyledItemDelegate):
    """Дорисовывает линию понизу ячейки, если у неё выставлена BORDER_ROLE —
    визуальный разделитель между годовыми блоками в режиме "Подробно"."""

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> None:
        super().paint(painter, option, index)
        if not index.data(BORDER_ROLE):
            return
        painter.save()
        pen = QPen(theme.TEXT_SECONDARY)
        pen.setWidth(2)
        painter.setPen(pen)
        y = option.rect.bottom()
        painter.drawLine(option.rect.left(), y, option.rect.right(), y)
        painter.restore()


def _make_item(cell: Cell, border_bottom: bool) -> QTableWidgetItem:
    item = QTableWidgetItem(format_cell_value(cell.value))
    item.setTextAlignment(_ALIGN_MAP[cell.align])
    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
    if cell.bold:
        font = QFont()
        font.setBold(True)
        item.setFont(font)
    if border_bottom:
        item.setData(BORDER_ROLE, True)
    return item


def populate_table_widget(table: QTableWidget, grid: Grid) -> None:
    """Полностью перестраивает содержимое table по grid — вызывающая сторона
    решает, когда перерисовывать (обычно после "Применить")."""
    table.clear()
    table.setItemDelegate(_BottomBorderDelegate(table))
    table.horizontalHeader().hide()
    table.verticalHeader().hide()
    table.setRowCount(len(grid.rows))
    table.setColumnCount(grid.col_count)

    for row_index, row in enumerate(grid.rows):
        for col_index, cell in enumerate(row.cells):
            table.setItem(row_index, col_index, _make_item(cell, row.border_bottom))

    table.resizeColumnsToContents()
    header = table.horizontalHeader()
    for section in range(grid.col_count):
        if header.sectionSize(section) < MIN_COLUMN_WIDTH:
            header.resizeSection(section, MIN_COLUMN_WIDTH)
