from __future__ import annotations

import pytest

from meteoregik.reports.aggregate_query import MonthSlice, YearSlice
from meteoregik.reports.grid import (
    DECADE_COLUMNS,
    DETAILED_COL_COUNT,
    DETAILED_TOTAL_COLUMN,
    MONTHLY_TOTAL_COLUMN,
    Grid,
    Row,
    build_detailed_grid,
    build_monthly_grid,
    build_yearly_grid,
)

AVG = ("T", "avg")
PARAMETERS = [AVG, ("T", "min"), ("T", "max")]


def _year_slice_2020_january() -> YearSlice:
    day_values = {1: {AVG: -8.11}, 2: {AVG: -15.59}}
    decade_values = {1: {AVG: -13.03}}
    month = MonthSlice(month_values={AVG: -6.13}, decade_values=decade_values, day_values=day_values)
    return YearSlice(year=2020, year_values={AVG: 6.83}, months={1: month})


def _year_slice_2020_jan_feb() -> YearSlice:
    year = _year_slice_2020_january()
    february = MonthSlice(month_values={AVG: -4.61})
    return YearSlice(year=2020, year_values=year.year_values, months={1: year.months[1], 2: february})


def _is_blank(row: Row) -> bool:
    return all(cell.value is None and not cell.in_table for cell in row.cells)


def _row_index(grid: Grid, first_value: object) -> int:
    return next(i for i, row in enumerate(grid.rows) if row.cells[0].value == first_value)


def _table_ranges(grid: Grid) -> list[tuple[int, int]]:
    """Непрерывные диапазоны строк, где есть жирный контур (т.е. таблицы)."""
    ranges: list[tuple[int, int]] = []
    start = None
    for i, row in enumerate(grid.rows):
        in_block = any(cell.thick for cell in row.cells)
        if in_block and start is None:
            start = i
        if not in_block and start is not None:
            ranges.append((start, i - 1))
            start = None
    if start is not None:
        ranges.append((start, len(grid.rows) - 1))
    return ranges


# --- "Подробно", несколько показателей ---


def test_detailed_grid_col_count_matches_example_35_data_columns_plus_label() -> None:
    # Пример: A9=месяц, B..K=дни 1-10 (10), L=1 декада, M..V=дни 11-20 (10),
    # W=2 декада, X..AH=дни 21-31 (11), AI=3 декада, AJ=месяц -> всего 36.
    assert DETAILED_COL_COUNT == 36


def test_detailed_grid_year_title_values_and_blank_row_under_them() -> None:
    grid = build_detailed_grid([_year_slice_2020_january()], PARAMETERS)
    assert grid.rows[0].cells[0].value == "2020 год"
    assert grid.rows[0].cells[0].bold is True
    assert not grid.rows[0].cells[0].in_table  # заголовок года — без сетки
    avg_year_row = grid.rows[1]
    assert avg_year_row.cells[0].value == "t средняя"
    assert avg_year_row.cells[1].value == 6.83
    assert avg_year_row.cells[0].in_table and avg_year_row.cells[1].in_table
    assert not avg_year_row.cells[2].in_table  # сетка только там, где что-то есть
    # п.3: пустая строка под среднегодовыми данными
    assert _is_blank(grid.rows[4])


def test_detailed_grid_month_header_row_matches_example_layout() -> None:
    grid = build_detailed_grid([_year_slice_2020_january()], PARAMETERS)
    header = grid.rows[5]  # 0=год, 1..3=показатели года, 4=пустая, 5=шапка января
    assert header.cells[0].value == "январь"
    assert [c.value for c in header.cells[1:11]] == list(range(1, 11))
    assert header.cells[11].value == "1 декада"
    assert [c.value for c in header.cells[12:22]] == list(range(11, 21))
    assert header.cells[22].value == "2 декада"
    assert [c.value for c in header.cells[23:34]] == list(range(21, 32))
    assert header.cells[34].value == "3 декада"
    assert header.cells[35].value == "месяц"


def test_detailed_grid_month_header_fonts_and_colors() -> None:
    header = build_detailed_grid([_year_slice_2020_january()], PARAMETERS).rows[5]
    month_name = header.cells[0]
    assert month_name.bold and month_name.color == "accent"  # жирный тёмно-красный
    day = header.cells[1]
    assert not day.bold and day.color == "accent"  # обычный, тот же цвет


def test_detailed_grid_decade_and_month_columns_filled_and_outlined() -> None:
    grid = build_detailed_grid([_year_slice_2020_january()], PARAMETERS)
    for row in grid.rows[5:9]:  # шапка + 3 строки показателей
        for col in DECADE_COLUMNS:
            assert row.cells[col].fill == "decade"
            assert {"left", "right"} <= row.cells[col].thick
        assert row.cells[DETAILED_TOTAL_COLUMN].fill == "total"
        assert row.cells[1].fill is None


