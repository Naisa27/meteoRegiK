"""Экран "Дашборд": три вкладки. "Исходные данные" (корректировка
2026-10-10) — что залито и где пропуски, по всей базе
(ui/dashboard_source_tab.py). "Осадки" (корректировка 2026-10-11) —
ui/dashboard_precip_tab.py; станция и период у неё общие с "Температурным
режимом", поэтому фильтр стоит над вкладками, а данные обеих вкладок
считаются одним фоновым расчётом (два расчёта разом оба пошли бы
досчитывать нормы одной станции). Остальное в этом модуле — вкладка
"Температурный режим", этап 1 (решение пользователя 2026-10-01, plan.md, "Экраны
UI", п.4): одна станция + период -> сверху карточки-сводки, ниже теплокарта
"год × месяц" и аномалии среднегодовой температуры с трендом. Только по уже
посчитанной температуре; агрометеорологические показатели появятся позже
отдельной вкладкой (после их расчёта в разделе "Расчёт показателей").

Фильтры и их поведение — как в "Просмотре агрегатных данных"
(ui/query_panel.py): станция обязательна и не запоминается между
открытиями, пустой период = весь архив станции, результат строится только
по "Применить", загрузка в фоне с лоадером поверх результата. Все расчёты —
в reports/dashboard_data.py (без Qt), экран только раскладывает готовое.

Карточки — текстом с явной формулировкой ("на 0.6 °C выше нормы"), не
только знаком числа или цветом; значения — цветом обычного текста (dataviz:
text wears text tokens), цвет "тепло/холодно" есть только на графиках.
В правом верхнем углу каждой карточки — значок "?" с подсказкой, как
посчитан показатель (решение пользователя 2026-10-04). Подсказка
показывается не только при наведении мыши, но и по фокусу с клавиатуры и по
щелчку — иначе она недоступна без мыши.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from typing import Callable, Iterable

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QAbstractButton,
    QButtonGroup,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QStackedWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy.engine import Engine

from meteoregik.reports.dashboard_data import (
    MONTHS_GENITIVE,
    MONTHS_NOMINATIVE,
    MIN_NORM_BASE_YEARS,
    MIN_TREND_YEARS,
    NORM_BASE_FIRST_YEAR,
    NORM_BASE_LAST_YEAR,
    DashboardData,
    MIN_YEAR_RECORD_MONTHS,
    MAX_MISSING_DECADE_DAYS,
    DecadeRecord,
    MonthRecord,
    YearAnomaly,
    build_dashboard,
    describe_anomaly,
    format_temperature,
    station_day_bounds,
)
from meteoregik.reports.precipitation_data import PrecipitationData, build_precipitation
from meteoregik.reports.precipitation_data import station_day_bounds as precipitation_day_bounds
from meteoregik.resources import DASHBOARD_ICON_PATH
from meteoregik.stations.choices import load_stations_with_computed_indicators
from meteoregik.ui import theme
from meteoregik.ui.background import run_in_background
from meteoregik.ui.buttons import reserve_focus_width
from meteoregik.ui.dashboard_charts import (
    HEATMAP_ANOMALY,
    HEATMAP_VALUE,
    AnomalyChart,
    TemperatureHeatmap,
)
from meteoregik.ui.dashboard_precip_tab import PrecipitationTab
from meteoregik.ui.dashboard_source_tab import SourceDataTab
from meteoregik.ui.dashboard_widgets import (
    HELP_QSS,
    CardText,
    GrooveDivider as _GrooveDivider,
    StatCard as _StatCard,
    format_count as _format_count,
    format_day as _format_day,
    scrollable,
)
from meteoregik.ui.date_field import DateField, DateFieldError
from meteoregik.ui.dialogs import enable_min_max_buttons, minimize_owner_instead
from meteoregik.ui.empty_state import NO_DATA_HINT, hint_label
from meteoregik.ui.loading_overlay import LoadingOverlay
from meteoregik.ui.station_combo import StationCombo

_TITLE = "Дашборд"
TAB_TEMPERATURE = "Температурный режим"
TAB_PRECIPITATION = "Осадки"
TAB_SOURCE = "Исходные данные"
# Тёмно-синий текст переключателя теплокарты: 11.6:1 на белом
HEATMAP_MODE_TEXT = "#1E3A6E"
_START_HINT = (
    "Выберите метеостанцию и нажмите «Применить».\n"
    "Если период не указан, берётся весь архив станции."
)
_NO_VALUE = "—"

DASHBOARD_QSS = f"""
QFrame#statCard {{
    background: {theme.INPUT_BG};
    border: 1px solid {theme.BORDER};
    border-radius: 8px;
}}
QLabel#statCaption {{ color: {theme.TEXT_SECONDARY}; font-size: 12px; }}
QLabel#statValue {{ color: {theme.TEXT_PRIMARY}; font-size: 20px; font-weight: 600; }}
QLabel#statNote {{ color: {theme.TEXT_SECONDARY}; font-size: 12px; }}
QFrame#heatmapMode {{
    background: {theme.INPUT_BG};
    border: 1px solid {theme.HEADER_BORDER};
    border-radius: 8px;
}}
QFrame#heatmapMode QRadioButton {{ color: {HEATMAP_MODE_TEXT}; font-weight: 600; }}
QLabel#chartTitle {{ color: {theme.TEXT_PRIMARY}; font-size: 14px; font-weight: 600; }}
QLabel#chartNote {{ color: {theme.TEXT_SECONDARY}; font-size: 12px; }}
QScrollArea {{ background: transparent; border: none; }}
QWidget#scrollViewport, QWidget#scrollContent {{ background: transparent; }}
""" + HELP_QSS

# Подсказки "?" — как посчитан показатель карточки. Описывают фактический
# расчёт (reports/dashboard_data.py, calculations/temperature.py), а не
# "учебное" определение: при изменении расчёта править и их.
_DAILY_MEAN = (
    "Средняя температура дня: для данных rp5 — среднее всех измерений за сутки "
    "(обычно их 8, каждые 3 часа; если части нет — по тем, что есть), для файлов "
    "ГГ — готовое значение из файла."
)
_COMPLETE_MONTH = (
    "Месяц считается полным, если в нём пропущено не больше 5 дней и не больше "
    "3 дней подряд (правило ВМО «3/5»)."
)
_COMPLETE_YEAR = "Год считается полным, если полны все 12 его месяцев. " + _COMPLETE_MONTH
# Сколько рекордов (дат, месяцев) перечислять на карточке, дальше — "и ещё N"
MAX_LISTED = 5
# Месяцы/декады крупным шрифтом и их отклонения длиннее дат — их меньше, чтобы
# карточка не обрезалась снизу в обычном по высоте окне: до MAX_LISTED_MONTHS
# перечисляются все, больше — только первый и "и ещё N"
MAX_LISTED_MONTHS = 2
MAX_LISTED_MONTH_NOTES = 1

_NORM_BASE = f"{NORM_BASE_FIRST_YEAR}–{NORM_BASE_LAST_YEAR}"
_NORM_WHAT = (
    "Норма — обычная для станции температура. По правилам Всемирной "
    f"метеорологической организации (ВМО) это средняя за 30 лет, {_NORM_BASE}; "
    "учитываются только полные месяцы и годы. Норма одна и та же для любого "
    f"выбранного периода и для любого года, в том числе до {NORM_BASE_FIRST_YEAR}-го."
)
_NORM_FALLBACK = (
    f"Если у станции за {_NORM_BASE} меньше {MIN_NORM_BASE_YEARS} полных лет, вместо нормы берётся "
    "средняя по всему архиву станции (в подписи — «средняя по архиву»). Тогда "
    "весь архив почти всегда выходит «на уровне»: данные сравниваются сами с собой."
)
_DAILY_EXTREME_SOURCE = (
    "Откуда берётся {what} температура дня: для файлов ГГ — готовое значение из "
    "файла; для rp5 — {which} из измерений за сутки (столбец T, измерения каждые "
    "3 часа), а не столбец {column}. Настоящий {peak} мог прийтись между "
    "измерениями, поэтому значение может быть немного {direction} фактического."
)
_EXTREME_PERIOD = (
    "{caption} температура за выбранный период (если период не выбран — за весь "
    "архив станции) и даты, когда она наблюдалась. Если такая температура была в "
    "несколько дней, перечислены все даты."
)

HELP_MEAN = (
    "Берутся средние температуры всех дней периода, за которые есть данные, "
    "складываются и делятся на число таких дней. Дни без данных просто "
    "пропускаются — нулями они не считаются. Если период не выбран, берётся "
    "весь архив станции. "
    + _DAILY_MEAN
    + "\n\n"
    + _NORM_WHAT
    + "\n\nСравнение с нормой: каждый день сравнивается с нормой своего месяца "
    "(январский — с январской, июльский — с июльской), и эти разницы "
    "усредняются. Поэтому летний период не окажется «выше нормы» только "
    "оттого, что летом теплее. В скобках — сама норма для выбранного периода: "
    "нормы месяцев, усреднённые по его дням (для лета — летняя, для целого года "
    "почти совпадает с годовой).\n\n"
    + _NORM_FALLBACK
)
HELP_ABS_MAX = (
    _EXTREME_PERIOD.format(caption="Самая высокая")
    + "\n\n"
    + _DAILY_EXTREME_SOURCE.format(what="максимальная", which="самое высокое", column="Tx",
                                   peak="пик", direction="ниже")
)
HELP_ABS_MIN = (
    _EXTREME_PERIOD.format(caption="Самая низкая")
    + "\n\n"
    + _DAILY_EXTREME_SOURCE.format(what="минимальная", which="самое низкое", column="Tn",
                                   peak="минимум", direction="выше")
)
HELP_COMPLETENESS = (
    "Доля календарных дней периода, за которые есть суточная средняя температура: "
    "дни с данными / все дни периода × 100 %."
)
_YEAR_RECORD = (
    "Год с {which} среднегодовой температурой среди полных лет, целиком попавших в "
    "период. Если у нескольких лет средняя одинаковая, перечислены все. "
    "Среднегодовая — среднее суточных средних за год. "
    + _COMPLETE_YEAR
    + "\n\nОтклонение — от годовой нормы, её значение указано в скобках. "
    + _NORM_WHAT
    + "\n\n"
    + _NORM_FALLBACK
)
_MONTH_RECORD = (
    "Месяц с {which} средней температурой среди полных месяцев, целиком попавших в "
    "период. Если у нескольких месяцев средняя одинаковая, перечислены все. "
    "Средняя месяца — среднее суточных средних за месяц. "
    + _COMPLETE_MONTH
    + "\n\nОтклонение — от нормы этого месяца (июль сравнивается с нормой июля), "
    "её значение указано в скобках. "
    + _NORM_WHAT
    + "\n\n"
    + _NORM_FALLBACK
)
_ANOMALOUS_MONTH = (
    "Месяц, который сильнее всего {which} своей нормы: каждый полный месяц периода "
    "сравнивается с нормой того же месяца (декабрь — с нормой декабря, июль — с "
    "нормой июля), и выбирается наибольшая разница. В отличие от карточки «Самый "
    "{plain} месяц» здесь может оказаться и {example} — если он был необычно "
    "{adjective} для своего времени года. Учитываются только месяцы {which} нормы; "
    "если таких нет, карточка пустая. Если разница одинаковая у нескольких "
    "месяцев, перечислены все.\n\nВ скобках — норма этого месяца, после неё — "
    "средняя температура самого месяца. "
    + _COMPLETE_MONTH
    + "\n\n"
    + _NORM_WHAT
    + "\n\n"
    + _NORM_FALLBACK
)
HELP_ANOMALOUS_WARM_MONTH = _ANOMALOUS_MONTH.format(
    which="выше", plain="тёплый", example="зимний месяц", adjective="тёплым"
)
HELP_ANOMALOUS_COLD_MONTH = _ANOMALOUS_MONTH.format(
    which="ниже", plain="холодный", example="летний месяц", adjective="холодным"
)
_DECADE_RECORD = (
    "Выбранный период короче {months} месяцев — полных лет в нём не больше "
    "одного, поэтому вместо самого {plain} года показывается самая {adjective} "
    "декада. Декады — 1–10, 11–20 и 21-е — последнее число месяца. Выбирается "
    "декада с {which} средней температурой среди полных декад, целиком "
    "попавших в период; если у нескольких декад средняя одинаковая, "
    "перечислены все. Средняя декады — среднее суточных средних за её дни. "
    "Декада считается полной, если в ней пропущено не больше {missing} дней."
    + "\n\nОтклонение — от нормы той же декады того же месяца (2-я декада июля "
    "сравнивается с нормой 2-й декады июля), её значение указано в скобках. "
    + _NORM_WHAT
    + "\n\n"
    + _NORM_FALLBACK
)
HELP_WARMEST_DECADE = _DECADE_RECORD.format(
    months=MIN_YEAR_RECORD_MONTHS, plain="тёплого", adjective="тёплая", which="наибольшей",
    missing=MAX_MISSING_DECADE_DAYS,
)
HELP_COLDEST_DECADE = _DECADE_RECORD.format(
    months=MIN_YEAR_RECORD_MONTHS, plain="холодного", adjective="холодная", which="наименьшей",
    missing=MAX_MISSING_DECADE_DAYS,
)
HELP_WARMEST_YEAR = _YEAR_RECORD.format(which="наибольшей")
HELP_COLDEST_YEAR = _YEAR_RECORD.format(which="наименьшей")
HELP_WARMEST_MONTH = _MONTH_RECORD.format(which="наибольшей")
HELP_COLDEST_MONTH = _MONTH_RECORD.format(which="наименьшей")


def _join_limited(items: list[str], sep: str, rest_unit: str, limit: int = MAX_LISTED) -> str:
    """Список рекордов через sep. Больше MAX_LISTED — первые и "и ещё N
    <rest_unit>": иначе на длинном архиве карточка могла бы вытянуться на
    пол-экрана и растянуть весь ряд карточек."""
    shown = sep.join(items[:limit])
    rest = len(items) - limit
    return f"{shown} и ещё {rest} {rest_unit}" if rest > 0 else shown


def _format_days(days: tuple[date, ...]) -> str:
    return _join_limited([_format_day(d) for d in days], ", ", "дн.")


def _big_list_limit(records: tuple[object, ...]) -> int:
    return len(records) if len(records) <= MAX_LISTED_MONTHS else 1


def _decade_name(record: DecadeRecord, *, with_year: bool = True) -> str:
    """"2-я декада июля 2020" (без года — для подписи нормы)."""
    text = f"{record.number}-я декада {MONTHS_GENITIVE[record.start.month - 1]}"
    return f"{text} {record.start.year}" if with_year else text


def _format_decades(records: tuple[DecadeRecord, ...]) -> str:
    text = _join_limited([_decade_name(r) for r in records], ", ", "дек.", _big_list_limit(records))
    return text[:1].upper() + text[1:]


def _format_months(records: tuple[MonthRecord, ...]) -> str:
    """"Июль 2010, июль 2021" — с заглавной только первое слово строки."""
    text = _join_limited([f"{MONTHS_NOMINATIVE[r.month - 1]} {r.year}" for r in records], ", ", "мес.", _big_list_limit(records))
    return text[:1].upper() + text[1:]


def _unique(texts: Iterable[str]) -> list[str]:
    """Без повторов, с сохранением порядка."""
    return list(dict.fromkeys(texts))


def _norm_name(data: DashboardData, month: int | None = None) -> str:
    """С чем сравнивали — в родительном падеже для describe_anomaly. При
    откате на весь архив это не норма ВМО, и подпись этого не скрывает."""
    if data.norm_standard:
        return f"нормы {MONTHS_GENITIVE[month - 1]}" if month else f"нормы {_NORM_BASE}"
    return f"средней {MONTHS_GENITIVE[month - 1]} по архиву" if month else "средней по архиву"


def _decade_norm_name(data: DashboardData, record: DecadeRecord) -> str:
    """"нормы 2-й декады июля" / "средней 2-й декады июля по архиву"."""
    name = f"{record.number}-й декады {MONTHS_GENITIVE[record.start.month - 1]}"
    return f"нормы {name}" if data.norm_standard else f"средней {name} по архиву"


def card_texts(data: DashboardData) -> list[CardText]:
    """Тексты десяти карточек по порядку: средняя, максимум, минимум,
    полнота; тёплый/холодный год (для периода короче 24 месяцев — декада),
    тёплый/холодный месяц; аномально
    тёплый/холодный месяц (правая колонка). Отдельно от
    виджетов — проверяется тестами без раскладки."""
    if data.period_mean is None:
        mean = CardText("Средняя суточная температура за период", _NO_VALUE, "нет суточных значений в периоде")
    elif data.period_anomaly is None:
        mean = CardText("Средняя суточная температура за период", format_temperature(data.period_mean),
                        "норма не рассчитана: нет полных месяцев")
    else:
        # Значение нормы в скобках — чтобы цифра была перед глазами, а не только "выше/ниже"
        note = describe_anomaly(data.period_anomaly, _norm_name(data))
        if data.period_norm is not None:
            note += f" ({format_temperature(data.period_norm)})"
        mean = CardText("Средняя суточная температура за период", format_temperature(data.period_mean), note)

    def extreme(caption: str, record) -> CardText:
        if record is None:
            return CardText(caption, _NO_VALUE, "нет данных в периоде")
        return CardText(caption, format_temperature(record.value), _format_days(record.days))

    missing = data.days_total - data.days_with_data
    share = data.days_with_data / data.days_total * 100 if data.days_total else 0.0
    completeness = CardText(
        "Полнота данных",
        f"{share:.1f} %",
        f"{_format_count(data.days_with_data)} из {_format_count(data.days_total)} дн."
        + (f", пропущено {_format_count(missing)}" if missing else ", без пропусков"),
    )

    def year(caption: str, records: tuple[YearAnomaly, ...]) -> CardText:
        if not records:
            return CardText(caption, _NO_VALUE, "нет полных лет в периоде")
        # у всех годов-рекордсменов одна и та же средняя, а значит и отклонение
        first = records[0]
        note = f"{format_temperature(first.value)}, {describe_anomaly(first.anomaly, _norm_name(data))}"
        if data.annual_norm is not None:
            note += f" ({format_temperature(data.annual_norm.value)})"
        return CardText(caption, ", ".join(str(r.year) for r in records), note)

    def anomaly_text(record: MonthRecord) -> str:
        """"на 2.0 °C выше нормы июля (+19.3 °C)" — с самой нормой в скобках."""
        text = describe_anomaly(record.anomaly, _norm_name(data, record.month))
        return text + (f" ({format_temperature(record.norm)})" if record.norm is not None else "")

    def month(caption: str, records: tuple[MonthRecord, ...]) -> CardText:
        if not records:
            return CardText(caption, _NO_VALUE, "нет полных месяцев в периоде")
        # средняя у всех одна; отклонение — своё у каждого календарного месяца
        # (равные +21.3 °C в июле и августе — разные отклонения от разных норм)
        note = format_temperature(records[0].value)
        anomalies = _unique(anomaly_text(r) for r in records if r.anomaly is not None)
        if anomalies:
            note += ", " + _join_limited(anomalies, "; ", "мес.", MAX_LISTED_MONTH_NOTES)
        return CardText(caption, _format_months(records), note)

    def anomalous_month(caption: str, records: tuple[MonthRecord, ...], side: str) -> CardText:
        if not records:
            if not data.warmest_months:
                reason = "нет полных месяцев в периоде"
            elif all(m.anomaly is None for m in data.warmest_months + data.coldest_months):
                reason = "норма не рассчитана"
            else:
                reason = f"нет месяцев {side} нормы в периоде"
            return CardText(caption, _NO_VALUE, reason)
        note = _join_limited(
            _unique(f"{anomaly_text(r)}, средняя {format_temperature(r.value)}" for r in records), "; ", "мес.",
            MAX_LISTED_MONTH_NOTES,
        )
        return CardText(caption, _format_months(records), note)

    def decade(caption: str, records: tuple[DecadeRecord, ...]) -> CardText:
        if not records:
            return CardText(caption, _NO_VALUE, "нет полных декад в периоде")
        note = format_temperature(records[0].value)
        anomalies = _unique(
            f"{describe_anomaly(r.anomaly, _decade_norm_name(data, r))} ({format_temperature(r.norm)})"
            for r in records
            if r.anomaly is not None and r.norm is not None
        )
        if anomalies:
            note += ", " + _join_limited(anomalies, "; ", "дек.", MAX_LISTED_MONTH_NOTES)
        return CardText(caption, _format_decades(records), note)

    if data.short_period:
        warm_record = (decade("Самая тёплая декада", data.warmest_decades), HELP_WARMEST_DECADE)
        cold_record = (decade("Самая холодная декада", data.coldest_decades), HELP_COLDEST_DECADE)
    else:
        warm_record = (year("Самый тёплый год", data.warmest_years), HELP_WARMEST_YEAR)
        cold_record = (year("Самый холодный год", data.coldest_years), HELP_COLDEST_YEAR)

    cards = [
        (mean, HELP_MEAN),
        (extreme("Абсолютный максимум за период", data.abs_max), HELP_ABS_MAX),
        (extreme("Абсолютный минимум за период", data.abs_min), HELP_ABS_MIN),
        (completeness, HELP_COMPLETENESS),
        warm_record,
        cold_record,
        (month("Самый тёплый месяц", data.warmest_months), HELP_WARMEST_MONTH),
        (month("Самый холодный месяц", data.coldest_months), HELP_COLDEST_MONTH),
        (anomalous_month("Самый аномально тёплый месяц", data.anomalous_warm_months, "теплее"),
         HELP_ANOMALOUS_WARM_MONTH),
        (anomalous_month("Самый аномально холодный месяц", data.anomalous_cold_months, "холоднее"),
         HELP_ANOMALOUS_COLD_MONTH),
    ]
    return [replace(text, help=help_text) for text, help_text in cards]


def anomaly_title(data: DashboardData) -> str:
    """Короткий период — график по месяцам (см. AnomalyChart)."""
    kind = "среднемесячной" if data.short_period else "среднегодовой"
    return f"Отклонение {kind} температуры от нормы"


def anomaly_note(data: DashboardData) -> str:
    """Пояснение под заголовком графика аномалий — от чего отсчитано."""
    if data.short_period:
        base = (
            f"нормы этого месяца за {_NORM_BASE}" if data.norm_standard
            else f"средней этого месяца по архиву станции (за {_NORM_BASE} меньше "
            f"{MIN_NORM_BASE_YEARS} полных лет)"
        )
        return (
            f"Период короче {MIN_YEAR_RECORD_MONTHS} месяцев, поэтому по месяцам: столбик — "
            f"отклонение средней месяца от {base}."
        )
    norm = data.annual_norm
    if norm is None:
        return "Норма не рассчитана: у станции нет ни одного полного года."
    years = f"{norm.first_year}–{norm.last_year}" if norm.first_year != norm.last_year else str(norm.first_year)
    if norm.standard:
        text = (
            f"Норма {format_temperature(norm.value)} — средняя за {norm.year_count} полн. лет "
            f"базового периода ВМО {_NORM_BASE}. Показаны только полные годы."
        )
    else:
        text = (
            f"За {_NORM_BASE} меньше {MIN_NORM_BASE_YEARS} полных лет, поэтому нулевая линия — "
            f"средняя по архиву {format_temperature(norm.value)} за {norm.year_count} полн. лет ({years}). "
            "Показаны только полные годы."
        )
    if data.year_anomalies and data.trend is None:
        text += f" Линия тренда — от {MIN_TREND_YEARS} полных лет в периоде."
    return text


_Loaded = tuple[DashboardData, PrecipitationData]


@dataclass(frozen=True)
class _AppliedFilter:
    wmo_index: int
    date_from: date
    date_to: date
    station_title: str


class DashboardDialog(QDialog):
    def __init__(
        self,
        engine: Engine,
        parent=None,
        run_async: bool = True,
        wmo_index: int | None = None,
        tab: str = TAB_TEMPERATURE,
    ) -> None:
        """wmo_index — станция, которую сразу выбрать и показать за весь её
        архив (переход из карточки станции на экране "Карта"); tab — какую
        вкладку открыть (TAB_*): с карты осадков — "Осадки"."""
        super().__init__(parent)
        enable_min_max_buttons(self)
        self.engine = engine
        # run_async=False — расчёт в том же потоке (тесты).
        self._run_async = run_async
        self._request_id = 0
        self.setWindowTitle(_TITLE)
        self.setWindowIcon(QIcon(str(DASHBOARD_ICON_PATH)))
        if parent is not None:
            self.resize(parent.size())
        else:
            self.resize(1100, 750)
        self.setStyleSheet(theme.DIALOG_QSS + DASHBOARD_QSS)
        self._build_ui()
        choices = load_stations_with_computed_indicators(self.engine)
        self.station_combo.set_choices(choices)
        if not choices:
            # Выбирать не из чего — подсказка "выберите станцию" вела бы в тупик
            self._hint.setText(NO_DATA_HINT)
            self.precipitation_tab.set_hint(NO_DATA_HINT)
        for index in range(self.tabs.count()):
            if self.tabs.tabText(index) == tab:
                self.tabs.setCurrentIndex(index)
        if wmo_index is not None:
            self.station_combo.select_wmo(wmo_index)
            self._apply_filters()

    # --- построение интерфейса ---

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 14, 20, 16)
        layout.setSpacing(10)

        # Фильтр "станция + период" — над вкладками: он общий для
        # "Температурного режима" и "Осадков". "Исходные данные" — по всей
        # базе, на этой вкладке фильтр скрыт (_on_tab_changed).
        self._filter_card = self._build_filter_card()
        layout.addWidget(self._filter_card)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.addTab(self._build_temperature_tab(), TAB_TEMPERATURE)
        self.precipitation_tab = PrecipitationTab(_START_HINT)
        self.tabs.addTab(self.precipitation_tab, TAB_PRECIPITATION)
        self.source_tab = SourceDataTab(self.engine, run_async=self._run_async)
        self.tabs.addTab(self.source_tab, TAB_SOURCE)
        self.tabs.currentChanged.connect(self._on_tab_changed)
        layout.addWidget(self.tabs, stretch=1)

        close_row = QHBoxLayout()
        close_row.addStretch(1)
        close_btn = QPushButton("Закрыть")
        close_btn.setObjectName("secondary")
        close_btn.clicked.connect(self.accept)
        close_row.addWidget(close_btn)
        layout.addLayout(close_row)
        reserve_focus_width(self.apply_btn, close_btn)

    def _on_tab_changed(self, index: int) -> None:
        on_source = self.tabs.widget(index) is self.source_tab
        self._filter_card.setVisible(not on_source)
        if on_source:
            self.source_tab.ensure_loaded()

    def _build_temperature_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 10, 0, 0)
        layout.setSpacing(10)

        self._station_label = QLabel("")
        self._station_label.setStyleSheet("font-weight: 600; font-size: 14px;")
        layout.addWidget(self._station_label)

        # До первого "Применить" — подсказка, что делать, а не пустые карточки.
        self._stack = QStackedWidget()
        self._hint = hint_label(_START_HINT)
        self._stack.addWidget(self._hint)
        # Прокручивается только страница результатов, а не вся стопка: стопка
        # требует места под самую высокую страницу, и подсказка по её центру
        # уезжала вниз, а в невысоком окне — за край (скриншот 2026-10-11).
        self._content = scrollable(self._build_content())
        self._stack.addWidget(self._content)

        results_area = QWidget()
        results_layout = QGridLayout(results_area)
        results_layout.setContentsMargins(0, 0, 0, 0)
        results_layout.addWidget(self._stack, 0, 0)
        self.loading_overlay = LoadingOverlay("Считаем показатели…")
        results_layout.addWidget(self.loading_overlay, 0, 0)
        layout.addWidget(results_area, stretch=1)
        return tab

    def _build_filter_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("card")
        row = QHBoxLayout(card)
        row.setContentsMargins(16, 12, 16, 12)
        row.setSpacing(10)

        row.addWidget(QLabel("Метеостанция:"))
        self.station_combo = StationCombo()
        row.addWidget(self.station_combo, stretch=1)

        period = QFrame()
        period.setObjectName("group")
        period_layout = QHBoxLayout(period)
        period_layout.setContentsMargins(12, 6, 12, 6)
        period_layout.setSpacing(8)
        period_title = QLabel("Период:")
        period_title.setStyleSheet("font-weight: 600;")
        period_layout.addWidget(period_title)
        period_layout.addWidget(QLabel("с"))
        self.date_from_edit = DateField()
        period_layout.addWidget(self.date_from_edit)
        period_layout.addWidget(QLabel("по"))
        self.date_to_edit = DateField()
        period_layout.addWidget(self.date_to_edit)
        row.addWidget(period)

        self.apply_btn = QPushButton("Применить")
        self.apply_btn.setObjectName("primary")
        self.apply_btn.clicked.connect(self._apply_filters)
        row.addWidget(self.apply_btn)
        return card

    def _build_content(self) -> QWidget:
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        cards = QGridLayout()
        cards.setHorizontalSpacing(10)
        cards.setVerticalSpacing(10)
        self.cards = [_StatCard() for _ in range(10)]
        # первые восемь — сетка 4 × 2, аномальные месяцы — правая колонка друг под другом
        for i, card in enumerate(self.cards):
            row, col = (i // 4, i % 4) if i < 8 else (i - 8, 4)
            cards.addWidget(card, row, col)
        for col in range(5):
            cards.setColumnStretch(col, 1)
        layout.addLayout(cards)

        charts = QHBoxLayout()
        charts.setSpacing(16)
        self.heatmap = TemperatureHeatmap()
        charts.addLayout(self._heatmap_block(), stretch=1)
        charts.addWidget(_GrooveDivider())
        self.anomaly_chart = AnomalyChart()
        self._anomaly_note = QLabel()
        self._anomaly_title = QLabel()  # текст — в _show(), по виду периода
        charts.addLayout(
            self._chart_block(self._anomaly_title, self._anomaly_note,
                              self.anomaly_chart, centered=True),
            stretch=1,
        )
        layout.addLayout(charts, stretch=1)
        return content

    def _heatmap_block(self) -> QVBoxLayout:
        """Теплокарта с переключателем "Отклонение от нормы / Температура" в
        строке заголовка. Переключение — сразу, без "Применить": данные уже
        загружены, меняется только раскраска."""
        block = QVBoxLayout()
        block.setSpacing(2)
        title_row = QHBoxLayout()
        title_row.setSpacing(12)
        self._heatmap_title = QLabel()
        self._heatmap_title.setObjectName("chartTitle")
        title_row.addWidget(self._heatmap_title)
        title_row.addStretch(1)
        # Переключатель — в рамке (как группы фильтров), подписи тёмно-синие:
        # отделяет "настройку вида" от заголовка и подписей самой карты.
        mode_frame = QFrame()
        mode_frame.setObjectName("heatmapMode")
        mode_layout = QHBoxLayout(mode_frame)
        mode_layout.setContentsMargins(10, 3, 10, 3)
        mode_layout.setSpacing(12)
        self.heatmap_anomaly_radio = QRadioButton("Отклонение от нормы")
        self.heatmap_value_radio = QRadioButton("Температура")
        self.heatmap_anomaly_radio.setChecked(True)
        self._heatmap_mode_group = QButtonGroup(self)
        for radio in (self.heatmap_anomaly_radio, self.heatmap_value_radio):
            self._heatmap_mode_group.addButton(radio)
            mode_layout.addWidget(radio)
        title_row.addWidget(mode_frame)
        self._heatmap_mode_group.buttonToggled.connect(self._on_heatmap_mode_toggled)
        block.addLayout(title_row)
        self._heatmap_note = QLabel()
        self._heatmap_note.setObjectName("chartNote")
        self._heatmap_note.setWordWrap(True)
        block.addWidget(self._heatmap_note)
        block.addWidget(self.heatmap, stretch=1)
        self._update_heatmap_texts(short_period=False)
        return block

    def _on_heatmap_mode_toggled(self, button: QAbstractButton, checked: bool) -> None:
        if not checked:
            return
        self.heatmap.set_mode(HEATMAP_ANOMALY if button is self.heatmap_anomaly_radio else HEATMAP_VALUE)
        self._update_heatmap_texts(self._heatmap_short)

    def _update_heatmap_texts(self, short_period: bool) -> None:
        self._heatmap_short = short_period
        unit = "декадам" if short_period else "месяцам"
        anomaly = self.heatmap.mode == HEATMAP_ANOMALY
        self._heatmap_title.setText(
            f"{'Отклонение от нормы' if anomaly else 'Средняя температура'} по {unit}"
        )
        part = "декада" if short_period else "месяц"
        incomplete = (
            "Штриховка — неполная декада (пропущено больше 2 дней)"
            if short_period
            else "Штриховка — неполный месяц (пропущено больше 5 дней или больше 3 подряд)"
        )
        color = (
            f"Красный — {part} теплее своей нормы, синий — холоднее. " if anomaly
            else "Красный — выше 0 °C, синий — ниже. "
        )
        self._heatmap_note.setText(f"{color}{incomplete}, пустая клетка — нет данных.")

    @staticmethod
    def _chart_block(
        title: str | QLabel, note: str | QLabel, chart: QWidget, *, centered: bool = False
    ) -> QVBoxLayout:
        block = QVBoxLayout()
        block.setSpacing(2)
        title_label = title if isinstance(title, QLabel) else QLabel(title)
        title_label.setObjectName("chartTitle")
        block.addWidget(title_label)
        note_label = note if isinstance(note, QLabel) else QLabel(note)
        note_label.setObjectName("chartNote")
        note_label.setWordWrap(True)
        block.addWidget(note_label)
        if centered:
            for label in (title_label, note_label):
                label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        block.addWidget(chart, stretch=1)
        return block

    # --- применение фильтров ---

    def _apply_filters(self) -> None:
        try:
            wmo_index = self.station_combo.selected_wmo()
        except ValueError:
            QMessageBox.warning(self, "Фильтр", "Выберите метеостанцию из списка.")
            return

        dates: list[date | None] = []
        for label, field in (("с", self.date_from_edit), ("по", self.date_to_edit)):
            try:
                dates.append(field.value())
            except DateFieldError as exc:
                QMessageBox.warning(self, "Фильтр", f"Период, поле «{label}»: {exc}")
                return
        date_from, date_to = dates
        if (date_from is None) != (date_to is None):
            QMessageBox.warning(
                self, "Фильтр",
                "Период задаётся двумя датами: заполните обе или очистите обе "
                "(тогда берётся весь архив станции).",
            )
            return
        if date_from is not None and date_to is not None and date_from > date_to:
            QMessageBox.warning(self, "Фильтр", "Начало периода позже его конца.")
            return
        if date_from is None or date_to is None:
            bounds = self._archive_bounds(wmo_index)
            if bounds is None:
                self._clear_results(f"{self.station_combo.currentText()}: посчитанных данных ещё нет.")
                QMessageBox.information(self, _TITLE, "По этой станции ещё нет посчитанных данных.")
                return
            date_from, date_to = bounds

        self._load(_AppliedFilter(wmo_index, date_from, date_to, self.station_combo.currentText()))

    def _archive_bounds(self, wmo_index: int) -> tuple[date, date] | None:
        """"Весь архив" станции — от первого до последнего дня с суточной
        температурой или суммой осадков: период у вкладок общий."""
        bounds = [
            b for b in (station_day_bounds(self.engine, wmo_index), precipitation_day_bounds(self.engine, wmo_index))
            if b is not None
        ]
        if not bounds:
            return None
        return min(first for first, _ in bounds), max(last for _, last in bounds)

    def _load(self, applied: _AppliedFilter) -> None:
        self._request_id += 1
        request_id = self._request_id
        self._set_loading(True)

        def task() -> _Loaded:
            period = (applied.wmo_index, applied.date_from, applied.date_to)
            return build_dashboard(self.engine, *period), build_precipitation(self.engine, *period)

        def on_done(loaded: _Loaded) -> None:
            if request_id != self._request_id:
                return
            self._set_loading(False)
            self._show(applied, *loaded)

        def on_error(exc: BaseException) -> None:
            if request_id != self._request_id:
                return
            self._set_loading(False)
            self._clear_results("Не удалось загрузить данные — показатели не построены.")
            QMessageBox.warning(self, _TITLE, f"Не удалось загрузить данные: {exc}")

        self._run(task, on_done, on_error)

    def _run(
        self,
        task: Callable[[], _Loaded],
        on_done: Callable[[_Loaded], None],
        on_error: Callable[[BaseException], None],
    ) -> None:
        if self._run_async:
            run_in_background(self, task, on_done, on_error)
            return
        try:
            result = task()
        except Exception as exc:  # noqa: BLE001 — тот же путь, что и у фонового режима
            on_error(exc)
        else:
            on_done(result)

    def _set_loading(self, loading: bool) -> None:
        self.loading_overlay.setVisible(loading)
        self.precipitation_tab.set_loading(loading)
        self.apply_btn.setEnabled(not loading)
        if loading:
            self.loading_overlay.raise_()

    def _show(self, applied: _AppliedFilter, data: DashboardData, rain: PrecipitationData) -> None:
        period = f"{_format_day(applied.date_from)}–{_format_day(applied.date_to)}"
        title = f"{applied.station_title} — {period}"
        # У станции может быть температура без осадков и наоборот: пустая
        # вкладка объясняет это сама, окно-сообщение — только если пусты обе.
        if rain.has_data:
            self.precipitation_tab.show_data(title, rain)
        else:
            self.precipitation_tab.clear_results(
                f"{applied.station_title}: за период {period} данных об осадках не найдено."
            )
        if not data.has_data:
            self._clear_temperature(f"{applied.station_title}: за период {period} данных не найдено.")
            if not rain.has_data:
                QMessageBox.information(self, _TITLE, "За выбранный период данных не найдено.")
            return
        self._station_label.setText(title)
        for card, text in zip(self.cards, card_texts(data)):
            card.set_text(text)
        self._anomaly_title.setText(anomaly_title(data))
        self._anomaly_note.setText(anomaly_note(data))
        self._stack.setCurrentWidget(self._content)
        self._update_heatmap_texts(data.short_period)
        self.heatmap.plot(data)
        self.anomaly_chart.plot(data)

    def _clear_results(self, message: str) -> None:
        """Убрать с экрана результаты прошлой выборки на обеих вкладках:
        карточки и графики от другой станции/периода под новым фильтром
        вводили бы в заблуждение. Вместо них — причина, почему пусто."""
        self._clear_temperature(message)
        self.precipitation_tab.clear_results(message)

    def _clear_temperature(self, message: str) -> None:
        self._station_label.setText("")
        self._hint.setText(message)
        self._stack.setCurrentWidget(self._hint)

    # --- свернуть окно ---

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        minimize_owner_instead(self, event)
