"""Построение "грида" (абстрактной таблицы ячеек) из year/month/decade/day
срезов computed_indicators — общая раскладка, потребляемая и Qt-рендерером
экрана (ui/aggregate_table_view.py), и экспортом в Excel
(export/aggregate_excel.py), чтобы не дублировать логику компоновки в двух
местах. Ни Qt, ни openpyxl здесь не используется — Grid чисто описывает
"что в какой ячейке и как её показать": значение, выравнивание, жирность,
семантический цвет текста/заливки и границы.

Оформление (правки 2026-09-25, task.md "корректировки" п.2-6): сетка есть
только у ячеек, входящих в таблицу (Cell.in_table), остальное поле — без
линий, как в документе, а не в Excel. Каждая таблица (месяц в "Подробно",
год в "Помесячно", вся таблица "По годам") обведена жирным контуром, шапка
отделена от строк таким же контуром, выделенные столбцы (декады, "за год")
тоже обведены. Таблицы отделены друг от друга пустыми строками. Жирный
контур задаётся сторонами у конкретных ячеек (Cell.thick) — рендереры просто
рисуют то, что указано, не вычисляя периметры сами.

Раскладки повторяют .claude/docs/пример_вывода_агрегатных_данных.xlsx:
- несколько показателей — листы "Подробно"/"Помесячно"/"По годам": на каждый
  месяц (или год) своя таблица, строки — показатели;
- один показатель (п.6) — листы "1 показатель подробно"/"1 показатель
  помесячно": матрица, где строки — месяцы (или годы), а значения
  единственного показателя стоят прямо в ячейках пересечения.
Выбор раскладки делают сами build_detailed_grid/build_monthly_grid по числу
показателей, чтобы экран и экспорт не могли разойтись.

Название станции НЕ часть Grid — это забота каждого рендерера (на экране —
отдельный QLabel, в экспорте — замёрженная строка openpyxl).
"""

from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass, replace
from typing import Literal

from meteoregik.reports.aggregate_query import MonthSlice, Stats, YearSlice
from meteoregik.reports.parameters import ParamKey, label_for

Align = Literal["left", "center", "right"]
Side = Literal["top", "bottom", "left", "right"]
Fill = Literal["decade", "total"]
TextColor = Literal["accent"]

_MONTH_NAMES_RU = [
    "январь", "февраль", "март", "апрель", "май", "июнь",
    "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь",
]

# Ширина строки "Подробно": колонка-подпись + 31 день + 3 декадные ячейки + "месяц".
DETAILED_COL_COUNT = 1 + 31 + 3 + 1
# "Помесячно": колонка-подпись + 12 месяцев + "за год".
MONTHLY_COL_COUNT = 1 + 12 + 1

# Декады фиксированы по номеру дня (1-10 / 11-20 / 21-31) НЕЗАВИСИМО от
# длины месяца — иначе позиция "3 декада"/"месяц" и самих дневных колонок
# "плавала" бы между месяцами разной длины (28/29/30/31 дней), а в примере
# они всегда стоят в одних и тех же колонках (AI/AJ), с пустыми ячейками для
# несуществующих чисел месяца (29-31 в феврале), а не сдвигом остальных строк.
_DECADE_DAY_RANGES: dict[int, tuple[int, int]] = {1: (1, 10), 2: (11, 20), 3: (21, 31)}
# Индексы колонок "N декада" в строке "Подробно" (после дней своей декады).
DECADE_COLUMNS: tuple[int, ...] = (11, 22, 34)
DETAILED_TOTAL_COLUMN = DETAILED_COL_COUNT - 1
MONTHLY_TOTAL_COLUMN = MONTHLY_COL_COUNT - 1


@dataclass(frozen=True)
class Cell:
    value: float | str | None = None
    align: Align = "right"
    bold: bool = False
    color: TextColor | None = None
    fill: Fill | None = None
    # Ячейка часть таблицы — рисуется тонкая сетка; вне таблиц линий нет.
    in_table: bool = False
    # Стороны, по которым идёт жирный ("более чёрный") контур.
    thick: frozenset[Side] = frozenset()


