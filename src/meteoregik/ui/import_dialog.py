"""Экран "Импорт": выбор файла, явный выбор формата (rp5/ГГ), прогресс, отчёт
(ImportReport из importers/base.py) — станция, период, всего/импортировано/
пропущено строк.

После каждого импорта проверяет расчёты всех станций файла, автоматически
восстанавливает пропуски и незавершённую работу из устойчивой очереди.
Полные суточные наборы rp5 сравниваются до записи: более полные принимаются
автоматически, спорные требуют выбора пользователя. Это и
есть "пересчёт при добавлении сырых строк через интерфейс" из доменных
правил (см. plan.md). Пересчёт — самая долгая часть (день за днём по всему
периоду файла), поэтому во время него показываются текст "Идёт пересчёт",
текущая обрабатываемая дата и полоса прогресса — recompute_range вызывает
progress_callback(day) на каждый обработанный день.

Если пересчитанное значение расходится с уже сохранённым (например, rp5- и
ГГ-данные за одну дату дают разный максимум температуры), recompute_range
вызывает conflict_resolver — модальный диалог с выбором "оставить
сохранённое" / "взять новое" (и вариантами "...для всех оставшихся", чтобы
не переспрашивать на каждый день при массовом расхождении).

Если содержимое файла не соответствует выбранному формату (rp5/ГГ), импортёр
бросает ImportFormatError с понятным сообщением — обрабатывается отдельно от
прочих ошибок, с отдельным заголовком диалога (см. importers/base.py).

Если станции из файла нет в справочнике, импортёр бросает
StationNotFoundError (stations/matching.py) — файл не загружается, а
сообщение отправляет проверить индекс или добавить станцию в "Справочниках"
(решение пользователя 2026-10-11: импорт станции не создаёт).

Если станция rp5-файла в справочнике названа иначе, чем в файле, импортёр
спрашивает через _confirm_name_mismatch, загружать ли файл по индексу
(корректировка 2026-10-11). Отказ — StationNameMismatchError: файл не
загружен, второго окна об этом нет — пользователь только что сам так решил."""

from __future__ import annotations

import time
from datetime import date
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
)
from sqlalchemy.engine import Engine
from sqlalchemy import select

from meteoregik.calculations.pending import plan_recomputation
from meteoregik.calculations.precipitation import PARAMETER as PRECIPITATION
from meteoregik.calculations.recompute import ConflictChoice, ValueConflict, recompute_range
from meteoregik.importers.base import (
    DuplicateKeyConflictError, ImportFormatError, ImportReport, Rp5DayConflict, StationImportReport,
)
from meteoregik.db.schema import computed_indicators
from meteoregik.importers.gg_importer import GgImporter
from meteoregik.importers.rp5_importer import Rp5Importer
from meteoregik.stations.matching import StationNameMismatch, StationNameMismatchError, StationNotFoundError
from meteoregik.ui import theme
from meteoregik.ui.buttons import reserve_focus_width
from meteoregik.ui.dialogs import exec_window_modal

STATION_NOT_FOUND_TITLE = "Станции нет в справочнике"
NAME_MISMATCH_TITLE = "Название станции не совпадает"
LOAD_BY_INDEX_TEXT = "Загрузить по индексу"
DO_NOT_LOAD_TEXT = "Не загружать"
NOT_LOADED_STATUS = "Файл не загружен"
REPORT_LINES = 6  # минимальная видимая область отчёта, остальное прокручивается
DAY_CONFLICT_TITLE = "Изменение сроков наблюдений"
KEEP_OLD_DAY_TEXT = "Оставить старые"
USE_NEW_DAY_TEXT = "изменить на новые"

_PERIOD_TYPE_NAMES = {"day": "сутки", "decade": "декада", "month": "месяц", "year": "год"}
_STAT_TYPE_NAMES = {"avg": "средняя", "min": "минимальная", "max": "максимальная"}


def _indicator_name(conflict: ValueConflict) -> str:
    if conflict.parameter == PRECIPITATION:
        return "сумма осадков"
    stat = _STAT_TYPE_NAMES.get(conflict.stat_type, conflict.stat_type)
    return f"{stat} температура"


