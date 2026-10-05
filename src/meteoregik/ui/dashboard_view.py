"""Экран "Дашборд", этап 1 (решение пользователя 2026-10-01, plan.md, "Экраны
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
from typing import Callable

from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtGui import QFocusEvent, QIcon
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QToolButton,
    QToolTip,
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
    MonthRecord,
    YearAnomaly,
    build_dashboard,
    describe_anomaly,
    format_temperature,
    station_day_bounds,
)
from meteoregik.resources import DASHBOARD_ICON_PATH
from meteoregik.stations.choices import load_stations_with_computed_indicators
from meteoregik.ui import theme
from meteoregik.ui.background import run_in_background
from meteoregik.ui.buttons import reserve_focus_width
from meteoregik.ui.dashboard_charts import AnomalyChart, TemperatureHeatmap
from meteoregik.ui.date_field import DateField, DateFieldError
from meteoregik.ui.dialogs import enable_min_max_buttons, minimize_owner_instead
from meteoregik.ui.loading_overlay import LoadingOverlay
from meteoregik.ui.station_combo import StationCombo

_TITLE = "Дашборд"
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
QLabel#chartTitle {{ color: {theme.TEXT_PRIMARY}; font-size: 14px; font-weight: 600; }}
QLabel#chartNote {{ color: {theme.TEXT_SECONDARY}; font-size: 12px; }}
QLabel#emptyHint {{ color: {theme.TEXT_SECONDARY}; font-size: 14px; }}
QToolButton#helpBadge {{
    color: {theme.TEXT_SECONDARY};
    background: {theme.INPUT_BG};
    border: 1px solid {theme.TEXT_SECONDARY};
    border-radius: 10px;
    font-size: 12px;
    font-weight: 700;
    min-width: 18px; max-width: 18px;
    min-height: 18px; max-height: 18px;
    padding: 0;
}}
QToolButton#helpBadge:hover {{ background: {theme.HEADER_BG}; color: {theme.TEXT_PRIMARY}; }}
QToolButton#helpBadge:focus {{
    border: {theme.FOCUS_RING_WIDTH}px solid {theme.FOCUS_RING};
    color: {theme.TEXT_PRIMARY};
}}
QToolTip {{
    color: {theme.TEXT_PRIMARY};
    background: {theme.INPUT_BG};
    border: 1px solid {theme.BORDER};
    padding: 6px;
    font-size: 12px;
}}
"""

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
# Сколько дат рекорда перечислять на карточке абс. максимума/минимума
MAX_LISTED_DAYS = 5

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
    "период. Среднегодовая — среднее суточных средних за год. "
    + _COMPLETE_YEAR
    + "\n\nОтклонение — от годовой нормы. "
    + _NORM_WHAT
    + "\n\n"
    + _NORM_FALLBACK
)
_MONTH_RECORD = (
    "Месяц с {which} средней температурой среди полных месяцев, целиком попавших в "
    "период. Средняя месяца — среднее суточных средних за месяц. "
    + _COMPLETE_MONTH
    + "\n\nОтклонение — от нормы этого месяца (июль сравнивается с нормой июля). "
    + _NORM_WHAT
    + "\n\n"
    + _NORM_FALLBACK
)
HELP_WARMEST_YEAR = _YEAR_RECORD.format(which="наибольшей")
HELP_COLDEST_YEAR = _YEAR_RECORD.format(which="наименьшей")
HELP_WARMEST_MONTH = _MONTH_RECORD.format(which="наибольшей")
HELP_COLDEST_MONTH = _MONTH_RECORD.format(which="наименьшей")


def _format_day(day: date) -> str:
    return f"{day:%d.%m.%Y}"


def _format_days(days: tuple[date, ...]) -> str:
    """Даты рекорда через запятую. Больше MAX_LISTED_DAYS — первые и "и ещё N
    дн.": иначе на длинном архиве карточка могла бы вытянуться на пол-экрана
    и растянуть весь ряд карточек."""
    shown = ", ".join(_format_day(d) for d in days[:MAX_LISTED_DAYS])
    rest = len(days) - MAX_LISTED_DAYS
    return f"{shown} и ещё {rest} дн." if rest > 0 else shown


def _format_count(value: int) -> str:
    """Тысячи через неразрывный пробел — "3 652", как принято в русском тексте."""
    return f"{value:,}".replace(",", " ")


@dataclass(frozen=True)
class CardText:
    caption: str
    value: str
    note: str
    help: str = ""


def _norm_name(data: DashboardData, month: int | None = None) -> str:
    """С чем сравнивали — в родительном падеже для describe_anomaly. При
    откате на весь архив это не норма ВМО, и подпись этого не скрывает."""
    if data.norm_standard:
        return f"нормы {MONTHS_GENITIVE[month - 1]}" if month else f"нормы {_NORM_BASE}"
    return f"средней {MONTHS_GENITIVE[month - 1]} по архиву" if month else "средней по архиву"


