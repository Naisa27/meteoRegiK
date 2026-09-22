"""Построение "грида" (абстрактной таблицы ячеек) из year/month/decade/day
срезов computed_indicators — общая раскладка, потребляемая и Qt-рендерером
экрана (ui/aggregate_table_view.py), и экспортом в Excel
(export/aggregate_excel.py), чтобы не дублировать логику компоновки в двух
местах. Ни Qt, ни openpyxl здесь не используется — Grid чисто описывает
"что в какой ячейке и как её показать" (значение/выравнивание/жирность/
черта-разделитель под строкой).

Раскладка "Подробно" повторяет построчно пример из
.claude/docs/пример_вывода_агрегатных_данных.xlsx: годовой блок (заголовок +
строка на показатель), затем для каждого реально встретившегося месяца —
строка-шапка (дни 1-й декады, "1 декада", дни 2-й декады, "2 декада", дни
3-й декады, "3 декада", "месяц") и строки показателей с day/decade/month
значениями в соответствующих колонках. Название станции НЕ часть Grid — это
забота каждого рендерера (на экране — отдельный QLabel, в экспорте —
замёрженная строка openpyxl).
"""

from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from typing import Literal

from meteoregik.reports.aggregate_query import MonthSlice, Stats, YearSlice
from meteoregik.reports.parameters import ParamKey, label_for

Align = Literal["left", "center", "right"]

_MONTH_NAMES_RU = [
    "январь", "февраль", "март", "апрель", "май", "июнь",
    "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь",
]

# Ширина строки "Подробно": колонка-подпись + 31 день + 3 декадные ячейки + "месяц".
DETAILED_COL_COUNT = 1 + 31 + 3 + 1


@dataclass(frozen=True)
class Cell:
    value: float | str | None = None
    align: Align = "right"
    bold: bool = False


@dataclass(frozen=True)
class Row:
    cells: tuple[Cell, ...]
    border_bottom: bool = False


@dataclass(frozen=True)
class Grid:
    rows: tuple[Row, ...]
    col_count: int


def _empty_cell() -> Cell:
    return Cell()


def _pad(cells: list[Cell], col_count: int) -> tuple[Cell, ...]:
    if len(cells) >= col_count:
        return tuple(cells[:col_count])
    return tuple(cells) + tuple(_empty_cell() for _ in range(col_count - len(cells)))


def _value_cell(stats: Stats, key: ParamKey) -> Cell:
    return Cell(value=stats.get(key))


def build_detailed_grid(years: list[YearSlice], parameters: list[ParamKey]) -> Grid:
    """Режим "Подробно": годовые блоки (год -> показатели -> месяцы ->
    декады/дни), друг за другом по возрастанию года, с чертой-разделителем
    под последней строкой каждого блока."""
    rows: list[Row] = []
    col_count = DETAILED_COL_COUNT

    for year_slice in years:
        rows.append(Row(_pad([Cell(f"{year_slice.year} год", align="left", bold=True)], col_count)))
        for key in parameters:
            rows.append(Row(_pad([Cell(label_for(key), align="left"), _value_cell(year_slice.year_values, key)], col_count)))

        months = sorted(year_slice.months)
        for month_index, month in enumerate(months):
            is_last_month = month_index == len(months) - 1
            month_slice = year_slice.months[month]
            rows.append(_build_month_header_row(year_slice.year, month, col_count))
            param_rows = _build_month_param_rows(year_slice.year, month, month_slice, parameters, col_count)
            if is_last_month and param_rows:
                # черта-разделитель под последней строкой годового блока
                param_rows[-1] = Row(param_rows[-1].cells, border_bottom=True)
            rows.extend(param_rows)

    return Grid(rows=tuple(rows), col_count=col_count)


