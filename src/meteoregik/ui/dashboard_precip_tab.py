"""Вкладка "Осадки" экрана "Дашборд" (корректировка 2026-10-11, этап 3):
девять карточек и четыре графика по сумме осадков одной станции за период.
Станция и период — общие с "Температурным режимом": фильтр стоит над
вкладками (ui/dashboard_view.py), он же загружает данные обеих вкладок одним
расчётом и отдаёт готовое сюда (show_data). Расчёты —
reports/precipitation_data.py (без Qt), графики —
ui/dashboard_precip_charts.py.

Карточки — как у температуры: значение текстом с явной формулировкой
("112 % нормы (+68.0 мм)"), значок "?" объясняет расчёт. Карточка полноты
шире остальных: в ней два вида пропусков — пропущенные дни и дни с
пропущенным сроком наблюдения (решение пользователя 2026-10-12).
"""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtWidgets import (
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from meteoregik.calculations.anomalies import (
    MAX_MISSING_DAYS,
    MAX_MISSING_DECADE_DAYS,
    MAX_MISSING_RUN,
    MIN_NORM_BASE_YEARS,
    NORM_BASE_FIRST_YEAR,
    NORM_BASE_LAST_YEAR,
)
from meteoregik.reports.dashboard_data import (
    MIN_TREND_YEARS,
    MIN_YEAR_RECORD_MONTHS,
    MONTHS_GENITIVE,
    MONTHS_NOMINATIVE,
    format_signed,
)
from meteoregik.reports.precipitation_data import (
    WET_DAY_MM,
    DecadeSum,
    DryRun,
    MonthSum,
    PrecipitationData,
    YearSum,
    describe_percent,
    format_mm,
)
from meteoregik.ui.dashboard_precip_charts import (
    HEATMAP_PERCENT,
    HEATMAP_SUM,
    AnnualCourseChart,
    AnnualSumChart,
    PrecipitationHeatmap,
    SeasonsChart,
)
from meteoregik.ui.dashboard_widgets import (
    CardText,
    GrooveDivider,
    StatCard,
    format_count,
    format_day,
    mode_switch,
    scrollable,
)
from meteoregik.ui.empty_state import hint_label
from meteoregik.ui.loading_overlay import LoadingOverlay

_NO_VALUE = "—"
# Сколько рекордов перечислять на карточке, дальше — "и ещё N"
MAX_LISTED_DAYS = 5
MAX_LISTED_RECORDS = 2
MAX_LISTED_RUNS = 2

_NORM_BASE = f"{NORM_BASE_FIRST_YEAR}–{NORM_BASE_LAST_YEAR}"

# Подсказки "?" описывают фактический расчёт (reports/precipitation_data.py,
# calculations/precipitation.py) — при изменении расчёта править и их.
_DAILY_SUM = (
    "Сумма осадков за сутки: для файлов ГГ — готовое значение из файла; для rp5 — "
    "сумма двух основных сроков (осадки там записаны суммами за 12 часов). «Осадков "
    "нет» и «Следы осадков» считаются нулём. Если за сутки об осадках не записано "
    "ничего, суммы за день нет — это пропуск, а не сухой день."
)
_COMPLETE_MONTH = (
    f"Месяц считается полным, если в нём пропущено не больше {MAX_MISSING_DAYS} дней и не больше "
    f"{MAX_MISSING_RUN} дней подряд (правило ВМО «3/5»)."
)
_COMPLETE_YEAR = "Год считается полным, если полны все 12 его месяцев. " + _COMPLETE_MONTH
_NORM_WHAT = (
    "Норма — обычное для станции количество осадков. По правилам Всемирной "
    f"метеорологической организации (ВМО) это средняя сумма за 30 лет, {_NORM_BASE}; "
    "учитываются только полные месяцы и годы."
)
_NORM_FALLBACK = (
    f"Если у станции за {_NORM_BASE} меньше {MIN_NORM_BASE_YEARS} полных лет, вместо нормы берётся "
    "средняя по всему архиву станции (в подписи — «средняя по архиву»)."
)
_WET_DAY = (
    f"День с осадками — сутки с суммой не меньше {WET_DAY_MM:.0f} мм (порог ВМО): меньшие "
    "количества — следы осадков и роса."
)

HELP_SUM = (
    "Сумма суточных сумм осадков за все дни периода, за которые есть данные. Если "
    "период не выбран, берётся весь архив станции. "
    + _DAILY_SUM
    + "\n\nСравнение с нормой — только по дням полных месяцев периода (не больше 5 "
    "пропущенных дней в месяце и не больше 3 подряд): для каждого такого дня берётся "
    "его доля месячной нормы (норма месяца, делённая на число дней в нём), доли "
    "складываются — это норма для выбранного периода, она указана в скобках. Неполные "
    "месяцы в сравнение не входят: в ранних выгрузках rp5 сухие дни часто не записаны, "
    "и такой месяц выглядел бы влажнее, чем был. Если в сравнение вошли не все дни "
    "периода с данными, это написано под процентом.\n\n"
    + _NORM_WHAT
    + "\n\n"
    + _NORM_FALLBACK
)
_YEAR_RECORD = (
    "Год с {which} годовой суммой осадков среди полных лет, целиком попавших в "
    "период. Если у нескольких лет сумма одинаковая, перечислены все. "
    + _COMPLETE_YEAR
    + "\n\nПроцент — от годовой нормы, её значение указано в скобках. "
    + _NORM_WHAT
    + "\n\n"
    + _NORM_FALLBACK
)
_MONTH_RECORD = (
    "Месяц с {which} суммой осадков среди полных месяцев, целиком попавших в "
    "период. Если у нескольких месяцев сумма одинаковая, перечислены все. "
    + _COMPLETE_MONTH
    + "\n\nПроцент — от нормы этого месяца (июль сравнивается с нормой июля), её "
    "значение указано в скобках. "
    + _NORM_WHAT
    + "\n\n"
    + _NORM_FALLBACK
)
_DECADE_RECORD = (
    f"Выбранный период короче {MIN_YEAR_RECORD_MONTHS} месяцев — полных лет в нём не больше "
    "одного, поэтому вместо самого {plain} года показывается самая {adjective} "
    "декада. Декады — 1–10, 11–20 и 21-е — последнее число месяца. Выбирается "
    "декада с {which} суммой осадков среди полных декад, целиком попавших в "
    "период; если у нескольких декад сумма одинаковая, перечислены все. "
    f"Декада считается полной, если в ней пропущено не больше {MAX_MISSING_DECADE_DAYS} дней."
    "\n\nПроцент — от нормы той же декады того же месяца (2-я декада июля "
    "сравнивается с нормой 2-й декады июля), её значение указано в скобках. "
    + _NORM_WHAT
    + "\n\n"
    + _NORM_FALLBACK
)
HELP_WETTEST_DECADE = _DECADE_RECORD.format(plain="влажного", adjective="влажная", which="наибольшей")
HELP_DRIEST_DECADE = _DECADE_RECORD.format(plain="сухого", adjective="сухая", which="наименьшей")
HELP_WETTEST_YEAR = _YEAR_RECORD.format(which="наибольшей")
HELP_DRIEST_YEAR = _YEAR_RECORD.format(which="наименьшей")
HELP_WETTEST_MONTH = _MONTH_RECORD.format(which="наибольшей")
HELP_DRIEST_MONTH = _MONTH_RECORD.format(which="наименьшей")
HELP_DAILY_MAX = (
    "Наибольшая сумма осадков за одни сутки в выбранном периоде и даты, когда она "
    "наблюдалась. " + _DAILY_SUM
)
HELP_WET_DAYS = (
    "Сколько в среднем дней с осадками приходится на год: число таких дней в каждом "
    "полном году периода, усреднённое по этим годам. " + _WET_DAY + " " + _COMPLETE_YEAR
    + f"\n\nЕсли период короче {MIN_YEAR_RECORD_MONTHS} месяцев, показано число дней с осадками за "
    "весь период, без усреднения по годам."
)
HELP_DRY_RUN = (
    f"Самый длинный ряд идущих подряд дней, в каждый из которых выпало меньше {WET_DAY_MM:.0f} мм. "
    "День без данных ряд обрывает: что выпало в этот день, неизвестно. Если таких "
    "рядов одинаковой длины несколько, перечислены все."
)
HELP_COMPLETENESS = (
    "Доля календарных дней периода, за которые есть суточная сумма осадков: дни с "
    "данными / все дни периода × 100 %.\n\n"
    "Пропущенный день — день, за который об осадках не записано ничего (пустое поле "
    "значит, что осадки не измеряли). Такой день в суммы не входит.\n\n"
    "Пропущенный срок — день по данным rp5, в который записан только один из двух "
    "основных сроков наблюдения. День пропущенным не считается, но его сумма посчитана "
    "по одному сроку и может быть занижена. Для файлов ГГ сроков нет: там готовые "
    "суточные суммы."
)


def _join_limited(items: list[str], sep: str, rest_unit: str, limit: int) -> str:
    shown = sep.join(items[:limit])
    rest = len(items) - limit
    return f"{shown} и ещё {rest} {rest_unit}" if rest > 0 else shown


def _capitalized(text: str) -> str:
    return text[:1].upper() + text[1:]


def _norm_name(data: PrecipitationData, month: int | None = None) -> str:
    """С чем сравнивали — в родительном падеже; откат на архив не скрывается."""
    if data.norm_standard:
        return f"нормы {MONTHS_GENITIVE[month - 1]}" if month else f"нормы {_NORM_BASE}"
    return f"средней {MONTHS_GENITIVE[month - 1]} по архиву" if month else "средней по архиву"


def _compared(value: float, norm: float | None, percent: float | None, norm_name: str) -> str:
    """"112 % нормы 1991–2020 (+68.0 мм; 567.0 мм)" — доля, разница и сама норма."""
    if norm is None:
        return "норма не рассчитана"
    if percent is None:
        return f"{norm_name}: {format_mm(norm)}"
    return f"{describe_percent(percent, norm_name)} ({format_signed(value - norm)} мм; {format_mm(norm)})"


def _period_compared(data: PrecipitationData) -> str:
    """Сравнение суммы периода с нормой — по дням полных месяцев (см.
    reports/precipitation_data.py); если вошли не все дни, это сказано."""
    if not data.month_norms:
        return "норма не рассчитана"
    if data.compared_sum is None:
        return "нет полных месяцев — с нормой не сравнить"
    text = _compared(data.compared_sum, data.period_norm, data.period_percent, _norm_name(data))
    if data.compared_days < data.days_with_data:
        text += (
            f"\nсравнение — по полным месяцам: {format_mm(data.compared_sum)} "
            f"за {format_count(data.compared_days)} дн."
        )
    return text


def _decade_norm_name(data: PrecipitationData, record: DecadeSum) -> str:
    """"нормы 2-й декады июля" / "средней 2-й декады июля по архиву"."""
    name = f"{record.number}-й декады {MONTHS_GENITIVE[record.start.month - 1]}"
    return f"нормы {name}" if data.norm_standard else f"средней {name} по архиву"


def _run_text(run: DryRun) -> str:
    return f"{format_day(run.start)}–{format_day(run.end)}"


def card_texts(data: PrecipitationData) -> list[CardText]:
    """Тексты девяти карточек по порядку: сумма, влажный/сухой год (для
    периода короче 24 месяцев — декада), влажный/сухой месяц; суточный максимум, дни с осадками, период без
    осадков, полнота. Отдельно от виджетов — проверяется тестами без раскладки."""
    caption = "Сумма осадков за период"
    if data.period_sum is None:
        total = CardText(caption, _NO_VALUE, "нет суточных сумм в периоде")
    else:
        total = CardText(caption, format_mm(data.period_sum), _period_compared(data))

    def year(caption: str, records: tuple[YearSum, ...]) -> CardText:
        if not records:
            return CardText(caption, _NO_VALUE, "нет полных лет в периоде")
        first = records[0]  # у всех рекордсменов сумма одна, а значит и доля нормы
        note = f"{format_mm(first.value)}, {_compared(first.value, first.norm, first.percent, _norm_name(data))}"
        return CardText(caption, ", ".join(str(r.year) for r in records), note)

    def month(caption: str, records: tuple[MonthSum, ...]) -> CardText:
        if not records:
            return CardText(caption, _NO_VALUE, "нет полных месяцев в периоде")
        names = [f"{MONTHS_NOMINATIVE[r.month - 1]} {r.year}" for r in records]
        limit = len(names) if len(names) <= MAX_LISTED_RECORDS else 1
        # сумма у всех одна; доля нормы — своя у каждого календарного месяца
        compared = list(dict.fromkeys(
            _compared(r.value, r.norm, r.percent, _norm_name(data, r.month)) for r in records
        ))
        note = f"{format_mm(records[0].value)}, {_join_limited(compared, '; ', 'мес.', 1)}"
        return CardText(caption, _capitalized(_join_limited(names, ", ", "мес.", limit)), note)

    def decade(caption: str, records: tuple[DecadeSum, ...]) -> CardText:
        if not records:
            return CardText(caption, _NO_VALUE, "нет полных декад в периоде")
        names = [f"{r.number}-я декада {MONTHS_GENITIVE[r.start.month - 1]} {r.start.year}" for r in records]
        limit = len(names) if len(names) <= MAX_LISTED_RECORDS else 1
        compared = list(dict.fromkeys(
            _compared(r.value, r.norm, r.percent, _decade_norm_name(data, r)) for r in records
        ))
        note = f"{format_mm(records[0].value)}, {_join_limited(compared, '; ', 'дек.', 1)}"
        return CardText(caption, _capitalized(_join_limited(names, ", ", "дек.", limit)), note)

    if data.short_period:
        wet_record = (decade("Самая влажная декада", data.wettest_decades), HELP_WETTEST_DECADE)
        dry_record = (decade("Самая сухая декада", data.driest_decades), HELP_DRIEST_DECADE)
    else:
        wet_record = (year("Самый влажный год", data.wettest_years), HELP_WETTEST_YEAR)
        dry_record = (year("Самый сухой год", data.driest_years), HELP_DRIEST_YEAR)

    if data.daily_max is None:
        daily_max = CardText("Суточный максимум", _NO_VALUE, "нет данных в периоде")
    else:
        daily_max = CardText(
            "Суточный максимум", format_mm(data.daily_max.value),
            _join_limited([format_day(d) for d in data.daily_max.days], ", ", "дн.", MAX_LISTED_DAYS),
        )

    wet_caption = f"Дней с осадками ≥ {WET_DAY_MM:.0f} мм в году"
    if data.short_period and data.days_with_data:
        # В коротком периоде полных лет не больше одного — среднее "в году"
        # считать не по чему, показывается число дней за сам период
        wet = CardText(f"Дней с осадками ≥ {WET_DAY_MM:.0f} мм за период", f"{format_count(data.wet_days)} дн.",
                       f"из {format_count(data.days_with_data)} дн. с данными")
    elif data.wet_days_per_year is not None:
        wet = CardText(
            wet_caption, f"{data.wet_days_per_year:.0f} дн.",
            f"в среднем за {data.wet_days_years} полн. лет; всего в периоде {format_count(data.wet_days)} дн.",
        )
    elif data.days_with_data:
        wet = CardText(wet_caption, _NO_VALUE,
                       f"нет полных лет в периоде; всего в периоде {format_count(data.wet_days)} дн.")
    else:
        wet = CardText(wet_caption, _NO_VALUE, "нет данных в периоде")

    if data.longest_dry_runs:
        runs = data.longest_dry_runs
        dry = CardText(
            "Самый длинный период без осадков", f"{runs[0].days} дн.",
            _join_limited([_run_text(run) for run in runs], ", ", "пер.", MAX_LISTED_RUNS),
        )
    else:
        dry = CardText("Самый длинный период без осадков", _NO_VALUE,
                       "нет данных в периоде" if not data.days_with_data else "нет дней без осадков в периоде")

    missing = data.days_total - data.days_with_data
    share = data.days_with_data / data.days_total * 100 if data.days_total else 0.0
    completeness = CardText(
        "Полнота данных об осадках",
        f"{share:.1f} %",
        f"{format_count(data.days_with_data)} из {format_count(data.days_total)} дн."
        + (f", пропущено {format_count(missing)} дн." if missing else ", без пропущенных дней")
        + "\n"
        + (f"Пропущен срок наблюдения: {format_count(data.missed_term_days)} дн. (сумма может быть занижена)"
           if data.missed_term_days else "Пропущенных сроков наблюдения нет"),
    )

    cards = [
        (total, HELP_SUM),
        wet_record,
        dry_record,
        (month("Самый влажный месяц", data.wettest_months), HELP_WETTEST_MONTH),
        (month("Самый сухой месяц", data.driest_months), HELP_DRIEST_MONTH),
        (daily_max, HELP_DAILY_MAX),
        (wet, HELP_WET_DAYS),
        (dry, HELP_DRY_RUN),
        (completeness, HELP_COMPLETENESS),
    ]
    return [replace(text, help=help_text) for text, help_text in cards]


def annual_title(data: PrecipitationData) -> str:
    """Короткий период — график по месяцам (см. AnnualSumChart)."""
    kind = "Месячная" if data.short_period else "Годовая"
    return f"{kind} сумма осадков относительно нормы"


def annual_note(data: PrecipitationData) -> str:
    """Пояснение под заголовком графика годовых сумм — от чего отсчитано."""
    if data.short_period:
        base = (
            f"нормы этого месяца за {_NORM_BASE}" if data.norm_standard
            else f"средней этого месяца по архиву станции (за {_NORM_BASE} меньше "
            f"{MIN_NORM_BASE_YEARS} полных лет)"
        )
        return (
            f"Период короче {MIN_YEAR_RECORD_MONTHS} месяцев, поэтому по месяцам: столбик — "
            f"отклонение суммы месяца от {base}."
        )
    norm = data.annual_norm
    if norm is None:
        return "Норма не рассчитана: у станции нет ни одного полного года."
    years = f"{norm.first_year}–{norm.last_year}" if norm.first_year != norm.last_year else str(norm.first_year)
    if norm.standard:
        text = (
            f"Норма {format_mm(norm.value, 0)} в год — средняя за {norm.year_count} полн. лет "
            f"базового периода ВМО {_NORM_BASE}. Показаны только полные годы."
        )
    else:
        text = (
            f"За {_NORM_BASE} меньше {MIN_NORM_BASE_YEARS} полных лет, поэтому нулевая линия — "
            f"средняя по архиву {format_mm(norm.value, 0)} в год за {norm.year_count} полн. лет ({years}). "
            "Показаны только полные годы."
        )
    if data.year_sums and data.trend is None:
        text += f" Линия тренда — от {MIN_TREND_YEARS} полных лет в периоде."
    return text


HEATMAP_MODES = (HEATMAP_PERCENT, HEATMAP_SUM)
_INCOMPLETE_MONTH_NOTE = (
    f"Штриховка — неполный месяц (пропущено больше {MAX_MISSING_DAYS} дней или больше "
    f"{MAX_MISSING_RUN} подряд), пустая клетка — нет данных."
)
_INCOMPLETE_DECADE_NOTE = (
    f"Штриховка — неполная декада (пропущено больше {MAX_MISSING_DECADE_DAYS} дней), "
    "пустая клетка — нет данных."
)


class PrecipitationTab(QWidget):
    def __init__(self, start_hint: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 10, 0, 0)
        layout.setSpacing(10)

        self._station_label = QLabel("")
        self._station_label.setStyleSheet("font-weight: 600; font-size: 14px;")
        layout.addWidget(self._station_label)

        self._stack = QStackedWidget()
        self._hint = hint_label(start_hint)
        self._stack.addWidget(self._hint)
        self._content = scrollable(self._build_content())
        self._stack.addWidget(self._content)

        results_area = QWidget()
        results_layout = QGridLayout(results_area)
        results_layout.setContentsMargins(0, 0, 0, 0)
        results_layout.addWidget(self._stack, 0, 0)
        self.loading_overlay = LoadingOverlay("Считаем показатели…")
        results_layout.addWidget(self.loading_overlay, 0, 0)
        layout.addWidget(results_area, stretch=1)

    # --- построение интерфейса ---

    def _build_content(self) -> QWidget:
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        cards = QGridLayout()
        cards.setHorizontalSpacing(10)
        cards.setVerticalSpacing(10)
        self.cards = [StatCard() for _ in range(9)]
        # Пять в верхнем ряду; в нижнем — четыре, полнота занимает две колонки:
        # в ней две строки о пропусках
        for index, card in enumerate(self.cards):
            row, col = divmod(index, 5)
            cards.addWidget(card, row, col, 1, 2 if index == 8 else 1)
        for col in range(5):
            cards.setColumnStretch(col, 1)
        layout.addLayout(cards)

        top = QHBoxLayout()
        top.setSpacing(16)
        self.heatmap = PrecipitationHeatmap()
        top.addLayout(self._heatmap_block(), stretch=1)
        top.addWidget(GrooveDivider())
        self.annual_chart = AnnualSumChart()
        self._annual_note = QLabel()
        self._annual_title = self._title()  # текст — в show_data(), по виду периода
        top.addLayout(
            self._chart_block(self._annual_title, self._annual_note, self.annual_chart),
            stretch=1,
        )
        layout.addLayout(top, stretch=1)

        bottom = QHBoxLayout()
        bottom.setSpacing(16)
        self.seasons_chart = SeasonsChart()
        seasons_note = QLabel(
            "Зима — декабрь прошлого года, январь и февраль; год сезона — тот, в котором он "
            "заканчивается. Штриховка — неполный сезон: в его месяцах есть пропуски."
        )
        bottom.addLayout(
            self._chart_block(self._title("Суммы осадков по сезонам"), seasons_note, self.seasons_chart), stretch=1
        )
        bottom.addWidget(GrooveDivider())
        self.course_chart = AnnualCourseChart()
        bottom.addLayout(self._course_block(), stretch=1)
        layout.addLayout(bottom, stretch=1)
        return content

    @staticmethod
    def _title(text: str = "") -> QLabel:
        label = QLabel(text)
        label.setObjectName("chartTitle")
        return label

    @staticmethod
    def _chart_block(title: QLabel | QHBoxLayout, note: QLabel, chart: QWidget) -> QVBoxLayout:
        block = QVBoxLayout()
        block.setSpacing(2)
        if isinstance(title, QLabel):
            block.addWidget(title)
        else:
            block.addLayout(title)
        note.setObjectName("chartNote")
        note.setWordWrap(True)
        block.addWidget(note)
        block.addWidget(chart, stretch=1)
        return block

    def _heatmap_block(self) -> QVBoxLayout:
        """Теплокарта с переключателем "% нормы / Сумма" в строке заголовка;
        переключение — сразу: данные уже загружены, меняется раскраска."""
        title_row = QHBoxLayout()
        title_row.setSpacing(12)
        self._heatmap_title = self._title()
        title_row.addWidget(self._heatmap_title)
        title_row.addStretch(1)
        mode_frame, self._heatmap_mode_group = mode_switch(["% нормы", "Сумма, мм"], self)
        self._heatmap_mode_group.idToggled.connect(self._on_heatmap_mode_toggled)
        title_row.addWidget(mode_frame)
        self._heatmap_note = QLabel()
        self._heatmap_short = False
        self._update_heatmap_texts()
        return self._chart_block(title_row, self._heatmap_note, self.heatmap)

    def _on_heatmap_mode_toggled(self, mode_id: int, checked: bool) -> None:
        if checked:
            self.heatmap.set_mode(HEATMAP_MODES[mode_id])
            self._update_heatmap_texts()

    def _update_heatmap_texts(self) -> None:
        unit, part = ("декадам", "декада") if self._heatmap_short else ("месяцам", "месяц")
        if self.heatmap.mode == HEATMAP_PERCENT:
            self._heatmap_title.setText(f"Осадки по {unit}, % нормы")
            color = f"Сине-зелёный — {part} влажнее своей нормы, охра — суше; шкала до 200 %. "
        else:
            self._heatmap_title.setText(f"Сумма осадков по {unit}")
            color = f"Чем темнее клетка, тем больше осадков за {'декаду' if self._heatmap_short else 'месяц'}. "
        self._heatmap_note.setText(color + (_INCOMPLETE_DECADE_NOTE if self._heatmap_short else _INCOMPLETE_MONTH_NOTE))

    def _course_block(self) -> QVBoxLayout:
        """Годовой ход с выбором года в строке заголовка."""
        title_row = QHBoxLayout()
        title_row.setSpacing(12)
        title_row.addWidget(self._title("Годовой ход осадков"))
        title_row.addStretch(1)
        year_label = QLabel("Год:")
        title_row.addWidget(year_label)
        self.year_combo = QComboBox()
        self.year_combo.setAccessibleName("Год для сравнения с нормой")
        year_label.setBuddy(self.year_combo)
        self.year_combo.currentIndexChanged.connect(self._on_year_changed)
        title_row.addWidget(self.year_combo)
        note = QLabel(
            "Столбики — нормы месяцев, линия — суммы месяцев выбранного года. Пустой кружок — "
            "неполный месяц, его сумма занижена."
        )
        return self._chart_block(title_row, note, self.course_chart)

    def _on_year_changed(self, index: int) -> None:
        self.course_chart.set_year(self.year_combo.itemData(index))

    # --- показ ---

    def set_loading(self, loading: bool) -> None:
        self.loading_overlay.setVisible(loading)
        if loading:
            self.loading_overlay.raise_()

    def show_data(self, title: str, data: PrecipitationData) -> None:
        self._station_label.setText(title)
        for card, text in zip(self.cards, card_texts(data)):
            card.set_text(text)
        self._annual_title.setText(annual_title(data))
        self._annual_note.setText(annual_note(data))
        self._heatmap_short = data.short_period
        self._update_heatmap_texts()
        self._stack.setCurrentWidget(self._content)
        self.heatmap.plot(data)
        self.annual_chart.plot(data)
        self.seasons_chart.plot(data)
        # Список годов — без сигналов: иначе каждый addItem перерисовывал бы график
        self.year_combo.blockSignals(True)
        self.year_combo.clear()
        for year in data.years:
            self.year_combo.addItem(str(year), year)
        # По умолчанию — последний полный год: последний год архива обычно
        # оборван на середине, и линия из двух точек ничего не показывает
        default = data.year_sums[-1].year if data.year_sums else (data.years[-1] if data.years else None)
        self.year_combo.setCurrentIndex(self.year_combo.findData(default))
        self.year_combo.blockSignals(False)
        self.course_chart.plot(data, default)

    def clear_results(self, message: str) -> None:
        """Убрать результаты прошлой выборки и показать причину, почему пусто."""
        self._station_label.setText("")
        self._hint.setText(message)
        self._stack.setCurrentWidget(self._hint)

    def set_hint(self, message: str) -> None:
        self._hint.setText(message)