def card_texts(data: DashboardData) -> list[CardText]:
    """Тексты восьми карточек по порядку: средняя, максимум, минимум,
    полнота; тёплый/холодный год, тёплый/холодный месяц. Отдельно от
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

    def year(caption: str, record: YearAnomaly | None) -> CardText:
        if record is None:
            return CardText(caption, _NO_VALUE, "нет полных лет в периоде")
        return CardText(caption, str(record.year),
                        f"{format_temperature(record.value)}, {describe_anomaly(record.anomaly, _norm_name(data))}")

    def month(caption: str, record: MonthRecord | None) -> CardText:
        if record is None:
            return CardText(caption, _NO_VALUE, "нет полных месяцев в периоде")
        note = format_temperature(record.value)
        if record.anomaly is not None:
            note += ", " + describe_anomaly(record.anomaly, _norm_name(data, record.month))
        return CardText(caption, f"{MONTHS_NOMINATIVE[record.month - 1].capitalize()} {record.year}", note)

    cards = [
        (mean, HELP_MEAN),
        (extreme("Абсолютный максимум за период", data.abs_max), HELP_ABS_MAX),
        (extreme("Абсолютный минимум за период", data.abs_min), HELP_ABS_MIN),
        (completeness, HELP_COMPLETENESS),
        (year("Самый тёплый год", data.warmest_year), HELP_WARMEST_YEAR),
        (year("Самый холодный год", data.coldest_year), HELP_COLDEST_YEAR),
        (month("Самый тёплый месяц", data.warmest_month), HELP_WARMEST_MONTH),
        (month("Самый холодный месяц", data.coldest_month), HELP_COLDEST_MONTH),
    ]
    return [replace(text, help=help_text) for text, help_text in cards]


def anomaly_note(data: DashboardData) -> str:
    """Пояснение под заголовком графика аномалий — от чего отсчитано."""
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


def tooltip_html(text: str) -> str:
    """Подсказка как rich text фиксированной ширины — такую Qt переносит по
    словам, а не тянет одной строкой во весь экран; абзацы — по пустой строке."""
    paragraphs = "".join(f"<p style='margin: 0 0 6px 0'>{p}</p>" for p in text.split("\n\n"))
    return f"<div style='width: 340px'>{paragraphs}</div>"


class _HelpBadge(QToolButton):
    """Значок "?" с подсказкой. Кроме наведения мыши подсказка показывается
    по фокусу с клавиатуры (Tab) и по щелчку/пробелу — подсказка только по
    наведению недоступна без мыши."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("helpBadge")
        self.setText("?")
        self.setCursor(Qt.CursorShape.WhatsThisCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.clicked.connect(self.show_tip)

    def set_help(self, caption: str, text: str) -> None:
        self.setToolTip(tooltip_html(text) if text else "")
        self.setAccessibleName(f"Как считается: {caption}")
        self.setAccessibleDescription(text)

    def show_tip(self) -> None:
        if self.toolTip():
            QToolTip.showText(self.mapToGlobal(QPoint(0, self.height() + 4)), self.toolTip(), self)

    def focusInEvent(self, event: QFocusEvent) -> None:
        super().focusInEvent(event)
        if event.reason() in (Qt.FocusReason.TabFocusReason, Qt.FocusReason.BacktabFocusReason):
            self.show_tip()

    def focusOutEvent(self, event: QFocusEvent) -> None:
        super().focusOutEvent(event)
        QToolTip.hideText()


class _StatCard(QFrame):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("statCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 10, 10)
        layout.setSpacing(2)
        self.caption = QLabel()
        self.caption.setObjectName("statCaption")
        # Длинная подпись ("Средняя суточная температура за период") в узком
        # окне переносится, а не обрезается.
        self.caption.setWordWrap(True)
        self.help_badge = _HelpBadge()
        self.value = QLabel()
        self.value.setObjectName("statValue")
        self.note = QLabel()
        self.note.setObjectName("statNote")
        self.note.setWordWrap(True)
        for label in (self.caption, self.value, self.note):
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        caption_row = QHBoxLayout()
        caption_row.setSpacing(6)
        caption_row.addWidget(self.caption, stretch=1)
        caption_row.addWidget(self.help_badge, alignment=Qt.AlignmentFlag.AlignTop)
        layout.addLayout(caption_row)
        layout.addWidget(self.value)
        layout.addWidget(self.note)
        layout.addStretch(1)

    def set_text(self, text: CardText) -> None:
        self.caption.setText(text.caption)
        self.value.setText(text.value)
        self.note.setText(text.note)
        self.help_badge.set_help(text.caption, text.help)


@dataclass(frozen=True)
class _AppliedFilter:
    wmo_index: int
    date_from: date
    date_to: date
    station_title: str


class DashboardDialog(QDialog):
    def __init__(self, engine: Engine, parent=None, run_async: bool = True) -> None:
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
        self.station_combo.set_choices(load_stations_with_computed_indicators(self.engine))

    # --- построение интерфейса ---

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)
        layout.addWidget(self._build_filter_card())

        self._station_label = QLabel("")
        self._station_label.setStyleSheet("font-weight: 600; font-size: 14px;")
        layout.addWidget(self._station_label)

        # До первого "Применить" — подсказка, что делать, а не пустые карточки.
        self._stack = QStackedWidget()
        hint = QLabel(
            "Выберите метеостанцию и нажмите «Применить».\n"
            "Если период не указан, берётся весь архив станции."
        )
        hint.setObjectName("emptyHint")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._stack.addWidget(hint)
        self._content = self._build_content()
        self._stack.addWidget(self._content)

        results_area = QWidget()
        results_layout = QGridLayout(results_area)
        results_layout.setContentsMargins(0, 0, 0, 0)
        results_layout.addWidget(self._stack, 0, 0)
        self.loading_overlay = LoadingOverlay("Считаем показатели…")
        results_layout.addWidget(self.loading_overlay, 0, 0)
        layout.addWidget(results_area, stretch=1)

        close_row = QHBoxLayout()
        close_row.addStretch(1)
        close_btn = QPushButton("Закрыть")
        close_btn.setObjectName("secondary")
        close_btn.clicked.connect(self.accept)
        close_row.addWidget(close_btn)
        layout.addLayout(close_row)
        reserve_focus_width(*self.findChildren(QPushButton))

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
        self.cards = [_StatCard() for _ in range(8)]
        for i, card in enumerate(self.cards):
            cards.addWidget(card, i // 4, i % 4)
        for col in range(4):
            cards.setColumnStretch(col, 1)
        layout.addLayout(cards)

        charts = QHBoxLayout()
        charts.setSpacing(16)
        self.heatmap = TemperatureHeatmap()
        charts.addLayout(
            self._chart_block(
                "Средняя температура по месяцам",
                "Штриховка — неполный месяц (пропущено больше 5 дней или больше 3 подряд), "
                "пустая клетка — нет данных.",
                self.heatmap,
            ),
            stretch=1,
        )
        self.anomaly_chart = AnomalyChart()
        self._anomaly_note = QLabel()
        charts.addLayout(
            self._chart_block("Отклонение среднегодовой температуры от нормы", self._anomaly_note,
                              self.anomaly_chart),
            stretch=1,
        )
        layout.addLayout(charts, stretch=1)
        return content

    @staticmethod
    def _chart_block(title: str, note: str | QLabel, chart: QWidget) -> QVBoxLayout:
        block = QVBoxLayout()
        block.setSpacing(2)
        title_label = QLabel(title)
        title_label.setObjectName("chartTitle")
        block.addWidget(title_label)
        note_label = note if isinstance(note, QLabel) else QLabel(note)
        note_label.setObjectName("chartNote")
        note_label.setWordWrap(True)
        block.addWidget(note_label)
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
            bounds = station_day_bounds(self.engine, wmo_index)
            if bounds is None:
                QMessageBox.information(self, _TITLE, "По этой станции ещё нет посчитанных данных.")
                return
            date_from, date_to = bounds

        self._load(_AppliedFilter(wmo_index, date_from, date_to, self.station_combo.currentText()))

    def _load(self, applied: _AppliedFilter) -> None:
        self._request_id += 1
        request_id = self._request_id
        self._set_loading(True)

        def task() -> DashboardData:
            return build_dashboard(self.engine, applied.wmo_index, applied.date_from, applied.date_to)

        def on_done(data: DashboardData) -> None:
            if request_id != self._request_id:
                return
            self._set_loading(False)
            self._show(applied, data)

        def on_error(exc: BaseException) -> None:
            if request_id != self._request_id:
                return
            self._set_loading(False)
            QMessageBox.warning(self, _TITLE, f"Не удалось загрузить данные: {exc}")

        self._run(task, on_done, on_error)

    def _run(
        self,
        task: Callable[[], DashboardData],
        on_done: Callable[[DashboardData], None],
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
        self.apply_btn.setEnabled(not loading)
        if loading:
            self.loading_overlay.raise_()

    def _show(self, applied: _AppliedFilter, data: DashboardData) -> None:
        if not data.has_data:
            QMessageBox.information(self, _TITLE, "За выбранный период данных не найдено.")
            return
        self._station_label.setText(
            f"{applied.station_title} — {_format_day(applied.date_from)}–{_format_day(applied.date_to)}"
        )
        for card, text in zip(self.cards, card_texts(data)):
            card.set_text(text)
        self._anomaly_note.setText(anomaly_note(data))
        self._stack.setCurrentWidget(self._content)
        self.heatmap.plot(data)
        self.anomaly_chart.plot(data)

    # --- свернуть окно ---

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        minimize_owner_instead(self, event)