# Декады фиксированы по номеру дня (1-10 / 11-20 / 21-31) НЕЗАВИСИМО от
# длины месяца — иначе позиция "3 декада"/"месяц" и самих дневных колонок
# "плавала" бы между месяцами разной длины (28/29/30/31 дней), а в примере
# они всегда стоят в одних и тех же колонках (AI/AJ), с пустыми ячейками для
# несуществующих чисел месяца (29-31 в феврале), а не сдвигом остальных строк.
_DECADE_DAY_RANGES: dict[int, tuple[int, int]] = {1: (1, 10), 2: (11, 20), 3: (21, 31)}


def _build_month_header_row(year: int, month: int, col_count: int) -> Row:
    last_day = monthrange(year, month)[1]
    cells: list[Cell] = [Cell(_MONTH_NAMES_RU[month - 1], align="left")]
    for decade in (1, 2, 3):
        start, end = _DECADE_DAY_RANGES[decade]
        for day in range(start, end + 1):
            cells.append(Cell(day) if day <= last_day else _empty_cell())
        cells.append(Cell(f"{decade} декада", align="center", bold=True))
    cells.append(Cell("месяц", align="center", bold=True))
    return Row(_pad(cells, col_count))


def _build_month_param_rows(
    year: int, month: int, month_slice: MonthSlice, parameters: list[ParamKey], col_count: int
) -> list[Row]:
    last_day = monthrange(year, month)[1]
    rows: list[Row] = []
    for key in parameters:
        cells: list[Cell] = [Cell(label_for(key), align="left")]
        for decade in (1, 2, 3):
            start, end = _DECADE_DAY_RANGES[decade]
            for day in range(start, end + 1):
                cells.append(_value_cell(month_slice.day_values.get(day, {}), key) if day <= last_day else _empty_cell())
            cells.append(_value_cell(month_slice.decade_values.get(decade, {}), key))
        cells.append(_value_cell(month_slice.month_values, key))
        rows.append(Row(_pad(cells, col_count)))
    return rows


def build_monthly_grid(years: list[YearSlice], parameters: list[ParamKey]) -> Grid:
    """Режим "Помесячно": для каждого года — шапка "показатель/янв.../дек/за
    год" и строки показателей с month_values по 12 месяцам + year_values в
    последней колонке."""
    col_count = 1 + 12 + 1
    rows: list[Row] = []
    for year_slice in years:
        rows.append(Row(_pad([Cell(f"{year_slice.year} год", align="left", bold=True)], col_count)))
        header = [Cell("показатель", align="left", bold=True)]
        header += [Cell(name, align="center", bold=True) for name in _MONTH_NAMES_RU]
        header.append(Cell("за год", align="center", bold=True))
        rows.append(Row(_pad(header, col_count)))

        param_rows: list[Row] = []
        for key in parameters:
            cells: list[Cell] = [Cell(label_for(key), align="left")]
            for month in range(1, 13):
                month_slice = year_slice.months.get(month)
                stats = month_slice.month_values if month_slice is not None else {}
                cells.append(_value_cell(stats, key))
            cells.append(_value_cell(year_slice.year_values, key))
            param_rows.append(Row(_pad(cells, col_count)))
        if param_rows:
            # черта-разделитель под последней строкой каждого годового блока
            param_rows[-1] = Row(param_rows[-1].cells, border_bottom=True)
        rows.extend(param_rows)

    return Grid(rows=tuple(rows), col_count=col_count)


def build_yearly_grid(year_values: dict[int, Stats], years: list[int], parameters: list[ParamKey]) -> Grid:
    """Режим "По годам": одна таблица — шапка "показатель/год1/год2/..." и
    строки показателей с year-агрегатом каждого выбранного года."""
    col_count = 1 + len(years)
    rows: list[Row] = []
    header = [Cell("показатель", align="left", bold=True)]
    header += [Cell(year, align="center", bold=True) for year in years]
    rows.append(Row(_pad(header, col_count)))

    for key in parameters:
        cells: list[Cell] = [Cell(label_for(key), align="left")]
        for year in years:
            cells.append(_value_cell(year_values.get(year, {}), key))
        rows.append(Row(_pad(cells, col_count)))

    return Grid(rows=tuple(rows), col_count=col_count)
