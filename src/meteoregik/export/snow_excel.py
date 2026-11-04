"""Снежные показатели: общая раскладка строк для экрана и Excel."""

from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

from meteoregik.reports.snow_data import SnowData, SnowPeriod, format_maximum_dates

HEADERS = ("Период", "Дней со снегом", "Максимум, см", "Даты максимума",
           "Морозы при малоснежье, дни", "Доступная часть / оценка температуры")
HELP_SNOW = (
    "Снег есть, если хотя бы одна числовая высота за день больше нуля. "
    "Суточная высота — максимум измерений. Пустые и нечисловые значения, "
    "а также отсутствующие дни внутри rp5-архива считаются нулём. "
    "За границами rp5-архива данных нет. Правило полноты «3/5» не применяется."
)
HELP_FROST = (
    "День учитывается, если сохранённая среднесуточная температура ≤ −20 °C "
    "и суточная высота снега ≤ 5 см, включая бесснежные дни. "
    "Без средней температуры день не оценён. Пропуски высоты внутри rp5-архива "
    "считаются нулём и могут увеличивать счётчик морозов при малоснежье."
)


def period_label(period: SnowPeriod, seasonal: bool = False) -> str:
    return (f"{period.start.year}/{period.end.year % 100:02d}" if seasonal
            else f"{period.start:%m.%Y}")


def coverage_text(period: SnowPeriod) -> str:
    """Не выдавать доступную часть и неоценённые дни за полные наблюдения."""
    days = (period.available_end - period.available_start).days + 1
    text = (f"Доступная часть: {period.available_start:%d.%m.%Y}–{period.available_end:%d.%m.%Y}"
            if period.partial else "Полный период")
    missing = days - period.temperature_days
    return text + (f"; без температуры: {missing} дн." if missing else "; температура за все дни")


def period_values(period: SnowPeriod, seasonal: bool = False) -> tuple[object, ...]:
    return (period_label(period, seasonal), period.snow_days, period.max_depth,
            format_maximum_dates(period.maximum_dates),
            period.frost_days if period.frost_days is not None else "нет данных",
            coverage_text(period))


def export_snow(path: str | Path, title: str, data: SnowData) -> None:
    """Оба уровня отчёта, все даты максимумов и оговорки полноты."""
    workbook = Workbook()
    workbook.remove(workbook.active)
    for name, periods, seasonal in (("По месяцам", data.months, False),
                                    ("Зимние сезоны", data.seasons, True)):
        sheet = workbook.create_sheet(name)
        sheet.append([title])
        sheet.append([HELP_SNOW + " " + HELP_FROST])
        sheet.merge_cells("A1:F1")
        sheet.merge_cells("A2:F2")
        sheet["A1"].font = Font(bold=True, size=13)
        sheet["A2"].alignment = Alignment(wrap_text=True, vertical="top")
        sheet.row_dimensions[2].height = 72
        sheet.append(HEADERS)
        for cell in sheet[3]:
            cell.font = Font(bold=True)
            cell.fill = PatternFill("solid", fgColor="DCE7EE")
        for period in periods:
            sheet.append(period_values(period, seasonal))
        for row in sheet.iter_rows(min_row=3):
            for cell in row:
                cell.alignment = Alignment(wrap_text=True, vertical="top")
        for column, width in zip("ABCDEF", (15, 18, 18, 65, 26, 66)):
            sheet.column_dimensions[column].width = width
        for row_number, period in enumerate(periods, 4):
            # Excel не подбирает высоту автоматически при открытии файла.
            dates = format_maximum_dates(period.maximum_dates)
            sheet.row_dimensions[row_number].height = max(32, 16 * (len(dates) // 55 + 1))
        sheet.freeze_panes = "B4"
        sheet.auto_filter.ref = f"A3:F{sheet.max_row}"
        sheet.print_title_rows = "1:3"
    workbook.save(path)
