"""Экран "Импорт": выбор файла, явный выбор формата (rp5/ГГ), прогресс, отчёт
(ImportReport из importers/base.py) — станция, период, всего/импортировано/
пропущено строк.

После успешного импорта (imported_rows > 0) запускает
calculations/recompute.py для станции и периода, затронутых файлом — это и
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
(решение пользователя 2026-10-11: импорт станции не создаёт)."""

from __future__ import annotations

import time
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
    QVBoxLayout,
)
from sqlalchemy.engine import Engine

from meteoregik.calculations.recompute import ConflictChoice, ValueConflict, recompute_range
from meteoregik.importers.base import ImportFormatError, ImportReport
from meteoregik.importers.gg_importer import GgImporter
from meteoregik.importers.rp5_importer import Rp5Importer
from meteoregik.stations.matching import StationNotFoundError
from meteoregik.ui import theme

STATION_NOT_FOUND_TITLE = "Станции нет в справочнике"
REPORT_LINES = 6  # строк в тексте format_import_report (у ГГ; у rp5 на одну меньше)

_PERIOD_TYPE_NAMES = {"day": "сутки", "decade": "декада", "month": "месяц", "year": "год"}
_STAT_TYPE_NAMES = {"avg": "средняя", "min": "минимальная", "max": "максимальная"}


def format_conflict_message(conflict: ValueConflict) -> str:
    """Текст диалога расхождения значений — вынесено из ImportDialog, чтобы
    тестировать без QApplication."""
    period = _PERIOD_TYPE_NAMES.get(conflict.period_type, conflict.period_type)
    stat = _STAT_TYPE_NAMES.get(conflict.stat_type, conflict.stat_type)
    old_source = conflict.old_source_file or "неизвестный файл"
    new_source = conflict.new_source_file or "неизвестный файл"
    period_range = (
        conflict.period_start.isoformat()
        if conflict.period_start == conflict.period_end
        else f"{conflict.period_start.isoformat()} — {conflict.period_end.isoformat()}"
    )
    return (
        f"Показатель «{stat} температура», период: {period} ({period_range}).\n\n"
        f"Сохранено (из файла «{old_source}»): {conflict.old_value}\n"
        f"Рассчитано сейчас (из файла «{new_source}»): {conflict.new_value}\n\n"
        "Какое значение оставить?"
    )


def format_import_report(report: ImportReport) -> str:
    """Текст отчёта об импорте — вынесено из ImportDialog, чтобы тестировать
    без QApplication."""
    period = "—"
    if report.period_start is not None and report.period_end is not None:
        period = f"{report.period_start.isoformat()} — {report.period_end.isoformat()}"
    lines = [
        f"Станция: {report.station_name or '—'}",
        f"Период: {period}",
        f"Всего строк в файле: {report.total_rows}",
        f"Импортировано: {report.imported_rows}",
        f"Пропущено (уже существуют): {report.skipped_rows}",
    ]
    if report.rows_without_data is not None:
        lines.append(f"Строк без данных (не загружены): {report.rows_without_data}")
    return "\n".join(lines)


class ImportDialog(QDialog):
    def __init__(self, engine: Engine, parent=None) -> None:
        super().__init__(parent)
        self.engine = engine
        self.setWindowTitle("Импорт данных")
        self.resize(480, 520)
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
        self.report_label.setWordWrap(True)
        # место под все строки отчёта (сейчас их 6) — иначе последняя обрезается
        self.report_label.setMinimumHeight(self.report_label.fontMetrics().lineSpacing() * REPORT_LINES + 8)
        layout.addWidget(self.report_label, stretch=1)

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
        QApplication.processEvents()

        try:
            if self.rp5_radio.isChecked():
                importer = Rp5Importer(self.engine)
            else:
                importer = GgImporter(self.engine)
            report = importer.import_file(file_path)

            if report.imported_rows > 0 and report.wmo_index is not None:
                self._recompute_with_progress(report)
        except ImportFormatError as exc:
            self.status_label.setText("")
            QMessageBox.critical(self, "Неверный формат файла", str(exc))
            return
        except StationNotFoundError as exc:
            # Не ошибка программы, а то, что пользователь исправляет сам — предупреждение
            self.status_label.setText("")
            QMessageBox.warning(self, STATION_NOT_FOUND_TITLE, str(exc))
            return
        except Exception as exc:  # noqa: BLE001 — показываем пользователю любую ошибку импорта
            self.status_label.setText("")
            QMessageBox.critical(self, "Ошибка импорта", str(exc))
            return
        finally:
            self._set_controls_enabled(True)
            self.date_label.setText("")
            self.progress_bar.setVisible(False)

        self.status_label.setText("")
        self.success_label.setText("Импорт завершён успешно")
        self.report_label.setText(format_import_report(report))

    def _recompute_with_progress(self, report: ImportReport) -> None:
        self.status_label.setText("Идёт пересчёт агрегатов…")
        total_days = (report.period_end - report.period_start).days + 1
        self.progress_bar.setRange(0, total_days)
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(True)

        period_start = report.period_start
        # Обновление UI на каждый день дорого (processEvents на ~7500 дней
        # заметно замедляет сам пересчёт) — троттлим примерно до 10 раз/сек,
        # прогресс-бар всё равно долистывает до конца на последнем дне.
        last_ui_update = 0.0

        def _on_progress(day) -> None:
            nonlocal last_ui_update
            day_index = (day - period_start).days + 1
            self.progress_bar.setValue(day_index)
            now = time.monotonic()
            if now - last_ui_update > 0.1 or day_index == total_days:
                self.date_label.setText(f"Дата: {day.isoformat()}")
                QApplication.processEvents()
                last_ui_update = now

        recompute_range(
            self.engine, report.wmo_index, report.period_start, report.period_end,
            progress_callback=_on_progress,
            conflict_resolver=self._make_conflict_resolver(),
        )

    def _make_conflict_resolver(self) -> Callable[[ValueConflict], ConflictChoice]:
        """Резолвер конфликтов на одно выполнение _run_import: если
        пользователь выбрал "...для всех оставшихся", дальше отвечает без
        повторных диалогов."""
        remembered: dict[str, ConflictChoice] = {}

        def _resolve(conflict: ValueConflict) -> ConflictChoice:
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