def test_detailed_grid_month_table_outline_and_header_border() -> None:
    grid = build_detailed_grid([_year_slice_2020_january()], PARAMETERS)
    header, first, last = grid.rows[5], grid.rows[6], grid.rows[8]
    assert all({"top", "bottom"} <= cell.thick for cell in header.cells)  # контур шапки
    assert "top" not in first.cells[5].thick and "bottom" not in first.cells[5].thick
    assert all("bottom" in cell.thick for cell in last.cells)  # нижняя граница таблицы
    for row in grid.rows[5:9]:
        assert "left" in row.cells[0].thick
        assert "right" in row.cells[DETAILED_TOTAL_COLUMN].thick
        assert all(cell.in_table for cell in row.cells)


def test_detailed_grid_month_param_row_places_values_at_fixed_positions() -> None:
    grid = build_detailed_grid([_year_slice_2020_january()], PARAMETERS)
    avg_row = grid.rows[6]  # первая строка показателей января ("t средняя")
    assert avg_row.cells[0].value == "t средняя"
    assert avg_row.cells[1].value == -8.11  # день 1
    assert avg_row.cells[2].value == -15.59  # день 2
    assert avg_row.cells[3].value is None  # день 3 без данных — пустая ячейка
    assert avg_row.cells[11].value == -13.03  # "1 декада"
    assert avg_row.cells[35].value == -6.13  # "месяц"


def test_detailed_grid_short_february_keeps_fixed_columns_for_decade3_and_month() -> None:
    """Критично: у месяца короче 31 дня "3 декада"/"месяц" не должны
    сдвигаться влево — они всегда в колонках 34/35, как и у января."""
    month = MonthSlice(month_values={AVG: 1.0}, decade_values={3: {AVG: 2.0}}, day_values={27: {AVG: 3.0}})
    year_slice = YearSlice(year=2021, year_values={}, months={2: month})
    grid = build_detailed_grid([year_slice], PARAMETERS)
    header = grid.rows[_row_index(grid, "февраль")]
    assert header.cells[30].value == 28  # последний реальный день месяца
    assert header.cells[31].value is None  # 29 февраля не существует в невисокосном году
    assert header.cells[33].value is None  # 31 февраля не существует
    assert header.cells[34].value == "3 декада"
    assert header.cells[35].value == "месяц"

    param_row = grid.rows[_row_index(grid, "февраль") + 1]
    assert param_row.cells[34].value == 2.0
    assert param_row.cells[35].value == 1.0


def test_detailed_grid_month_tables_separated_by_blank_row() -> None:
    grid = build_detailed_grid([_year_slice_2020_jan_feb()], PARAMETERS)
    ranges = _table_ranges(grid)
    assert len(ranges) == 2  # январь, февраль
    (_, jan_end), (feb_start, _) = ranges
    assert feb_start - jan_end >= 2
    assert all(_is_blank(grid.rows[i]) for i in range(jan_end + 1, feb_start))


def test_detailed_grid_has_no_trailing_blank_rows() -> None:
    grid = build_detailed_grid([_year_slice_2020_january()], PARAMETERS)
    assert not _is_blank(grid.rows[-1])


def test_detailed_grid_preserves_input_order_of_years() -> None:
    # Сортировка по возрастанию года — ответственность group_detailed
    # (reports/aggregate_query.py), не билдера грида.
    year_2021 = YearSlice(year=2021, year_values={AVG: 1.0}, months={})
    grid = build_detailed_grid([_year_slice_2020_january(), year_2021], PARAMETERS)
    assert grid.rows[0].cells[0].value == "2020 год"
    assert _row_index(grid, "2021 год") > _row_index(grid, "январь")


# --- "Подробно", один показатель (лист "1 показатель подробно") ---


def test_detailed_single_parameter_is_month_by_day_matrix() -> None:
    grid = build_detailed_grid([_year_slice_2020_jan_feb()], [AVG])
    assert grid.rows[0].cells[0].value == "2020 год"
    assert [grid.rows[1].cells[0].value, grid.rows[1].cells[1].value] == ["t средняя", 6.83]
    assert _is_blank(grid.rows[2])

    header = grid.rows[3]
    assert header.cells[0].value is None
    assert [c.value for c in header.cells[1:11]] == list(range(1, 11))
    assert header.cells[34].value == "3 декада"
    assert header.cells[35].value == "за месяц"

    january, february, march = grid.rows[4], grid.rows[5], grid.rows[6]
    assert january.cells[0].value == "январь"
    assert january.cells[0].bold and january.cells[0].color == "accent"
    assert january.cells[1].value == -8.11
    assert january.cells[11].value == -13.03
    assert january.cells[35].value == -6.13
    assert february.cells[0].value == "февраль"
    assert february.cells[35].value == -4.61
    assert february.cells[33].value is None  # 31 февраля нет — пустая ячейка
    # п.5 (2026-09-25): все 12 месяцев, даже без данных — пустые строки
    assert march.cells[0].value == "март"
    assert all(cell.value is None for cell in march.cells[1:])
    assert [row.cells[0].value for row in grid.rows[4:16]][-1] == "декабрь"
    assert _table_ranges(grid) == [(3, 15)]  # одна таблица на год


