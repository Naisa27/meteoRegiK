from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QTableWidget

from meteoregik.reports.grid import Cell, Grid, Row
from meteoregik.ui.aggregate_table_view import BORDER_ROLE, format_cell_value, populate_table_widget


def _qapp():
    return QApplication.instance() or QApplication([])


def _sample_grid() -> Grid:
    return Grid(
        rows=(
            Row(cells=(Cell("2020 год", align="left", bold=True), Cell())),
            Row(cells=(Cell("t средняя", align="left"), Cell(-8.11)), border_bottom=True),
        ),
        col_count=2,
    )


def test_format_cell_value_rounds_floats_and_keeps_text():
    assert format_cell_value(-8.1) == "-8.10"
    assert format_cell_value(None) == ""
    assert format_cell_value("1 декада") == "1 декада"
    assert format_cell_value(2020) == "2020"


def test_populate_table_widget_sets_dimensions_text_and_alignment():
    _qapp()
    table = QTableWidget()
    populate_table_widget(table, _sample_grid())

    assert table.rowCount() == 2
    assert table.columnCount() == 2
    assert table.item(0, 0).text() == "2020 год"
    assert table.item(0, 0).font().bold() is True
    assert table.item(1, 1).text() == "-8.11"
    assert int(table.item(1, 1).textAlignment()) & int(Qt.AlignmentFlag.AlignRight)
    assert int(table.item(1, 0).textAlignment()) & int(Qt.AlignmentFlag.AlignLeft)


def test_populate_table_widget_marks_border_bottom_row_only():
    _qapp()
    table = QTableWidget()
    populate_table_widget(table, _sample_grid())

    assert table.item(0, 0).data(BORDER_ROLE) is None
    assert table.item(1, 0).data(BORDER_ROLE) is True
    assert table.item(1, 1).data(BORDER_ROLE) is True


def test_populate_table_widget_items_are_not_editable():
    _qapp()
    table = QTableWidget()
    populate_table_widget(table, _sample_grid())

    assert not (table.item(0, 0).flags() & Qt.ItemFlag.ItemIsEditable)


def test_populate_table_widget_grab_smoke():
    """Смоук: виджет не падает и рисуется (не пустой QPixmap)."""
    _qapp()
    table = QTableWidget()
    populate_table_widget(table, _sample_grid())
    table.resize(200, 100)

    pixmap = table.grab()
    assert not pixmap.isNull()
    assert pixmap.width() > 0
    assert pixmap.height() > 0