@dataclass(frozen=True)
class Row:
    cells: tuple[Cell, ...]


@dataclass(frozen=True)
class Grid:
    rows: tuple[Row, ...]
    col_count: int


def _pad(cells: list[Cell], col_count: int) -> tuple[Cell, ...]:
    if len(cells) >= col_count:
        return tuple(cells[:col_count])
    return tuple(cells) + tuple(Cell() for _ in range(col_count - len(cells)))


def _blank_row(col_count: int) -> Row:
    return Row(_pad([], col_count))


def _value_cell(stats: Stats, key: ParamKey) -> Cell:
    return Cell(value=stats.get(key))


def _table_rows(
    header: list[Cell],
    body: list[list[Cell]],
    col_count: int,
    *,
    fills: dict[int, Fill] | None = None,
    outlined_columns: tuple[int, ...] = (),
) -> list[Row]:
    """Оформляет прямоугольный блок "шапка + строки" как отдельную таблицу:
    тонкая сетка у всех ячеек блока, жирный контур по периметру и под
    шапкой, жирные вертикальные линии по краям outlined_columns, заливка
    столбцов fills (и в шапке, и в строках — столбец выделяется целиком)."""
    fills = fills or {}
    width = len(header)
    table = [header, *body]
    last_row = len(table) - 1
    rows: list[Row] = []
    for row_index, cells in enumerate(table):
        styled: list[Cell] = []
        for col_index, cell in enumerate(cells[:width]):
            sides: set[Side] = set()
            if row_index == 0:
                sides.update(("top", "bottom"))  # контур шапки
            if row_index == last_row:
                sides.add("bottom")
            if col_index == 0 or col_index in outlined_columns:
                sides.add("left")
            if col_index == width - 1 or col_index in outlined_columns:
                sides.add("right")
            styled.append(
                replace(
                    cell,
                    in_table=True,
                    thick=frozenset(sides),
                    fill=fills.get(col_index, cell.fill),
                )
            )
        rows.append(Row(_pad(styled, col_count)))
    return rows


def _pairs_table(pairs: list[tuple[str, Cell]], col_count: int) -> list[Row]:
    """Маленькая таблица "подпись | значение" (годовые значения над месячными
    таблицами, "показатель | t средняя") — с тонкой сеткой, без шапки и
    жирного контура: это справка к основной таблице, а не сама таблица."""
    return [
        Row(_pad([Cell(label, align="left", in_table=True), replace(value, in_table=True)], col_count))
        for label, value in pairs
    ]


def _title_row(text: str, col_count: int) -> Row:
    return Row(_pad([Cell(text, align="left", bold=True)], col_count))


def _strip_trailing_blank(rows: list[Row]) -> tuple[Row, ...]:
    while rows and all(cell.value is None and not cell.in_table for cell in rows[-1].cells):
        rows.pop()
    return tuple(rows)


def _day_header_cells(year: int, month: int, total_label: str) -> list[Cell]:
    """Дни 1-й декады, "1 декада", дни 2-й, "2 декада", дни 3-й, "3 декада",
    итог — без первой (подписи) колонки. Номера дней — обычным шрифтом
    тёмно-красного цвета (п.3), несуществующие числа месяца — пустые."""
    last_day = monthrange(year, month)[1]
    cells: list[Cell] = []
    for decade in (1, 2, 3):
        start, end = _DECADE_DAY_RANGES[decade]
        for day in range(start, end + 1):
            cells.append(Cell(day, align="center", color="accent") if day <= last_day else Cell())
        cells.append(Cell(f"{decade} декада", align="center", bold=True))
    cells.append(Cell(total_label, align="center", bold=True))
    return cells


