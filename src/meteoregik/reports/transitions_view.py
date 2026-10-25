"""Таблицы окна "Переходы температуры" — без Qt, общие для экрана
(ui/transitions_dialog.py) и выгрузки в Excel (export/transitions_excel.py),
как reports/grid.py у "Просмотра агрегатных данных".

Раскладка — по примеру пользователя .claude/docs/пример_вывода_переходов.xlsx:
- "Таблица переходов": год, весна 0/5/10/15 °C, осень 15/10/5/0 °C (пороги
  идут как годовой ход: вверх весной, вниз осенью);
- "Таблица периодов": год и длительность периодов выше 0, 5, 10, 15 °C.
Последняя строка обеих — средняя многолетняя (правило — transitions.mean_date).

Ячейка без даты не пустая: "нет данных" (на экране — со штриховкой) или
"не было", и у каждой подсказка с причиной — состояние не передаётся одним
цветом (ui-ux-pro-max: color-only).
"""

from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass, field
from datetime import date

from meteoregik.calculations import transitions as tr
from meteoregik.calculations.anomalies import month_is_complete

KIND_VALUE = "value"
KIND_NONE = "none"
KIND_NO_DATA = "no_data"

GROUP_SPRING = "Весна"
GROUP_AUTUMN = "Осень"
GROUP_DURATION = "Длительность, дни"
YEAR_TITLE = "Год"
MEAN_LABEL = "Средняя"
NO_DATA_TEXT = "нет данных"
NONE_TEXT = "не было"
NO_VALUE = "—"

SEASON_NAMES = {tr.SPRING: "весной", tr.AUTUMN: "осенью"}
# Названия периодов выше порога — как в примере пользователя
PERIOD_NAMES = {
    0: "Тёплый период",
    5: "Вегетационный период",
    10: "Период активной вегетации",
    15: "Летний период",
}
_MONTHS = [
    "январь", "февраль", "март", "апрель", "май", "июнь",
    "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь",
]


@dataclass(frozen=True)
class Column:
    title: str
    group: str = ""
    season: str | None = None  # у столбцов дат — сезон (цвет шапки)
    tip: str = ""


@dataclass(frozen=True)
class TableCell:
    text: str
    kind: str = KIND_VALUE
    tip: str = ""
    day: date | None = None  # дата перехода — для Excel (там она пишется датой)
    number: float | None = None  # длительность — для Excel


@dataclass(frozen=True)
class TableRow:
    label: str
    cells: list[TableCell]
    mean: bool = False


@dataclass(frozen=True)
class Table:
    columns: list[Column]  # без столбца "Год"
    rows: list[TableRow] = field(default_factory=list)


def threshold_title(threshold: int) -> str:
    return f"{threshold} °C"


def transition_columns() -> list[tuple[str, int]]:
    """Порядок столбцов дат: весна по возрастанию порога, осень — по убыванию."""
    return [(tr.SPRING, t) for t in tr.THRESHOLDS] + [(tr.AUTUMN, t) for t in reversed(tr.THRESHOLDS)]


def _window_text(year: int, season: str) -> str:
    first, last = tr.season_window(year, season)
    return f"{first:%d.%m.%Y}–{last:%d.%m.%Y}"


def incomplete_months(year: int, season: str, days: dict[date, float]) -> list[str]:
    """Неполные месяцы окна словами: "июль 2015 (11 дн. из 31)"."""
    result = []
    for y, m in tr.window_months(year, season):
        length = monthrange(y, m)[1]
        present = {d for d in range(1, length + 1) if date(y, m, d) in days}
        if not month_is_complete(present, length):
            result.append(f"{_MONTHS[m - 1]} {y} ({len(present)} дн. из {length})")
    return result


def _no_data_tip(year: int, season: str, days: dict[date, float]) -> str:
    months = incomplete_months(year, season, days)
    text = (
        f"Недостаточно данных: дата считается, только если все месяцы окна поиска "
        f"({_window_text(year, season)}) полные — пропущено не больше 5 дней и не больше 3 подряд."
    )
    if months:
        text += " Неполные: " + ", ".join(months) + "."
    return text


def _none_tip(threshold: int, season: str) -> str:
    return (
        f"Перехода через {threshold} °C {SEASON_NAMES[season]} не было: температура весь сезон "
        "оставалась по одну сторону порога или так и не перешла его устойчиво."
    )


