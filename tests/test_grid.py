from __future__ import annotations

from meteoregik.reports.aggregate_query import MonthSlice, YearSlice
from meteoregik.reports.grid import (
    DETAILED_COL_COUNT,
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


def test_detailed_grid_col_count_matches_example_35_data_columns_plus_label() -> None:
    # Пример: A9=месяц, B..K=дни 1-10 (10), L=1 декада, M..V=дни 11-20 (10),
    # W=2 декада, X..AH=дни 21-31 (11), AI=3 декада, AJ=месяц -> всего 36.
    assert DETAILED_COL_COUNT == 36


def test_detailed_grid_year_header_and_year_values_rows() -> None:
    grid = build_detailed_grid([_year_slice_2020_january()], PARAMETERS)
    assert grid.rows[0].cells[0].value == "2020 год"
    assert grid.rows[0].cells[0].bold is True
    avg_year_row = grid.rows[1]
    assert avg_year_row.cells[0].value == "t средняя"
    assert avg_year_row.cells[1].value == 6.83


def test_detailed_grid_month_header_row_matches_example_layout() -> None:
    grid = build_detailed_grid([_year_slice_2020_january()], PARAMETERS)
    # rows: 0=год, 1..3=показатели года, 4=шапка января
    header = grid.rows[4]
    assert header.cells[0].value == "январь"
    assert [c.value for c in header.cells[1:11]] == list(range(1, 11))
    assert header.cells[11].value == "1 декада"
    assert [c.value for c in header.cells[12:22]] == list(range(11, 21))
    assert header.cells[22].value == "2 декада"
    assert [c.value for c in header.cells[23:34]] == list(range(21, 32))
    assert header.cells[34].value == "3 декада"
    assert header.cells[35].value == "месяц"


def test_detailed_grid_month_param_row_places_values_at_fixed_positions() -> None:
    grid = build_detailed_grid([_year_slice_2020_january()], PARAMETERS)
    avg_row = grid.rows[5]  # первая строка показателей января ("t средняя")
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
    header = grid.rows[4]
    assert header.cells[0].value == "февраль"
    # блок "3 декада" — фиксированные индексы 23..33 (дни 21..31); в феврале
    # 2021 (28 дней) реально существуют только дни 21..28 (индексы 23..30).
    assert header.cells[30].value == 28  # последний реальный день месяца
    assert header.cells[31].value is None  # 29 февраля не существует в невисокосном году
    assert header.cells[32].value is None  # 30 февраля не существует
    assert header.cells[33].value is None  # 31 февраля не существует
    assert header.cells[34].value == "3 декада"
    assert header.cells[35].value == "месяц"

    param_row = grid.rows[5]
    assert param_row.cells[34].value == 2.0  # значение декады на фиксированной позиции
    assert param_row.cells[35].value == 1.0  # значение месяца на фиксированной позиции


def test_detailed_grid_border_bottom_only_on_last_row_of_year_block() -> None:
    year1 = _year_slice_2020_january()
    year2 = YearSlice(year=2021, year_values={AVG: 1.0}, months={})
    grid = build_detailed_grid([year1, year2], PARAMETERS)
    borders = [i for i, row in enumerate(grid.rows) if row.border_bottom]
    # единственная черта — последняя строка показателей января (последний месяц года 2020)
    assert len(borders) == 1
    assert grid.rows[borders[0]].cells[0].value == "t макс"


def test_detailed_grid_preserves_input_order_of_years() -> None:
    # Сортировка по возрастанию года — ответственность group_detailed
    # (reports/aggregate_query.py), не билдера грида; билдер просто кладёт
    # годовые блоки в переданном порядке.
    year_2021 = YearSlice(year=2021, year_values={AVG: 1.0}, months={})
    year_2020 = _year_slice_2020_january()
    grid = build_detailed_grid([year_2020, year_2021], PARAMETERS)
    assert grid.rows[0].cells[0].value == "2020 год"


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


def test_yearly_grid_header_and_values() -> None:
    year_values = {2020: {AVG: 5.9}, 2021: {AVG: 7.3}}
    grid = build_yearly_grid(year_values, [2020, 2021], PARAMETERS)
    header = grid.rows[0]
    assert [c.value for c in header.cells] == ["показатель", 2020, 2021]

    avg_row = grid.rows[1]
    assert avg_row.cells[0].value == "t средняя"
    assert avg_row.cells[1].value == 5.9
    assert avg_row.cells[2].value == 7.3
