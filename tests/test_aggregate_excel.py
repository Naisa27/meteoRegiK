from __future__ import annotations

import openpyxl

from meteoregik.export.aggregate_excel import export_aggregate_view
from meteoregik.reports.chart_data import ChartData
from meteoregik.reports.grid import Cell, Grid, Row

AVG = ("T", "avg")
MIN = ("T", "min")


def _sample_grid() -> Grid:
    return Grid(
        rows=(
            Row(cells=(Cell("2020 год", align="left", bold=True), Cell())),
            Row(cells=(Cell("t средняя", align="left"), Cell(6.83)), border_bottom=True),
        ),
        col_count=2,
    )


def _sample_chart_data() -> ChartData:
    return ChartData(x_labels=["2020", "2021"], series={AVG: [5.9, 7.3], MIN: [-10.0, -8.0]})


def test_export_table_primary_writes_grid_and_chart_sheets(tmp_path):
    out_path = tmp_path / "export.xlsx"
    export_aggregate_view(
        out_path=out_path,
        station_title="Мосальск",
        primary="table",
        grid=_sample_grid(),
        chart_data=_sample_chart_data(),
        parameters=[AVG, MIN],
    )

    wb = openpyxl.load_workbook(out_path)
    assert wb.sheetnames == ["Данные", "График"]

    data_ws = wb["Данные"]
    assert data_ws.cell(row=1, column=1).value == "Мосальск"
    assert data_ws.cell(row=2, column=1).value == "2020 год"
    assert data_ws.cell(row=3, column=2).value == 6.83

    chart_ws = wb["График"]
    assert chart_ws.cell(row=1, column=1).value == "Мосальск"
    assert chart_ws.cell(row=2, column=1).value == "Период"
    assert chart_ws.cell(row=3, column=1).value == "2020"
    assert len(chart_ws._charts) == 1


def test_export_chart_primary_writes_chart_and_flat_table_sheets(tmp_path):
    out_path = tmp_path / "export.xlsx"
    export_aggregate_view(
        out_path=out_path,
        station_title="Мосальск",
        primary="chart",
        grid=None,
        chart_data=_sample_chart_data(),
        parameters=[AVG, MIN],
    )

    wb = openpyxl.load_workbook(out_path)
    assert wb.sheetnames == ["График", "Данные"]
    assert len(wb["График"]._charts) == 1
    assert wb["Данные"].cell(row=3, column=1).value == "2020"


def test_export_table_primary_requires_grid(tmp_path):
    import pytest

    with pytest.raises(ValueError):
        export_aggregate_view(
            out_path=tmp_path / "export.xlsx",
            station_title="Мосальск",
            primary="table",
            grid=None,
            chart_data=_sample_chart_data(),
            parameters=[AVG],
        )