def format_conflict_message(conflict: ValueConflict) -> str:
    """Текст диалога расхождения значений — вынесено из ImportDialog, чтобы
    тестировать без QApplication."""
    period = _PERIOD_TYPE_NAMES.get(conflict.period_type, conflict.period_type)
    indicator = _indicator_name(conflict)
    old_source = conflict.old_source_file or "неизвестный файл"
    new_source = conflict.new_source_file or "неизвестный файл"
    period_range = (
        conflict.period_start.isoformat()
        if conflict.period_start == conflict.period_end
        else f"{conflict.period_start.isoformat()} — {conflict.period_end.isoformat()}"
    )
    return (
        f"Станция: ВМО {conflict.wmo_index}\n\n"
        f"Показатель «{indicator}», период: {period} ({period_range}).\n\n"
        f"Сохранено (из файла «{old_source}»): {conflict.old_value}\n"
        f"Рассчитано сейчас (из файла «{new_source}»): {conflict.new_value}\n\n"
        "Какое значение оставить?"
    )


def format_name_mismatch_question(mismatch: StationNameMismatch) -> str:
    """Текст вопроса о расхождении названия станции — вынесено из
    ImportDialog, чтобы тестировать без QApplication."""
    return (
        f"{mismatch.description}\n\n"
        f"Если это одна и та же станция, загрузите файл по индексу: данные будут записаны "
        f"станции «{mismatch.name_in_directory}».\n\n"
        "Если станции разные, не загружайте файл: исправьте в нём индекс или название и "
        "повторите импорт."
    )


def format_day_replacement_question(conflict: Rp5DayConflict) -> str:
    """Сравнение полных суточных наборов, а не отдельных пересекающихся ключей."""
    def times(values: tuple) -> str:
        return ", ".join(f"{value:%H:%M}" for value in values) or "нет"

    def stats(values: dict[tuple[str, str], float | None]) -> str:
        lines = []
        for key, caption in ((('T', 'avg'), "Средняя температура"),
                             (('T', 'min'), "Минимальная температура"),
                             (('T', 'max'), "Максимальная температура"),
                             (('R', 'sum'), "Сумма осадков")):
            value = values.get(key)
            number = "—" if value is None else (f"{value:.1f} мм" if key[0] == 'R' else f"{value:.2f} °C")
            lines.append(f"{caption}: {number}")
        return "\n".join(lines)

    def terms(count: int) -> str:
        ending = "сроков"
        if count % 10 == 1 and count % 100 != 11:
            ending = "срок"
        elif count % 10 in (2, 3, 4) and count % 100 not in (12, 13, 14):
            ending = "срока"
        return f"{count} {ending}"

    consequence = (
        "Агрегатные данные изменятся."
        if conflict.old_stats != conflict.new_stats else
        "Изменится набор исходных наблюдений; агрегаты будут пересчитаны."
    )

    return (
        f"Метеостанция: {conflict.station_name} (ВМО {conflict.wmo_index})\n"
        f"Дата: {conflict.obs_date:%d.%m.%Y}\n\n"
        f"По этой дате существует {terms(len(conflict.old_times))} "
        f"({times(conflict.old_times)}).\nАгрегатные значения сохранённого набора:\n{stats(conflict.old_stats)}\n\n"
        f"В новой загрузке {terms(len(conflict.new_times))} "
        f"({times(conflict.new_times)}).\nНовые агрегатные значения:\n{stats(conflict.new_stats)}\n\n"
        "Сроки посчитаны по заполненной температуре.\n"
        f"{consequence}\n"
        f"Оставить старые {terms(len(conflict.old_times))} или изменить их "
        f"на новые {terms(len(conflict.new_times))}?"
    )


def format_import_report(report: ImportReport) -> str:
    """Текст отчёта об импорте — вынесено из ImportDialog, чтобы тестировать
    без QApplication."""
    period = "—"
    if report.period_start is not None and report.period_end is not None:
        period = f"{report.period_start.isoformat()} — {report.period_end.isoformat()}"
    multiple = len(report.stations) > 1
    skipped = (
        f"Пропущено (уже существуют): {report.skipped_rows}"
        if report.duplicates_in_file == 0
        else f"Пропущено всего: {report.skipped_rows}"
    )
    lines = [
        f"Станций: {len(report.stations)}" if multiple else f"Станция: {report.station_name or '—'}",
        f"Период: {period}",
        f"Всего строк в файле: {report.total_rows}",
        f"Импортировано: {report.imported_rows}",
        skipped,
    ]
    if report.duplicates_in_file:
        lines.extend([
            f"Из них уже в базе: {report.skipped_rows - report.duplicates_in_file}",
            f"Одинаковые повторы внутри файла: {report.duplicates_in_file}",
        ])
    if report.rows_without_data is not None:
        lines.append(f"Строк без данных (не загружены): {report.rows_without_data}")
    if report.replaced_rows:
        lines.append(f"Заменено прежних строк: {report.replaced_rows}")
    if multiple:
        for station in report.stations:
            lines.extend([
                "",
                f"{station.station_name} (ВМО {station.wmo_index})",
                f"Период: {station.period_start.isoformat()} — {station.period_end.isoformat()}",
                f"Импортировано: {station.imported_rows}; пропущено: {station.skipped_rows}",
            ])
    return "\n".join(lines)