def _day_value_cells(year: int, month: int, month_slice: MonthSlice, key: ParamKey) -> list[Cell]:
    last_day = monthrange(year, month)[1]
    cells: list[Cell] = []
    for decade in (1, 2, 3):
        start, end = _DECADE_DAY_RANGES[decade]
        for day in range(start, end + 1):
            cells.append(_value_cell(month_slice.day_values.get(day, {}), key) if day <= last_day else Cell())
        cells.append(_value_cell(month_slice.decade_values.get(decade, {}), key))
    cells.append(_value_cell(month_slice.month_values, key))
    return cells


def _month_label(month: int, *, bold: bool) -> Cell:
    return Cell(_MONTH_NAMES_RU[month - 1], align="left", bold=bold, color="accent")


_DETAILED_FILLS: dict[int, Fill] = {**{col: "decade" for col in DECADE_COLUMNS}, DETAILED_TOTAL_COLUMN: "total"}


def build_detailed_grid(years: list[YearSlice], parameters: list[ParamKey]) -> Grid:
    """Режим "Подробно": годовые блоки по возрастанию года. При одном
    показателе — матрица "месяцы × дни" на год (_build_detailed_single)."""
    if len(parameters) == 1:
        return _build_detailed_single(years, parameters[0])

    col_count = DETAILED_COL_COUNT
    rows: list[Row] = []
    for year_index, year_slice in enumerate(years):
        if year_index:
            rows.append(_blank_row(col_count))  # годы разделены сильнее, чем месяцы
        rows.append(_title_row(f"{year_slice.year} год", col_count))
        rows += _pairs_table([(label_for(key), _value_cell(year_slice.year_values, key)) for key in parameters], col_count)
        rows.append(_blank_row(col_count))  # пустая строка под среднегодовыми (п.3)

        for month in sorted(year_slice.months):
            month_slice = year_slice.months[month]
            header = [_month_label(month, bold=True), *_day_header_cells(year_slice.year, month, "месяц")]
            body = [
                [Cell(label_for(key), align="left"), *_day_value_cells(year_slice.year, month, month_slice, key)]
                for key in parameters
            ]
            rows += _table_rows(header, body, col_count, fills=_DETAILED_FILLS, outlined_columns=DECADE_COLUMNS)
            rows.append(_blank_row(col_count))  # пустая строка между таблицами месяцев

    return Grid(rows=_strip_trailing_blank(rows), col_count=col_count)


def _build_detailed_single(years: list[YearSlice], key: ParamKey) -> Grid:
    """Один показатель, "Подробно" (лист "1 показатель подробно" примера):
    год, под ним показатель с годовым значением, ниже одна таблица на год —
    в шапке дни/декады/"за месяц", слева месяцы, значения на пересечении.

    Шапка дней берётся по 31-дневному месяцу: строки разных месяцев делят
    одну шапку, а несуществующие числа (30 февраля) у конкретного месяца
    просто остаются пустыми ячейками. В матрице всегда все 12 месяцев, в
    т.ч. без данных (правка 2026-09-25, п.5): пропущенные строки сдвигали
    бы месяцы и искажали картину при сравнении лет."""
    col_count = DETAILED_COL_COUNT
    rows: list[Row] = []
    for year_index, year_slice in enumerate(years):
        if year_index:
            rows.append(_blank_row(col_count))
        rows.append(_title_row(f"{year_slice.year} год", col_count))
        rows += _pairs_table([(label_for(key), _value_cell(year_slice.year_values, key))], col_count)
        rows.append(_blank_row(col_count))

        header = [Cell(), *_day_header_cells(year_slice.year, 1, "за месяц")]
        body = [
            [
                _month_label(month, bold=True),
                *_day_value_cells(year_slice.year, month, year_slice.months.get(month, MonthSlice()), key),
            ]
            for month in range(1, 13)
        ]
        rows += _table_rows(header, body, col_count, fills=_DETAILED_FILLS, outlined_columns=DECADE_COLUMNS)
        rows.append(_blank_row(col_count))

    return Grid(rows=_strip_trailing_blank(rows), col_count=col_count)


def _month_header_cells() -> list[Cell]:
    # Названия месяцев в шапке — обычным шрифтом, тёмно-красным (п.4).
    return [Cell(name, align="center", color="accent") for name in _MONTH_NAMES_RU]


