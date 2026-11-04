"""Выделение событий снега и закреплённые сводки при прокрутке."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QAbstractItemView

from meteoregik.reports.aggregate_query import MonthSlice, Stats, YearSlice
from meteoregik.reports.grid import build_detailed_grid, build_monthly_grid
from meteoregik.ui.aggregate_table_view import StickyAggregateTable

SNOW = ("S", "sum")
FROST = ("FROST_LOW_SNOW", "sum")


def test_snow_and_frost_highlights_survive_total_column_styling() -> None:
    month = MonthSlice(month_values=dict({FROST: 3.0}),
                       day_values={1: dict({SNOW: 1.0, FROST: 1.0}),
                                   2: dict({SNOW: 0.0, FROST: 0.0})})
    year = YearSlice(2025, dict({FROST: 3.0}), {1: month})
    grid = build_detailed_grid([year], [SNOW, FROST])
    cells = [cell for row in grid.rows for cell in row.cells]
    assert any(cell.value == "есть" and cell.fill == "snow" for cell in cells)
    assert all(cell.fill != "snow" for cell in cells if cell.value == "нет")
    assert any(cell.value == 3 and cell.fill == "snow" and cell.bold for cell in cells)
    monthly = build_monthly_grid([year], [SNOW, FROST])
    assert any(cell.value == 3 and cell.fill == "snow" and cell.bold
               for row in monthly.rows for cell in row.cells)


def test_sticky_summary_changes_with_visible_year_and_has_no_star() -> None:
    app = QApplication.instance() or QApplication([])
    key = ("T", "avg")
    years = [YearSlice(year, dict({key: float(year)}),
                       {month: MonthSlice(month_values=dict({key: 1.0})) for month in range(1, 13)})
             for year in (2024, 2025)]
    grid = build_detailed_grid(years, [key, ("T", "max")])
    widget = StickyAggregateTable()
    widget.resize(800, 400)
    widget.set_grid(grid)
    widget.show()
    app.processEvents()
    assert widget.summary.model().index(0, 0).data() == "2024 год"
    row = grid.sticky_sections[1][0] + 5
    widget.table.scrollTo(widget.table.model().index(row, 0), QAbstractItemView.ScrollHint.PositionAtTop)
    app.processEvents()
    assert widget.summary.model().index(0, 0).data() == "2025 год"
    assert "*" not in widget.summary.model().index(1, 1).data(Qt.ItemDataRole.DisplayRole)
    widget.table.verticalScrollBar().setValue(0)
    app.processEvents()
    assert widget.summary.model().index(0, 0).data() == "2024 год"
    widget.close()
