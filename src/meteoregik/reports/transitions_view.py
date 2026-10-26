"""Таблицы окна "Переходы температуры" — без Qt, общие для экрана
(ui/transitions_dialog.py) и выгрузки в Excel (export/transitions_excel.py),
как reports/grid.py у "Просмотра агрегатных данных".

Раскладка — по примеру пользователя .claude/docs/пример_вывода_переходов.xlsx:
- "Таблица переходов": год, весна 0/5/10/15 °C, осень 15/10/5/0 °C (пороги
  идут как годовой ход: вверх весной, вниз осенью);
- "Таблица периодов": год и длительность периодов выше 0, 5, 10, 15 °C.
Последняя строка — средняя многолетняя дата / длительность (правило —
transitions.mean_date).

В каждой ячейке — два прочтения метода через слэш, всегда в одном порядке:
Гидрометцентр / "Климат России" (корректировка 2026-10-27). Если у обоих
одно и то же "нет данных" или "не было", слово пишется один раз — дата же
пишется дважды и при совпадении: пользователь просил видеть оба значения.

Ячейка без даты не пустая: "нет данных" (на экране — со штриховкой) или
"не было", и у каждой подсказка с причиной — состояние не передаётся одним
цветом (ui-ux-pro-max: color-only).
"""

from __future__ import annotations

from calendar import monthrange
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date

from meteoregik.calculations import transitions as tr

KIND_VALUE = "value"
KIND_NONE = "none"
KIND_NO_DATA = "no_data"

GROUP_SPRING = "Весна"
GROUP_AUTUMN = "Осень"
GROUP_DURATION = "Длительность, дни"
YEAR_TITLE = "Год"
MEAN_DATE_LABEL = "Средняя многолетняя дата"
MEAN_DURATION_LABEL = "Средняя многолетняя длительность"
NO_DATA_TEXT = "нет данных"
NONE_TEXT = "не было"
NO_VALUE = "—"
SEPARATOR = " / "
# Порядок прочтений в ячейке — словами, для подписи над таблицей и подсказок
METHODS_ORDER = SEPARATOR.join(tr.METHOD_NAMES[m] for m in tr.METHODS)

