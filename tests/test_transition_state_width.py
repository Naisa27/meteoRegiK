"""Состояние расчёта: длинный список сезонов читается и в обычном окне."""

from __future__ import annotations

from datetime import date

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QHeaderView

from meteoregik.calculations.transitions import StationState
from meteoregik.db.migrations import run_migrations
from meteoregik.db.session import get_engine
from meteoregik.ui.transitions_dialog import TransitionsDialog, _StateRow


def test_last_state_column_keeps_readable_width_and_wraps_full_text(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    engine = get_engine(tmp_path / "state.sqlite3")
    run_migrations(engine)
    dialog = TransitionsDialog(engine, run_async=False)
    try:
        text = ", ".join(f"весна и осень {year}" for year in range(1959, 2026))
        state = StationState(27707, (date(1959, 7, 1), date(2025, 2, 28)), None, text, 67, {})
        dialog.state_model.set_rows([_StateRow("Сухиничи (27707)", state)])
        dialog.tabs.setCurrentIndex(2)
        dialog.resize(1100, 650)
        dialog.show()
        app.processEvents()
        table = dialog.state_table
        header = table.horizontalHeader()
        assert header.sectionResizeMode(6) == QHeaderView.ResizeMode.Interactive
        assert table.columnWidth(6) >= 300
        assert table.textElideMode() == Qt.TextElideMode.ElideNone
        index = table.model().index(0, 6)
        assert index.data() == text
        assert table.rowHeight(0) > table.fontMetrics().height() * 2
        assert header.sectionViewportPosition(6) + table.columnWidth(6) <= table.viewport().width()
        before = table.columnWidth(6)
        dialog.resize(700, 650)
        app.processEvents()
        assert table.columnWidth(6) == before
        assert table.horizontalScrollBar().maximum() > 0
        table.scrollTo(index)
        app.processEvents()
        assert table.visualRect(index).intersects(table.viewport().rect())
    finally:
        dialog.close()
        engine.dispose()
