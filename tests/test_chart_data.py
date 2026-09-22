from __future__ import annotations

from meteoregik.reports.aggregate_query import MonthSlice, YearSlice
from meteoregik.reports.chart_data import (
    chart_data_from_detailed,
    chart_data_from_monthly,
    chart_data_from_yearly,
)

AVG = ("T", "avg")
MIN = ("T", "min")
PARAMETERS = [AVG, MIN]


def test_chart_data_from_detailed_one_point_per_day() -> None:
    month = MonthSlice(day_values={1: {AVG: -8.11, MIN: -12.3}, 3: {AVG: -19.3}})
    year_slice = YearSlice(year=2020, months={1: month})
    chart = chart_data_from_detailed([year_slice], PARAMETERS)
    assert chart.x_labels == ["01.01.2020", "03.01.2020"]
    assert chart.series[AVG] == [-8.11, -19.3]
    assert chart.series[MIN] == [-12.3, None]


def test_chart_data_from_monthly_one_point_per_month() -> None:
    year_slice = YearSlice(
        year=2020,
        months={1: MonthSlice(month_values={AVG: -6.13}), 3: MonthSlice(month_values={AVG: 0.86})},
    )
    chart = chart_data_from_monthly([year_slice], PARAMETERS)
    assert chart.x_labels == ["01.2020", "03.2020"]
    assert chart.series[AVG] == [-6.13, 0.86]


def test_chart_data_from_yearly_one_point_per_year() -> None:
    year_values = {2020: {AVG: 5.9}, 2021: {AVG: 7.3}}
    chart = chart_data_from_yearly(year_values, [2020, 2021], PARAMETERS)
    assert chart.x_labels == ["2020", "2021"]
    assert chart.series[AVG] == [5.9, 7.3]


def test_chart_data_from_yearly_missing_year_gives_none() -> None:
    chart = chart_data_from_yearly({2020: {AVG: 5.9}}, [2020, 2022], PARAMETERS)
    assert chart.series[AVG] == [5.9, None]