SEASON_NAMES = {tr.SPRING: "весной", tr.AUTUMN: "осенью"}
# Названия периодов выше порога — как в примере пользователя
PERIOD_NAMES = {
    0: "Тёплый период",
    5: "Вегетационный период",
    10: "Период активной вегетации",
    15: "Летний период",
}
MONTHS = [
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


@dataclass(frozen=True)
class TableRow:
    label: str
    cells: list[TableCell]
    mean: bool = False


@dataclass(frozen=True)
class Table:
    columns: list[Column]  # без столбца "Год"
    rows: list[TableRow] = field(default_factory=list)


@dataclass(frozen=True)
class _Part:
    """Значение одного прочтения в ячейке."""

    text: str
    kind: str
    tip: str = ""


def threshold_title(threshold: int) -> str:
    return f"{threshold} °C"


def transition_columns() -> list[tuple[str, int]]:
    """Порядок столбцов дат: весна по возрастанию порога, осень — по убыванию."""
    return [(tr.SPRING, t) for t in tr.THRESHOLDS] + [(tr.AUTUMN, t) for t in reversed(tr.THRESHOLDS)]


def month_text(year: int, month: int, present: int, length: int, season_year: int | None = None) -> str:
    """"июль 2015 (11 дн. из 31)"; с season_year год месяца пишется, только
    если он другой (январь–февраль осени)."""
    name = MONTHS[month - 1] if year == season_year else f"{MONTHS[month - 1]} {year}"
    return f"{name} ({present} дн. из {length})"


def incomplete_months(year: int, season: str, days: dict[date, float]) -> list[str]:
    """Неполные месяцы окна словами: "июль 2015 (11 дн. из 31)"."""
    return [
        month_text(y, m, len(tr.month_days(y, m, days)), monthrange(y, m)[1])
        for y, m in tr.incomplete_window_months(year, season, days)
    ]


def _no_data_tip(year: int, season: str, days: dict[date, float]) -> str:
    first, last = tr.season_window(year, season)
    text = (
        f"Недостаточно данных. Дата считается, только если полные все месяцы окна поиска от его "
        f"начала ({first:%d.%m.%Y}) до месяца после даты перехода; если переход не найден — все месяцы "
        f"окна, до {last:%d.%m.%Y}. Полный месяц — пропущено не больше 5 дней и не больше 3 подряд."
    )
    months = incomplete_months(year, season, days)
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


def _combine(parts: list[_Part], extra_tip: str = "") -> TableCell:
    """Ячейка из значений прочтений (в порядке tr.METHODS). Подсказка
    называет значение каждого прочтения и объясняет "нет данных"/"не было"
    (одинаковые объяснения — один раз)."""
    same_status = len({(p.kind, p.text) for p in parts}) == 1 and parts[0].kind != KIND_VALUE
    text = parts[0].text if same_status else SEPARATOR.join(p.text for p in parts)
    kinds = {p.kind for p in parts}
    if KIND_NO_DATA in kinds:
        kind = KIND_NO_DATA
    elif kinds == {KIND_NONE}:
        kind = KIND_NONE
    else:
        kind = KIND_VALUE
    if same_status:
        lines = [f"{METHODS_ORDER}: {parts[0].text}."]
    else:
        lines = ["; ".join(f"{tr.METHOD_NAMES[m]}: {p.text}" for m, p in zip(tr.METHODS, parts)) + "."]
    explanations: list[str] = []
    for part in parts:
        if part.tip and part.tip not in explanations:
            explanations.append(part.tip)
    if extra_tip:
        explanations.append(extra_tip)
    return TableCell(text, kind, "\n\n".join(lines + explanations))


def _transition_part(
    transition: tr.Transition | None, year: int, season: str, threshold: int, days: dict[date, float]
) -> _Part:
    if transition is None or transition.status == tr.STATUS_NO_DATA:
        return _Part(NO_DATA_TEXT, KIND_NO_DATA, _no_data_tip(year, season, days))
    if transition.day is None:
        return _Part(NONE_TEXT, KIND_NONE, _none_tip(threshold, season))
    return _Part(_date_text(transition.day, year), KIND_VALUE)


def _base_tip(mean: tr.MeanValue, what: str) -> str:
    if mean.standard:
        base = f"средняя {what} за {tr.NORM_BASE_FIRST_YEAR}–{tr.NORM_BASE_LAST_YEAR}"
    else:
        base = f"средняя {what} по архиву станции, {mean.first_year}–{mean.last_year}"
    text = f"{base}: {mean.years} {_years_word(mean.years)} с данными"
    if mean.none_years:
        text += f"; ещё в {mean.none_years} {_years_word(mean.none_years, genitive=True)} перехода не было"
    return text + "."


def _mean_cell(means: list[tr.MeanValue | None], what: str, fmt: Callable[[tr.MeanValue], str], empty_tip: str) -> TableCell:
    parts = [_Part(NO_VALUE, KIND_NONE) if mean is None else _Part(fmt(mean), KIND_VALUE) for mean in means]
    if all(mean is None for mean in means):
        return TableCell(NO_VALUE, KIND_NONE, empty_tip)
    cell = _combine(parts)
    bases = [
        f"{tr.METHOD_NAMES[m]} — {_base_tip(mean, what)}" if mean else f"{tr.METHOD_NAMES[m]} — {empty_tip}"
        for m, mean in zip(tr.METHODS, means)
    ]
    return TableCell(cell.text, KIND_VALUE, "\n\n".join([cell.tip, *bases]))


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
               f"Дата устойчивого перехода средней суточной температуры через {t} °C {SEASON_NAMES[s]}. "
               f"В ячейке: {METHODS_ORDER}.")
        for s, t in transition_columns()
    ]
    rows = []
    for year in data.years:
        cells = [
            _combine([
                _transition_part(data.get(year, season, threshold, method), year, season, threshold, days)
                for method in tr.METHODS
            ])
            for season, threshold in transition_columns()
        ]
        rows.append(TableRow(str(year), cells))
    if data.years:
        means = [
            _mean_cell(
                [tr.mean_date(data, season, threshold, method) for method in tr.METHODS],
                "дата", lambda mean: f"{mean.day:%d.%m}", "ни в одном году нет даты перехода.",
            )
            for season, threshold in transition_columns()
        ]
        rows.append(TableRow(MEAN_DATE_LABEL, means, mean=True))
    return Table(columns, rows)


def _duration_part(data: tr.StationTransitions, year: int, threshold: int, method: str,
                   days: dict[date, float]) -> _Part:
    duration = data.duration(year, threshold, method)
    if duration is not None:
        return _Part(str(duration), KIND_VALUE)
    pair = [(s, data.get(year, s, threshold, method)) for s in tr.SEASONS]
    missing = [s for s, t in pair if t is None or t.status == tr.STATUS_NO_DATA]
    if missing:
        return _Part(NO_DATA_TEXT, KIND_NO_DATA, "\n\n".join(_no_data_tip(year, s, days) for s in missing))
    names = " и ".join(("весеннего" if s == tr.SPRING else "осеннего") for s, t in pair if t.day is None)
    return _Part(NO_VALUE, KIND_NONE, f"Длительность не считается: {names} перехода через {threshold} °C не было.")


def periods_table(data: tr.StationTransitions, days: dict[date, float]) -> Table:
    columns = [
        Column(f"{PERIOD_NAMES[t]}\n(выше {t} °C)", GROUP_DURATION,
               tip=f"Дней от весеннего перехода через {t} °C до осеннего. В ячейке: {METHODS_ORDER}.")
        for t in tr.THRESHOLDS
    ]
    rows = []
    for year in data.years:
        cells = [
            _combine([_duration_part(data, year, threshold, method, days) for method in tr.METHODS])
            for threshold in tr.THRESHOLDS
        ]
        rows.append(TableRow(str(year), cells))
    if data.years:
        means = [
            _mean_cell(
                [tr.mean_duration(data, threshold, method) for method in tr.METHODS],
                "длительность", lambda mean: str(round(mean.days)), "ни в одном году нет обеих дат перехода.",
            )
            for threshold in tr.THRESHOLDS
        ]
        rows.append(TableRow(MEAN_DURATION_LABEL, means, mean=True))
    return Table(columns, rows)
