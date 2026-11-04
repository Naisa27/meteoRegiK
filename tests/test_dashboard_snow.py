"""Снег: неизвестные морозы, покрытие и полный список дат в экране/Excel."""

from __future__ import annotations

from datetime import date
from dataclasses import replace
from types import SimpleNamespace

from openpyxl import load_workbook
from PySide6.QtCore import QModelIndex, Qt, QPointF, QPoint
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication, QTableView, QPushButton

from meteoregik.export.snow_excel import export_snow
from meteoregik.reports.snow_data import SnowData, SnowPeriod, SnowHeatCell
from meteoregik.ui.dashboard_snow_tab import SnowTab
from meteoregik.ui.snow_view import SnowTableModel, SnowDialog
from meteoregik.ui.dashboard_snow_charts import SnowSeriesChart
from meteoregik.db.session import get_engine
from meteoregik.db.migrations import run_migrations


def _period() -> SnowPeriod:
    return SnowPeriod(date(2026, 2, 1), date(2026, 2, 28),
                      date(2026, 2, 3), date(2026, 2, 27), 12, 42.,
                      (date(2026, 2, 12), date(2026, 2, 13), date(2026, 2, 21)),
                      None, 0)


def test_model_keeps_unknown_frost_and_separate_dates() -> None:
    app = QApplication.instance() or QApplication([])
    model = SnowTableModel()
    model.set_periods((_period(),))
    assert model.rowCount() == 1
    assert model.rowCount(model.index(0, 0)) == 0
    assert model.columnCount(model.index(0, 0)) == 0
    assert model.data(model.index(0, 4)) == "нет данных"
    assert model.data(model.index(0, 3)) == "12–13.02.2026, 21.02.2026"
    coverage = model.data(model.index(0, 5))
    assert "Доступная часть: 03.02.2026–27.02.2026" in coverage
    assert "без температуры: 25 дн." in coverage
    assert "−20" in model.data(model.index(0, 4), Qt.ItemDataRole.ToolTipRole)
    assert model.data(QModelIndex()) is None
    assert app is not None


def test_tab_contains_charts_and_no_tables_or_export() -> None:
    app = QApplication.instance() or QApplication([])
    tab = SnowTab()
    period = _period()
    tab.show_data("Мосальск", SnowData(period, (period,), (period,)))
    assert not tab.findChildren(QTableView)
    assert not any(b.text() == "Экспорт в Excel" for b in tab.findChildren(QPushButton))
    assert tab.cards[2].value.text() == "нет данных"
    assert len(tab.count_chart.periods) == 1
    tab.set_loading(True)
    assert not tab.isEnabled()
    tab.set_loading(False)
    assert tab.isEnabled()
    tab.clear_results("Нет данных другой станции")
    assert not tab.count_chart.periods
    assert tab.stack.currentWidget() is tab.hint
    tab.show_data("Нет rp5", SnowData(None))
    assert tab._data is None
    tab.close()
    assert app is not None


def test_separate_window_keeps_tables_and_export(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    engine = get_engine(tmp_path / "snow.sqlite3")
    run_migrations(engine)
    window = SnowDialog(engine, run_async=False)
    window.resize(1100, 750)
    window.show()
    app.processEvents()
    assert window.width() == 1100
    assert window.height() == 750
    period = _period()
    window.show_data("Мосальск", SnowData(period, (period,), (period,)))
    assert window.month_model.rowCount() == window.season_model.rowCount() == 1
    assert window.export_btn.isEnabled()
    assert "width" in window.month_model.data(window.month_model.index(0, 3), Qt.ItemDataRole.ToolTipRole)
    window.show_data("Нет rp5", SnowData(None))
    assert not window.export_btn.isEnabled()
    assert window.month_model.rowCount() == 0
    window.close()
    engine.dispose()
    assert app is not None


def test_chart_exposes_whole_archive_and_wheel_propagates() -> None:
    app = QApplication.instance() or QApplication([])
    chart = SnowSeriesChart()
    periods = tuple(replace(_period(), start=date(2000 + i // 12, i % 12 + 1, 1)) for i in range(120))
    chart.plot(periods)
    assert len(chart.periods) == 120
    assert chart.scrollbar.maximum() == 60
    chart.scrollbar.setValue(0)
    assert chart._ax.get_xlim()[0] < 0
    wheel = QWheelEvent(QPointF(10, 10), QPointF(10, 10), QPoint(), QPoint(0, 120),
                        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                        Qt.ScrollPhase.NoScrollPhase, False)
    assert chart.eventFilter(chart.canvas, wheel)
    assert not wheel.isAccepted()
    chart.close()
    assert app is not None


def test_heatmap_switches_months_decades_and_anomalies() -> None:
    app = QApplication.instance() or QApplication([])
    tab = SnowTab()
    period = _period()
    cell = SnowHeatCell(period.start, period.end, 12, 10., 2., True, "По архиву, 10 лет")
    decade = replace(cell, end=date(2026, 2, 10), value=5)
    data = SnowData(period, (period,), (period,), (cell,), (decade,))
    tab.show_data("Мосальск", data)
    assert len(tab.heatmap._cells[0]) == 3
    tab.heatmap.set_mode("anomaly")
    text = tab.heatmap.tooltip_at(SimpleNamespace(xdata=.5, ydata=.5))
    assert "+2.0" in text and "10 лет" in text
    tab.close()
    assert app is not None


def test_wheel_over_chart_scrolls_page_in_both_directions() -> None:
    app = QApplication.instance() or QApplication([])
    tab = SnowTab()
    period = _period()
    tab.show_data("Мосальск", SnowData(period, (period,), (period,)))
    tab.resize(1100, 750)
    tab.show()
    app.processEvents()
    scrollbar = tab.content_scroll.verticalScrollBar()
    scrollbar.setValue(scrollbar.maximum() // 2)
    middle = scrollbar.value()
    for delta in (120, -120):
        event = QWheelEvent(QPointF(10, 10), QPointF(10, 10), QPoint(), QPoint(0, delta),
                            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                            Qt.ScrollPhase.NoScrollPhase, False)
        before = scrollbar.value()
        app.sendEvent(tab.count_chart.canvas, event)
        assert scrollbar.value() < before if delta > 0 else scrollbar.value() > before
    assert scrollbar.value() == middle
    tab.close()


def test_excel_preserves_dates_missing_temperature_and_coverage(tmp_path) -> None:
    period = _period()
    path = tmp_path / "snow.xlsx"
    export_snow(path, "Мосальск", SnowData(period, (period,), (period,)))
    workbook = load_workbook(path)
    assert workbook.sheetnames == ["По месяцам", "Зимние сезоны"]
    month = workbook["По месяцам"]
    assert month["B4"].value == 12
    assert month["C4"].value == 42
    assert month["D4"].value == "12–13.02.2026, 21.02.2026"
    assert month["E4"].value == "нет данных"
    assert "без температуры: 25 дн." in month["F4"].value
    assert "−20" in month["A2"].value
    assert month.freeze_panes == "B4"
    workbook.close()