def test_detailed_single_parameter_styles_match_multi_parameter() -> None:
    grid = build_detailed_grid([_year_slice_2020_january()], [AVG])
    for row in grid.rows[3:5]:
        for col in DECADE_COLUMNS:
            assert row.cells[col].fill == "decade"
        assert row.cells[DETAILED_TOTAL_COLUMN].fill == "total"
    assert grid.rows[3].cells[1].color == "accent"  # номера дней


# --- "Помесячно" ---


def test_monthly_grid_header_and_values() -> None:
    grid = build_monthly_grid([_year_slice_2020_january()], PARAMETERS)
    assert grid.rows[0].cells[0].value == "2020 год"
    header = grid.rows[1]
    assert header.cells[0].value == "показатель"
    assert header.cells[1].value == "январь"
    assert header.cells[12].value == "декабрь"
    assert header.cells[13].value == "за год"

    avg_row = grid.rows[2]
    assert avg_row.cells[0].value == "t средняя"
    assert avg_row.cells[1].value == -6.13  # январь
    assert avg_row.cells[2].value is None  # февраль без данных
    assert avg_row.cells[13].value == 6.83  # за год


def test_monthly_grid_styles() -> None:
    grid = build_monthly_grid([_year_slice_2020_january()], PARAMETERS)
    header = grid.rows[1]
    assert header.cells[1].color == "accent" and not header.cells[1].bold  # месяц в шапке
    for row in grid.rows[1:5]:
        total = row.cells[MONTHLY_TOTAL_COLUMN]
        assert total.fill == "total"
        assert {"left", "right"} <= total.thick
    assert all({"top", "bottom"} <= cell.thick for cell in header.cells)


def test_monthly_grid_year_tables_separated_by_blank_row() -> None:
    year_2021 = YearSlice(year=2021, year_values={AVG: 1.0}, months={})
    grid = build_monthly_grid([_year_slice_2020_january(), year_2021], PARAMETERS)
    title_2021 = _row_index(grid, "2021 год")
    assert _is_blank(grid.rows[title_2021 - 1])


def test_monthly_single_parameter_is_year_by_month_matrix() -> None:
    year_2021 = YearSlice(year=2021, year_values={AVG: 7.0}, months={1: MonthSlice(month_values={AVG: -3.0})})
    grid = build_monthly_grid([_year_slice_2020_january(), year_2021], [AVG])
    assert [grid.rows[0].cells[0].value, grid.rows[0].cells[1].value] == ["показатель", "t средняя"]
    assert _is_blank(grid.rows[1])

    header = grid.rows[2]
    assert header.cells[0].value is None
    assert header.cells[1].value == "январь"
    assert header.cells[13].value == "за год"

    row_2020, row_2021 = grid.rows[3], grid.rows[4]
    assert row_2020.cells[0].value == 2020
    assert row_2020.cells[0].align == "center"
    assert row_2020.cells[0].color == "accent"
    assert row_2020.cells[1].value == -6.13
    assert row_2020.cells[13].value == 6.83
    assert row_2021.cells[1].value == -3.0
    assert row_2021.cells[MONTHLY_TOTAL_COLUMN].fill == "total"
    assert _table_ranges(grid) == [(2, 4)]  # одна таблица на все годы


# --- "По годам" ---


def test_yearly_grid_header_and_values() -> None:
    year_values = {2020: {AVG: 5.9}, 2021: {AVG: 7.3}}
    grid = build_yearly_grid(year_values, [2020, 2021], PARAMETERS)
    header = grid.rows[0]
    assert [c.value for c in header.cells] == ["показатель", 2020, 2021]

    avg_row = grid.rows[1]
    assert avg_row.cells[0].value == "t средняя"
    assert avg_row.cells[1].value == 5.9
    assert avg_row.cells[2].value == 7.3


def test_yearly_grid_styles() -> None:
    grid = build_yearly_grid({2020: {AVG: 5.9}}, [2020, 2021], PARAMETERS)
    header = grid.rows[0]
    assert header.cells[1].color == "accent" and not header.cells[1].bold  # номера лет
    assert all({"top", "bottom"} <= cell.thick for cell in header.cells)
    assert all("bottom" in cell.thick for cell in grid.rows[-1].cells)
    assert all("left" in row.cells[0].thick and "right" in row.cells[-1].thick for row in grid.rows)


@pytest.mark.parametrize("builder", [build_detailed_grid, build_monthly_grid])
def test_empty_input_gives_no_table(builder) -> None:
    grid = builder([], PARAMETERS)
    assert _table_ranges(grid) == []


def test_monthly_single_parameter_keeps_years_without_data_from_period() -> None:
    # п.5 (2026-09-25): годы периода без данных — пустые строки матрицы
    grid = build_monthly_grid([_year_slice_2020_january()], [AVG], year_range=[2019, 2020, 2021])
    years = [grid.rows[i].cells[0].value for i in range(3, 6)]
    assert years == [2019, 2020, 2021]
    assert all(cell.value is None for cell in grid.rows[3].cells[1:])
    assert grid.rows[4].cells[1].value == -6.13
    assert _table_ranges(grid) == [(2, 5)]
