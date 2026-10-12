"""Окно "Отклонения от нормы" раздела "Расчёт показателей" (решение
пользователя 2026-10-10): какие станции рассчитаны, какие требуют расчёта,
и запуск расчёта — только помеченных или всех заново.

Сам расчёт и правила — calculations/anomalies.py. При импорте и правке
данных он не выполняется (станция только помечается), а дашборд и карта
досчитывают помеченные станции сами; это окно — способ посчитать всё
заранее и увидеть, по какой базе нормы посчитана каждая станция.

Параметров два — температура и осадки (корректировка 2026-10-11). Раньше в
окне нигде не было сказано, о чём расчёт; теперь вступление называет оба, а
база нормы каждого — в своём столбце под общей шапкой "Норма (база)":
полнота у них считается по-разному, и у одной станции температура может
иметь норму ВМО, а осадки — только "по архиву". Станция считается одной
кнопкой сразу по обоим параметрам, состояние у неё одно.

Расчёт идёт в GUI-потоке с обновлением полосы прогресса после каждой
станции, как пересчёт агрегатов в ui/import_dialog.py: станция считается
доли секунды, а прервать расчёт на середине должно быть нельзя.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy.engine import Engine

from meteoregik.calculations.anomalies import (
    NORM_BASE_FIRST_YEAR,
    NORM_BASE_LAST_YEAR,
    CalcStatus,
    NormBase,
    calculation_status,
    ensure_fresh,
)
from meteoregik.stations.choices import load_stations_with_computed_indicators
from meteoregik.ui import theme
from meteoregik.ui.buttons import reserve_focus_width
from meteoregik.ui.dashboard_source_tab import Column, _make_table, format_period, plural
from meteoregik.ui.dialogs import enable_min_max_buttons, minimize_owner_instead

_TITLE = "Отклонения от нормы"
_INTRO = (
    "Нормы температуры и осадков станции и отклонения от них хранятся в базе: "
    "у средней температуры — за декады, месяцы и годы, у суммы осадков — ещё и за сезоны. "
    "После импорта или правки данных станция помечается «требуется расчёт»: "
    "норма зависит от всего архива, поэтому станция пересчитывается целиком, сразу по обоим параметрам. "
    "Дашборд и карта досчитывают такие станции сами, когда их открывают."
)
GROUP_NORM = "Норма (база)"
NO_PRECIPITATION = "нет данных об осадках"
STATE_STALE = "требуется расчёт"
_NO_VALUE = "—"

CALC_QSS = f"""
QLabel#calcIntro {{ color: {theme.TEXT_SECONDARY}; font-size: 12px; }}
QLabel#calcSummary {{ color: {theme.TEXT_PRIMARY}; font-size: 14px; font-weight: 600; }}
QLabel#calcStatus {{ color: {theme.TEXT_SECONDARY}; font-size: 12px; }}
"""


@dataclass(frozen=True)
class _Row:
    label: str
    status: CalcStatus


def _years_text(count: int) -> str:
    return f"{count} {plural(count, 'год', 'года', 'лет')}"


def state_text(status: CalcStatus) -> str:
    if status.calculated_at is None:
        return STATE_STALE
    return f"рассчитано {status.calculated_at:%d.%m.%Y %H:%M}"


def base_text(base: NormBase) -> str:
    """По какой базе посчитана годовая норма параметра."""
    if not base.years:
        return "нет полных лет — годовой нормы нет"
    if base.standard:
        return f"ВМО {NORM_BASE_FIRST_YEAR}–{NORM_BASE_LAST_YEAR} ({_years_text(base.years)})"
    return f"по архиву {base.first_year}–{base.last_year} ({_years_text(base.years)})"


def norm_text(status: CalcStatus) -> str:
    """База нормы температуры станции."""
    return _NO_VALUE if status.stale else base_text(status.temperature)


def precipitation_norm_text(status: CalcStatus) -> str:
    """База нормы осадков станции."""
    if status.stale:
        return _NO_VALUE
    return base_text(status.precipitation) if status.precipitation is not None else NO_PRECIPITATION


def summary_text(statuses: list[CalcStatus]) -> str:
    if not statuses:
        return "Данные ещё не залиты — рассчитывать нечего."
    stale = sum(status.stale for status in statuses)
    if not stale:
        return f"Все станции рассчитаны ({len(statuses)})."
    return f"Требуют расчёта: {stale} из {len(statuses)}."


def _period_key(period: tuple[date, date]) -> int:
    return period[0].toordinal()


COLUMNS = [
    Column("Станция", lambda r: r.label, lambda r: r.label.casefold()),
    Column("Период данных", lambda r: format_period(r.status.period), lambda r: _period_key(r.status.period),
           tip="Первый и последний день с суточной температурой или суммой осадков"),
    Column("Состояние", lambda r: state_text(r.status),
           lambda r: r.status.calculated_at.timestamp() if r.status.calculated_at else 0.0,
           tip="Когда нормы и отклонения станции посчитаны в последний раз"),
    Column("Температура", lambda r: norm_text(r.status), lambda r: norm_text(r.status), group=GROUP_NORM,
           tip=(
               f"Стандартная норма ВМО — средняя за {NORM_BASE_FIRST_YEAR}–{NORM_BASE_LAST_YEAR}; "
               "если полных лет в этом периоде меньше 24 — средняя по всему архиву станции. "
               "Месяц полный, если пропущено не больше 5 дней и не больше 3 подряд"
           )),
    Column("Осадки", lambda r: precipitation_norm_text(r.status), lambda r: precipitation_norm_text(r.status),
           group=GROUP_NORM,
           tip=(
               f"Та же база, что у температуры: {NORM_BASE_FIRST_YEAR}–{NORM_BASE_LAST_YEAR} "
               "при 24 и более полных годах, иначе весь архив. Полнота месяца — по тому же правилу, "
               "но пропуски у осадков свои, поэтому полных лет может быть другое число"
           )),
]


class AnomalyCalcDialog(QDialog):
    def __init__(self, engine: Engine, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        enable_min_max_buttons(self)
        self.engine = engine
        self._statuses: list[CalcStatus] = []
        self.setWindowTitle(_TITLE)
        self.resize(1020, 540)
        self.setStyleSheet(theme.DIALOG_QSS + CALC_QSS)
        self._build_ui()
        self._reload()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(10)

        intro = QLabel(_INTRO)
        intro.setObjectName("calcIntro")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self._summary = QLabel("")
        self._summary.setObjectName("calcSummary")
        layout.addWidget(self._summary)

        self.table, self.model = _make_table(COLUMNS, self, stretch_column=0)
        layout.addWidget(self.table, stretch=1)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)
        self._status = QLabel("")
        self._status.setObjectName("calcStatus")
        layout.addWidget(self._status)

        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        self.calc_btn = QPushButton("Рассчитать")
        self.calc_btn.setObjectName("primary")
        self.calc_btn.setToolTip("Только станции, которые требуют расчёта")
        self.calc_btn.clicked.connect(lambda: self._run(force=False))
        buttons.addWidget(self.calc_btn)
        self.recalc_all_btn = QPushButton("Пересчитать все")
        self.recalc_all_btn.setObjectName("secondary")
        self.recalc_all_btn.setToolTip("Все станции заново, включая уже рассчитанные")
        self.recalc_all_btn.clicked.connect(lambda: self._run(force=True))
        buttons.addWidget(self.recalc_all_btn)
        buttons.addStretch(1)
        self.close_btn = QPushButton("Закрыть")
        self.close_btn.setObjectName("secondary")
        self.close_btn.clicked.connect(self.accept)
        buttons.addWidget(self.close_btn)
        layout.addLayout(buttons)
        reserve_focus_width(self.calc_btn, self.recalc_all_btn, self.close_btn)

    def _reload(self) -> None:
        self._statuses = calculation_status(self.engine)
        labels = {choice.wmo_index: choice.label for choice in load_stations_with_computed_indicators(self.engine)}
        self.model.set_rows([_Row(labels.get(s.wmo_index, str(s.wmo_index)), s) for s in self._statuses])
        self._summary.setText(summary_text(self._statuses))
        self._set_controls_enabled(True)

    def _set_controls_enabled(self, enabled: bool) -> None:
        """Пока идёт расчёт, недоступны запуск и закрытие: прервать расчёт
        на середине нельзя. В обычном состоянии "Рассчитать" доступна,
        только если есть что считать."""
        self.calc_btn.setEnabled(enabled and any(status.stale for status in self._statuses))
        self.recalc_all_btn.setEnabled(enabled and bool(self._statuses))
        self.close_btn.setEnabled(enabled)

    def _run(self, force: bool) -> None:
        labels = {row.status.wmo_index: row.label for row in self.model.rows}
        self._set_controls_enabled(False)
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(True)

        def on_progress(number: int, total: int, wmo_index: int) -> None:
            self.progress_bar.setRange(0, total)
            self.progress_bar.setValue(number - 1)
            self._status.setText(f"Станция {number} из {total}: {labels.get(wmo_index, wmo_index)}")
            QApplication.processEvents()

        try:
            done = ensure_fresh(self.engine, progress=on_progress, force=force)
        except Exception as exc:  # noqa: BLE001 — показываем пользователю любую ошибку расчёта
            self._status.setText("")
            QMessageBox.critical(self, _TITLE, f"Расчёт не выполнен: {exc}")
            return
        finally:
            self.progress_bar.setVisible(False)
            self._reload()
        self._status.setText(
            f"Рассчитано станций: {len(done)}." if done else "Рассчитывать было нечего."
        )

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        minimize_owner_instead(self, event)

    def closeEvent(self, event) -> None:  # noqa: N802 — имя метода Qt
        if not self.close_btn.isEnabled():  # идёт расчёт
            event.ignore()
            return
        super().closeEvent(event)
