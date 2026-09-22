"""Экспорт результата экрана "Просмотр агрегатных данных" в Excel —
потребляет тот же reports.grid.Grid/reports.chart_data.ChartData, что и
Qt-рендерер экрана (ui/aggregate_table_view.py, ui/aggregate_chart_view.py),
без повторной сборки раскладки.

Экспортируется только текущий видимый на экране режим (одна из "Подробно"/
"Помесячно"/"По годам", либо график) — не все три листа примера сразу
(пример .claude/docs/пример_вывода_агрегатных_данных.xlsx показывает три
раскладки для справки, а не формат единого экспорта). Второй лист книги —
всегда обратное представление тех же данных: если на экране была таблица —
рядом откладывается график, и наоборот."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from openpyxl import Workbook
from openpyxl.chart import LineChart, Reference
from openpyxl.styles import Alignment, Border, Font, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from meteoregik.reports.chart_data import ChartData
from meteoregik.reports.grid import Grid
from meteoregik.reports.parameters import ParamKey, label_for

_TITLE_FONT = Font(bold=True, size=12)
_HEADER_FONT = Font(bold=True)
_BOTTOM_BORDER = Border(bottom=Side(style="thin"))


def _write_title(ws: Worksheet, station_title: str, col_count: int) -> None:
    ws.cell(row=1, column=1, value=station_title)
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=max(col_count, 1))
    cell = ws.cell(row=1, column=1)
    cell.font = _TITLE_FONT
    cell.alignment = Alignment(horizontal="center")


def _write_grid_sheet(ws: Worksheet, grid: Grid, station_title: str) -> None:
    _write_title(ws, station_title, grid.col_count)

    for row_index, row in enumerate(grid.rows):
        excel_row = row_index + 2
        for col_index, grid_cell in enumerate(row.cells):
            cell = ws.cell(row=excel_row, column=col_index + 1, value=grid_cell.value)
            cell.alignment = Alignment(horizontal=grid_cell.align)
            if grid_cell.bold:
                cell.font = _HEADER_FONT
            if isinstance(grid_cell.value, float):
                cell.number_format = "0.00"
        if row.border_bottom:
            for col_index in range(grid.col_count):
                ws.cell(row=excel_row, column=col_index + 1).border = _BOTTOM_BORDER


def _write_flat_table(ws: Worksheet, chart_data: ChartData, parameters: list[ParamKey], station_title: str) -> int:
    """Плоская таблица "период × показатель" — таблица-источник для
    LineChart и представление chart_data, когда именно график был основным
    видом на экране. Возвращает номер строки-шапки (нужен вызывающей
    стороне, чтобы построить Reference для нативного графика)."""
    col_count = 1 + len(parameters)
    _write_title(ws, station_title, col_count)

    header_row = 2
    ws.cell(row=header_row, column=1, value="Период").font = _HEADER_FONT
    for i, key in enumerate(parameters):
        cell = ws.cell(row=header_row, column=2 + i, value=label_for(key))
        cell.font = _HEADER_FONT

    for offset, x_label in enumerate(chart_data.x_labels):
        row = header_row + 1 + offset
        ws.cell(row=row, column=1, value=x_label)
        for i, key in enumerate(parameters):
            values = chart_data.series.get(key, [])
            value = values[offset] if offset < len(values) else None
            cell = ws.cell(row=row, column=2 + i, value=value)
            if isinstance(value, float):
                cell.number_format = "0.00"

    return header_row


def _write_chart_sheet(ws: Worksheet, chart_data: ChartData, parameters: list[ParamKey], station_title: str) -> None:
    header_row = _write_flat_table(ws, chart_data, parameters, station_title)
    if not chart_data.x_labels or not parameters:
        return

    last_row = header_row + len(chart_data.x_labels)
    chart = LineChart()
    chart.title = station_title
    chart.style = 2
    categories = Reference(ws, min_col=1, min_row=header_row + 1, max_row=last_row)
    data = Reference(ws, min_col=2, max_col=1 + len(parameters), min_row=header_row, max_row=last_row)
    chart.add_data(data, titles_from_data=True)
    chart.set_categories(categories)

    anchor_col = get_column_letter(len(parameters) + 3)
    ws.add_chart(chart, f"{anchor_col}2")


def export_aggregate_view(
    *,
    out_path: Path,
    station_title: str,
    primary: Literal["table", "chart"],
    grid: Grid | None,
    chart_data: ChartData,
    parameters: list[ParamKey],
    sheet_titles: tuple[str, str] = ("Данные", "График"),
) -> None:
    data_sheet_title, chart_sheet_title = sheet_titles
    wb = Workbook()

    if primary == "table":
        if grid is None:
            raise ValueError("grid обязателен, когда primary == 'table'")
        ws_data = wb.active
        ws_data.title = data_sheet_title
        _write_grid_sheet(ws_data, grid, station_title)
        ws_chart = wb.create_sheet(chart_sheet_title)
        _write_chart_sheet(ws_chart, chart_data, parameters, station_title)
    else:
        ws_chart = wb.active
        ws_chart.title = chart_sheet_title
        _write_chart_sheet(ws_chart, chart_data, parameters, station_title)
        ws_table = wb.create_sheet(data_sheet_title)
        _write_flat_table(ws_table, chart_data, parameters, station_title)

    wb.save(out_path)