class ImportDialog(QDialog):
    def __init__(self, engine: Engine, parent=None) -> None:
        super().__init__(parent)
        self.engine = engine
        self.setWindowTitle("Импорт данных")
        self.resize(620, 620)
        self.setStyleSheet(theme.DIALOG_QSS)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

        subtitle = QLabel("Загрузите файл срочных наблюдений (rp5) или среднесуточных показателей (ГГ).")
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet(f"color: {theme.TEXT_SECONDARY};")
        layout.addWidget(subtitle)

        layout.addWidget(self._build_form_card())

        self.import_btn = QPushButton("Импортировать")
        self.import_btn.setObjectName("primary")
        self.import_btn.clicked.connect(self._run_import)
        layout.addWidget(self.import_btn)

        self.status_label = QLabel("")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.status_label)
        self.date_label = QLabel("")
        self.date_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.date_label.setStyleSheet(f"color: {theme.TEXT_SECONDARY};")
        layout.addWidget(self.date_label)
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

        self.success_label = QLabel("")
        self.success_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.success_label.setStyleSheet(f"color: {theme.SUCCESS}; font-weight: bold;")
        layout.addWidget(self.success_label)

        self.report_label = QLabel("")
        self.report_label.setTextFormat(Qt.TextFormat.PlainText)
        self.report_label.setWordWrap(True)
        self.report_label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.report_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        self.report_label.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.report_label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        self.report_area = QScrollArea()
        self.report_area.setFrameShape(QFrame.Shape.NoFrame)
        self.report_area.setWidgetResizable(True)
        self.report_area.setWidget(self.report_label)
        # setWidget включает заливку фона ребёнка; сохраняем фон общей темы.
        self.report_label.setAutoFillBackground(False)
        self.report_area.viewport().setAutoFillBackground(False)
        self.report_area.setAutoFillBackground(False)
        self.report_area.setMinimumHeight(self.report_label.fontMetrics().lineSpacing() * REPORT_LINES + 12)
        self.report_area.verticalScrollBar().setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        layout.addWidget(self.report_area, stretch=1)

        self.close_btn = QPushButton("Закрыть")
        self.close_btn.setObjectName("secondary")
        self.close_btn.clicked.connect(self.accept)
        close_row = QHBoxLayout()
        close_row.addStretch(1)
        close_row.addWidget(self.close_btn)
        layout.addLayout(close_row)

    def _build_form_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("card")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(16, 16, 16, 16)
        card_layout.setSpacing(12)

        file_row = QHBoxLayout()
        file_row.setSpacing(8)
        file_row.addWidget(QLabel("Файл:"))
        self.file_edit = QLineEdit()
        self.file_edit.setReadOnly(True)
        file_row.addWidget(self.file_edit, stretch=1)
        self.browse_btn = QPushButton("Обзор…")
        self.browse_btn.setObjectName("secondary")
        self.browse_btn.clicked.connect(self._browse_file)
        file_row.addWidget(self.browse_btn)
        card_layout.addLayout(file_row)

        format_row = QHBoxLayout()
        format_row.setSpacing(8)
        format_row.addWidget(QLabel("Формат:"))
        self.rp5_radio = QRadioButton("rp5 (срочные наблюдения)")
        self.gg_radio = QRadioButton("ГГ (среднесуточные)")
        self.rp5_radio.setChecked(True)
        self._format_group = QButtonGroup(self)
        self._format_group.addButton(self.rp5_radio)
        self._format_group.addButton(self.gg_radio)
        format_row.addWidget(self.rp5_radio)
        format_row.addWidget(self.gg_radio)
        format_row.addStretch(1)
        card_layout.addLayout(format_row)

        return card

    def _set_controls_enabled(self, enabled: bool) -> None:
        """Пока идёт импорт/пересчёт — недоступны выбор файла, запуск
        импорта и закрытие диалога (не должно быть возможности прервать
        операцию на середине)."""
        self.browse_btn.setEnabled(enabled)
        self.import_btn.setEnabled(enabled)
        self.close_btn.setEnabled(enabled)
        self.rp5_radio.setEnabled(enabled)
        self.gg_radio.setEnabled(enabled)

    def _browse_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Выберите файл для импорта", "", "Excel файлы (*.xls *.xlsx)"
        )
        if path:
            self.file_edit.setText(path)

    def _run_import(self) -> None:
        file_path = self.file_edit.text()
        if not file_path:
            QMessageBox.warning(self, "Импорт", "Сначала выберите файл")
            return

        self._set_controls_enabled(False)
        self.report_label.setText("")
        self.success_label.setText("")
        self.status_label.setText("Идёт импорт…")
        self._performed_recompute = False
        QApplication.processEvents()

        report: ImportReport | None = None
        try:
            if self.rp5_radio.isChecked():
                importer = Rp5Importer(
                    self.engine, confirm_name_mismatch=self._confirm_name_mismatch,
                    confirm_day_replacement=self._confirm_day_replacement,
                )
            else:
                importer = GgImporter(self.engine)
            report = importer.import_file(file_path)

            self._recompute_with_progress(report)
        except DuplicateKeyConflictError as exc:
            self.status_label.setText(NOT_LOADED_STATUS)
            QMessageBox.critical(self, "Противоречивые строки файла", str(exc))
            return
        except ImportFormatError as exc:
            self.status_label.setText("")
            QMessageBox.critical(self, "Неверный формат файла", str(exc))
            return
        except StationNotFoundError as exc:
            # Не ошибка программы, а то, что пользователь исправляет сам — предупреждение
            self.status_label.setText("")
            QMessageBox.warning(self, STATION_NOT_FOUND_TITLE, str(exc))
            return
        except StationNameMismatchError:
            # Отказ в _confirm_name_mismatch: окно с вопросом уже было, второе не нужно
            self.status_label.setText(NOT_LOADED_STATUS)
            return
        except Exception as exc:  # noqa: BLE001 — показываем пользователю любую ошибку импорта
            if report is None:
                self.status_label.setText("")
                QMessageBox.critical(self, "Ошибка импорта", str(exc))
            else:
                self.status_label.setText("Пересчёт не завершён")
                self.report_label.setText(format_import_report(report))
                QMessageBox.critical(
                    self, "Ошибка пересчёта",
                    "Сырые данные файла сохранены, но пересчёт агрегатов не завершён.\n\n"
                    f"{exc}\n\n"
                    "Повторите импорт: программа автоматически обнаружит и завершит незаконченный пересчёт.",
                )
            return
        finally:
            self._set_controls_enabled(True)
            self.date_label.setText("")
            self.progress_bar.setVisible(False)

        self.status_label.setText("")
        self.success_label.setText("Импорт завершён успешно")
        checked = (
            "Расчёты проверены и пересчитаны."
            if self._performed_recompute else
            "Расчёты проверены: пропусков и незавершённых расчётов не обнаружено."
        )
        self.report_label.setText(format_import_report(report) + "\n\n" + checked)

    def _confirm_name_mismatch(self, mismatch: StationNameMismatch) -> bool:
        """Загружать ли файл по индексу, если название станции в нём не то,
        что в справочнике. По умолчанию (Enter, Esc, закрытие окна) — не
        загружать: залитые не той станции данные исправлять дольше, чем
        повторить импорт."""
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(NAME_MISMATCH_TITLE)
        box.setText(format_name_mismatch_question(mismatch))
        load_btn = box.addButton(LOAD_BY_INDEX_TEXT, QMessageBox.ButtonRole.AcceptRole)
        cancel_btn = box.addButton(DO_NOT_LOAD_TEXT, QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(cancel_btn)
        box.setEscapeButton(cancel_btn)
        reserve_focus_width(load_btn, cancel_btn)  # жирный шрифт фокуса шире обычного
        box.exec()
        return box.clickedButton() is load_btn

    def _confirm_day_replacement(self, conflict: Rp5DayConflict) -> ConflictChoice:
        box = QMessageBox(self)
        box.setWindowTitle(DAY_CONFLICT_TITLE)
        box.setIcon(QMessageBox.Icon.Question)
        box.setTextFormat(Qt.TextFormat.PlainText)
        box.setText(format_day_replacement_question(conflict))
        old_btn = box.addButton(KEEP_OLD_DAY_TEXT, QMessageBox.ButtonRole.RejectRole)
        new_btn = box.addButton(USE_NEW_DAY_TEXT, QMessageBox.ButtonRole.AcceptRole)
        box.setDefaultButton(old_btn)
        box.setEscapeButton(old_btn)
        reserve_focus_width(old_btn, new_btn)
        exec_window_modal(box)
        return "new" if box.clickedButton() is new_btn else "old"

    def _recompute_with_progress(self, report: ImportReport) -> None:
        stations = report.stations
        # Совместимость отчётов старых вызывающих функций и тестовых адаптеров.
        if not stations and report.wmo_index is not None and report.period_start is not None and report.period_end is not None:
            stations = (StationImportReport(
                wmo_index=report.wmo_index, station_name=report.station_name or str(report.wmo_index),
                period_start=report.period_start, period_end=report.period_end,
                imported_rows=report.imported_rows, skipped_rows=report.skipped_rows,
                new_period_start=report.period_start if report.imported_rows else None,
                new_period_end=report.period_end if report.imported_rows else None,
            ),)
        self.status_label.setText("Проверяем полноту расчётов всех станций файла…")
        QApplication.processEvents()
        names = {station.wmo_index: station.station_name for station in stations}
        targets = list(plan_recomputation(self.engine, names))
        if not targets:
            return
        total_days = sum((item.end - item.start).days + 1 for item in targets)
        self.progress_bar.setRange(0, total_days)
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(True)

        # Обновление UI на каждый день дорого (processEvents на ~7500 дней
        # заметно замедляет сам пересчёт) — троттлим примерно до 10 раз/сек,
        # прогресс-бар всё равно долистывает до конца на последнем дне.
        last_ui_update = 0.0
        completed_days = 0
        # Выбор «для всех оставшихся» действует на весь файл, а не на одну станцию.
        with self.engine.connect() as conn:
            initial_periods = set(conn.execute(select(
                computed_indicators.c.wmo_index, computed_indicators.c.parameter,
                computed_indicators.c.stat_type, computed_indicators.c.period_type,
                computed_indicators.c.period_start, computed_indicators.c.period_end,
            ).where(
                computed_indicators.c.wmo_index.in_(names),
                computed_indicators.c.parameter.in_(("T", "R")),
                computed_indicators.c.stat_type.in_(("avg", "min", "max", "sum")),
                computed_indicators.c.period_type != "day",
            )))
        resolver = self._make_conflict_resolver(initial_periods)
        for index, target in enumerate(targets, 1):
            start, end = target.start, target.end
            self.status_label.setText(
                f"Идёт пересчёт агрегатов… {index} из {len(targets)}: "
                f"{names[target.wmo_index]} (ВМО {target.wmo_index})"
            )
            station_days = (end - start).days + 1

            def _on_progress(day: date) -> None:
                nonlocal last_ui_update
                day_index = (day - start).days + 1
                self.progress_bar.setValue(completed_days + day_index)
                now = time.monotonic()
                if now - last_ui_update > 0.1 or day_index == station_days:
                    self.date_label.setText(f"Дата: {day.isoformat()}")
                    QApplication.processEvents()
                    last_ui_update = now

            recompute_range(
                self.engine, target.wmo_index, start, end,
                progress_callback=_on_progress, conflict_resolver=resolver,
                periods_only=target.periods_only,
            )
            self._performed_recompute = True
            completed_days += station_days

    def _make_conflict_resolver(self, initial_periods: set | None = None) -> Callable[[ValueConflict], ConflictChoice]:
        """Резолвер конфликтов на одно выполнение _run_import: если
        пользователь выбрал "...для всех оставшихся", дальше отвечает без
        повторных диалогов."""
        remembered: dict[str, ConflictChoice] = {}

        def _resolve(conflict: ValueConflict) -> ConflictChoice:
            key = (conflict.wmo_index, conflict.parameter, conflict.stat_type, conflict.period_type,
                   conflict.period_start, conflict.period_end)
            if conflict.period_type != "day" and initial_periods is not None and key not in initial_periods:
                # Промежуточный агрегат создан этой же загрузкой, а не выбран
                # пользователем ранее: при следующем диапазоне его дополняем.
                return "new"
            if "choice" in remembered:
                return remembered["choice"]

            box = QMessageBox(self)
            box.setWindowTitle("Расхождение значений")
            box.setText(format_conflict_message(conflict))
            keep_old_btn = box.addButton("Оставить сохранённое", QMessageBox.ButtonRole.ActionRole)
            keep_new_btn = box.addButton("Взять новое", QMessageBox.ButtonRole.ActionRole)
            keep_old_all_btn = box.addButton(
                "Оставить сохранённое для всех оставшихся", QMessageBox.ButtonRole.ActionRole
            )
            keep_new_all_btn = box.addButton(
                "Взять новое для всех оставшихся", QMessageBox.ButtonRole.ActionRole
            )
            box.exec()
            clicked = box.clickedButton()

            if clicked is keep_old_all_btn:
                remembered["choice"] = "old"
                return "old"
            if clicked is keep_new_all_btn:
                remembered["choice"] = "new"
                return "new"
            if clicked is keep_new_btn:
                return "new"
            return "old"  # keep_old_btn или диалог закрыт без выбора

        return _resolve
