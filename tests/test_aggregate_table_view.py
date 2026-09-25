from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication, QTableView

from meteoregik.reports import table_palette
from meteoregik.reports.grid import Cell, Grid, Row
from meteoregik.ui.aggregate_table_view import (
    IN_TABLE_ROLE,
    MIN_COLUMN_WIDTH,
    THICK_ROLE,
    format_cell_value,
    show_grid,
)


def _qapp():
    return QApplication.instance() or QApplication([])


def _sample_grid() -> Grid:
    return Grid(
        rows=(
            Row(cells=(Cell("2020 год", align="left", bold=True), Cell())),
            Row(
                cells=(
                    Cell("январь", align="left", color="accent", in_table=True, thick=frozenset({"left", "top"})),
                    Cell(-8.11, fill="decade", in_table=True),
                )
            ),
        ),
        col_count=2,
    )


def _shown(grid: Grid | None = None) -> QTableView:
    _qapp()
    view = QTableView()
    show_grid(view, grid or _sample_grid())
    return view


def _data(view: QTableView, row: int, col: int, role=Qt.ItemDataRole.DisplayRole):
    model = view.model()
    return model.data(model.index(row, col), role)


def test_format_cell_value_rounds_floats_and_keeps_text():
    assert format_cell_value(-8.1) == "-8.10"
    assert format_cell_value(None) == ""
    assert format_cell_value("1 декада") == "1 декада"
    assert format_cell_value(2020) == "2020"


def test_show_grid_sets_dimensions_text_and_alignment():
    view = _shown()
    assert view.model().rowCount() == 2
    assert view.model().columnCount() == 2
    assert _data(view, 0, 0) == "2020 год"
    assert _data(view, 0, 0, Qt.ItemDataRole.FontRole).bold() is True
    assert _data(view, 1, 1) == "-8.11"
    assert int(_data(view, 1, 1, Qt.ItemDataRole.TextAlignmentRole)) & int(Qt.AlignmentFlag.AlignRight)
    assert int(_data(view, 1, 0, Qt.ItemDataRole.TextAlignmentRole)) & int(Qt.AlignmentFlag.AlignLeft)


def test_show_grid_marks_table_cells_and_thick_sides():
    view = _shown()
    assert _data(view, 0, 0, IN_TABLE_ROLE) is None  # вне таблицы — без сетки
    assert _data(view, 1, 0, IN_TABLE_ROLE) is True
    assert _data(view, 1, 0, THICK_ROLE) == "lt"
    assert _data(view, 1, 1, THICK_ROLE) is None


def test_show_grid_disables_builtin_grid():
    assert not _shown().showGrid()


def test_show_grid_applies_colors_and_fills():
    view = _shown()
    assert _data(view, 1, 0, Qt.ItemDataRole.ForegroundRole).color() == QColor(table_palette.ACCENT_TEXT)
    assert _data(view, 1, 1, Qt.ItemDataRole.ForegroundRole).color() == QColor(table_palette.TEXT)
    assert _data(view, 1, 1, Qt.ItemDataRole.BackgroundRole).color() == QColor(table_palette.DECADE_FILL)


def test_show_grid_cells_are_not_editable():
    view = _shown()
    model = view.model()
    assert not (model.flags(model.index(0, 0)) & Qt.ItemFlag.ItemIsEditable)


def test_show_grid_column_widths_fit_text_with_minimum():
    long_text = "очень длинная подпись показателя"
    view = _shown(Grid(rows=(Row(cells=(Cell(long_text), Cell(1.0))),), col_count=2))
    header = view.horizontalHeader()
    assert header.sectionSize(0) > view.fontMetrics().horizontalAdvance(long_text)
    assert header.sectionSize(1) >= MIN_COLUMN_WIDTH


def test_show_grid_replaces_previous_model():
    view = _shown()
    show_grid(view, Grid(rows=(Row(cells=(Cell("x"),)),), col_count=1))
    assert view.model().rowCount() == 1
    assert view.model().columnCount() == 1


def test_show_grid_grab_smoke():
    """Смоук: виджет не падает и рисуется (не пустой QPixmap)."""
    view = _shown()
    view.resize(200, 100)
    pixmap = view.grab()
    assert not pixmap.isNull()
    assert pixmap.width() > 0 and pixmap.height() > 0