def _month_value_cells(year_slice: YearSlice, key: ParamKey) -> list[Cell]:
    cells: list[Cell] = []
    for month in range(1, 13):
        month_slice = year_slice.months.get(month)
        cells.append(_value_cell(month_slice.month_values if month_slice is not None else {}, key))
    cells.append(_value_cell(year_slice.year_values, key))
    return cells


_MONTHLY_FILLS: dict[int, Fill] = {MONTHLY_TOTAL_COLUMN: "total"}


def build_monthly_grid(
    years: list[YearSlice], parameters: list[ParamKey], year_range: list[int] | None = None
) -> Grid:
    """Режим "Помесячно": для каждого года — шапка "показатель/янв.../дек/за
    год" и строки показателей с month_values по 12 месяцам + year_values в
    последней колонке. При одном показателе — одна таблица "годы × месяцы"
    (_build_monthly_single); year_range — все годы выбранного периода, чтобы
    годы без данных остались в матрице пустыми строками, а не выпадали."""
    if len(parameters) == 1:
        return _build_monthly_single(years, parameters[0], year_range)

    col_count = MONTHLY_COL_COUNT
    rows: list[Row] = []
    for year_slice in years:
        rows.append(_title_row(f"{year_slice.year} год", col_count))
        header = [
            Cell("показатель", align="left", bold=True),
            *_month_header_cells(),
            Cell("за год", align="center", bold=True),
        ]
        body = [[Cell(label_for(key), align="left"), *_month_value_cells(year_slice, key)] for key in parameters]
        rows += _table_rows(
            header, body, col_count, fills=_MONTHLY_FILLS, outlined_columns=(MONTHLY_TOTAL_COLUMN,)
        )
        rows.append(_blank_row(col_count))  # пустая строка между таблицами лет (п.4)

    return Grid(rows=_strip_trailing_blank(rows), col_count=col_count)


def _build_monthly_single(years: list[YearSlice], key: ParamKey, year_range: list[int] | None) -> Grid:
    """Один показатель, "Помесячно" (лист "1 показатель помесячно" примера):
    сверху название показателя, ниже одна таблица по всем годам периода —
    в шапке месяцы и "за год", слева годы (по центру). Годы периода без
    данных — пустые строки (правка 2026-09-25, п.5, как и месяцы в
    матрице "Подробно")."""
    col_count = MONTHLY_COL_COUNT
    rows: list[Row] = _pairs_table([("показатель", Cell(label_for(key), align="left"))], col_count)
    rows.append(_blank_row(col_count))
    by_year = {year_slice.year: year_slice for year_slice in years}
    all_years = sorted(set(year_range or []) | set(by_year))
    years = [by_year.get(year, YearSlice(year=year)) for year in all_years]
    if years:
        header = [Cell(), *_month_header_cells(), Cell("за год", align="center", bold=True)]
        body = [
            [Cell(year_slice.year, align="center", color="accent"), *_month_value_cells(year_slice, key)]
            for year_slice in years
        ]
        rows += _table_rows(
            header, body, col_count, fills=_MONTHLY_FILLS, outlined_columns=(MONTHLY_TOTAL_COLUMN,)
        )
    return Grid(rows=_strip_trailing_blank(rows), col_count=col_count)


def build_yearly_grid(year_values: dict[int, Stats], years: list[int], parameters: list[ParamKey]) -> Grid:
    """Режим "По годам": одна таблица — шапка "показатель/год1/год2/..." и
    строки показателей с year-агрегатом каждого выбранного года. Номера лет —
    обычным шрифтом, тёмно-красным (п.5)."""
    col_count = 1 + len(years)
    header = [Cell("показатель", align="left", bold=True)]
    header += [Cell(year, align="center", color="accent") for year in years]
    body = [
        [Cell(label_for(key), align="left"), *(_value_cell(year_values.get(year, {}), key) for year in years)]
        for key in parameters
    ]
    return Grid(rows=tuple(_table_rows(header, body, col_count)), col_count=col_count)