def _date_text(day: date, year: int) -> str:
    # Осенний переход бывает в январе–феврале следующего года — тогда с годом
    return f"{day:%d.%m}" if day.year == year else f"{day:%d.%m.%Y}"


def _base_tip(mean: tr.MeanValue, what: str) -> str:
    if mean.standard:
        base = f"Средняя {what} за {tr.NORM_BASE_FIRST_YEAR}–{tr.NORM_BASE_LAST_YEAR}"
    else:
        base = f"Средняя {what} по архиву станции, {mean.first_year}–{mean.last_year}"
    text = f"{base}: {mean.years} {_years_word(mean.years)} с данными"
    if mean.none_years:
        text += f"; ещё в {mean.none_years} {_years_word(mean.none_years, genitive=True)} перехода не было"
    return text + "."


def _years_word(count: int, genitive: bool = False) -> str:
    tail = count % 100
    if genitive:
        return "году" if tail % 10 == 1 and tail != 11 else "годах"
    if 11 <= tail <= 14 or tail % 10 in (0, 5, 6, 7, 8, 9):
        return "лет"
    return "год" if tail % 10 == 1 else "года"


def transitions_table(data: tr.StationTransitions, days: dict[date, float]) -> Table:
    columns = [
        Column(threshold_title(t), GROUP_SPRING if s == tr.SPRING else GROUP_AUTUMN, s,
               f"Дата устойчивого перехода средней суточной температуры через {t} °C {SEASON_NAMES[s]}")
        for s, t in transition_columns()
    ]
    rows = []
    for year in data.years:
        cells = []
        for season, threshold in transition_columns():
            transition = data.get(year, season, threshold)
            if transition is None or transition.status == tr.STATUS_NO_DATA:
                cells.append(TableCell(NO_DATA_TEXT, KIND_NO_DATA, _no_data_tip(year, season, days)))
            elif transition.day is None:
                cells.append(TableCell(NONE_TEXT, KIND_NONE, _none_tip(threshold, season)))
            else:
                cells.append(TableCell(_date_text(transition.day, year), day=transition.day))
        rows.append(TableRow(str(year), cells))
    if data.years:
        means = []
        for season, threshold in transition_columns():
            mean = tr.mean_date(data, season, threshold)
            if mean is None:
                means.append(TableCell(NO_VALUE, KIND_NONE, "Ни в одном году нет даты перехода."))
            else:
                means.append(TableCell(f"{mean.day:%d.%m}", tip=_base_tip(mean, "дата")))
        rows.append(TableRow(MEAN_LABEL, means, mean=True))
    return Table(columns, rows)


def periods_table(data: tr.StationTransitions, days: dict[date, float]) -> Table:
    columns = [
        Column(f"{PERIOD_NAMES[t]}\n(выше {t} °C)", GROUP_DURATION,
               tip=f"Дней от весеннего перехода через {t} °C до осеннего")
        for t in tr.THRESHOLDS
    ]
    rows = []
    for year in data.years:
        cells = []
        for threshold in tr.THRESHOLDS:
            duration = data.duration(year, threshold)
            if duration is not None:
                cells.append(TableCell(str(duration), number=duration))
                continue
            pair = [(s, data.get(year, s, threshold)) for s in tr.SEASONS]
            missing = [s for s, t in pair if t is None or t.status == tr.STATUS_NO_DATA]
            if missing:
                tip = " ".join(_no_data_tip(year, s, days) for s in missing)
                cells.append(TableCell(NO_DATA_TEXT, KIND_NO_DATA, tip))
            else:
                names = " и ".join(
                    ("весеннего" if s == tr.SPRING else "осеннего") for s, t in pair if t.day is None
                )
                cells.append(TableCell(
                    NO_VALUE, KIND_NONE, f"Длительность не считается: {names} перехода через {threshold} °C не было."
                ))
        rows.append(TableRow(str(year), cells))
    if data.years:
        means = []
        for threshold in tr.THRESHOLDS:
            mean = tr.mean_duration(data, threshold)
            if mean is None:
                means.append(TableCell(NO_VALUE, KIND_NONE, "Ни в одном году нет обеих дат перехода."))
            else:
                means.append(TableCell(str(round(mean.days)), tip=_base_tip(mean, "длительность"),
                                       number=round(mean.days)))
        rows.append(TableRow(MEAN_LABEL, means, mean=True))
    return Table(columns, rows)
