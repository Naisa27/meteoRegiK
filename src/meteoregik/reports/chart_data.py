"""Данные для режима "графики" — извлекаются напрямую из тех же
year/month/decade/day срезов, что и Grid (reports/grid.py), а не из уже
построенного Grid: в Grid день/декада/месяц/год перемешаны в одной строке
(раскладка "Подробно"), доставать оттуда обратно только нужную гранулярность
было бы более хрупким, чем взять её напрямую из YearSlice.

Гранулярность точек графика = гранулярность выбранного режима вывода
(день/месяц/год) — по решению пользователя все выбранные показатели рисуются
на одном графике с общей легендой (см. ui/aggregate_chart_view.py)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from meteoregik.reports.aggregate_query import Stats, YearSlice
from meteoregik.reports.parameters import ParamKey


@dataclass(frozen=True)
class ChartData:
    x_labels: list[str] = field(default_factory=list)
    series: dict[ParamKey, list[float | None]] = field(default_factory=dict)


def _empty_series(parameters: list[ParamKey]) -> dict[ParamKey, list[float | None]]:
    return {key: [] for key in parameters}


def chart_data_from_detailed(years: list[YearSlice], parameters: list[ParamKey]) -> ChartData:
    """Гранулярность — день: одна точка на каждый реально встретившийся
    день (в порядке дат по возрастанию)."""
    x_labels: list[str] = []
    series = _empty_series(parameters)
    for year_slice in years:
        for month in sorted(year_slice.months):
            month_slice = year_slice.months[month]
            for day in sorted(month_slice.day_values):
                x_labels.append(date(year_slice.year, month, day).strftime("%d.%m.%Y"))
                stats = month_slice.day_values[day]
                for key in parameters:
                    series[key].append(stats.get(key))
    return ChartData(x_labels=x_labels, series=series)


def chart_data_from_monthly(years: list[YearSlice], parameters: list[ParamKey]) -> ChartData:
    """Гранулярность — месяц: одна точка на каждый реально встретившийся
    месяц."""
    x_labels: list[str] = []
    series = _empty_series(parameters)
    for year_slice in years:
        for month in sorted(year_slice.months):
            x_labels.append(f"{month:02d}.{year_slice.year}")
            stats = year_slice.months[month].month_values
            for key in parameters:
                series[key].append(stats.get(key))
    return ChartData(x_labels=x_labels, series=series)


def chart_data_from_yearly(year_values: dict[int, Stats], years: list[int], parameters: list[ParamKey]) -> ChartData:
    """Гранулярность — год: одна точка на каждый выбранный год."""
    x_labels = [str(year) for year in years]
    series = _empty_series(parameters)
    for year in years:
        stats = year_values.get(year, {})
        for key in parameters:
            series[key].append(stats.get(key))
    return ChartData(x_labels=x_labels, series=series)
