"""Экран "Импорт": выбор файла, явный выбор формата (rp5/ГГ), прогресс, отчёт
(ImportReport из importers/base.py) — станция, период, всего/импортировано/
пропущено строк.

После успешного импорта (imported_rows > 0) запускает
calculations/recompute.py для станции и периода, затронутых файлом — это и
есть "пересчёт при добавлении сырых строк через интерфейс" из доменных
правил (см. plan.md). Пересчёт — самая долгая часть (день за днём по всему
периоду файла), поэтому во время него показываются текст "Идёт пересчёт",
текущая обрабатываемая дата и полоса прогресса — recompute_range вызывает
progress_callback(day) на каждый обработанный день."""

from __future__ import annotations

import time

from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
)
from sqlalchemy.engine import Engine

from meteoregik.calculations.recompute import recompute_range
from meteoregik.importers.base import ImportReport
from meteoregik.importers.gg_importer import GgImporter
from meteoregik.importers.rp5_importer import Rp5Importer


def format_import_report(report: ImportReport) -> str:
    """Текст отчёта об импорте — вынесено из ImportDialog, чтобы тестировать
    без QApplication."""
    period = "—"
    if report.period_start is not None and report.period_end is not None:
        period = f"{report.period_start.isoformat()} — {report.period_end.isoformat()}"
    return (
        f"Станция: {report.station_name or '—'}\n"
        f"Период: {period}\n"
        f"Всего строк в файле: {report.total_rows}\n"
        f"Импортировано: {report.imported_rows}\n"
        f"Пропущено (уже существуют): {report.skipped_rows}"
    )


class ImportDialog(QDialog):
    def __init__(self, engine: Engine, parent=None) -> None:
        super().__init__(parent)
        self.engine = engine
        self.setWindowTitle("Импорт данных")
        self.resize(480, 340)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        file_row = QHBoxLayout()
        file_row.addWidget(QLabel("Файл:"))
        self.file_edit = QLineEdit()
        self.file_edit.setReadOnly(True)
        file_row.addWidget(self.file_edit, stretch=1)
        self.browse_btn = QPushButton("Обзор…")
        self.browse_btn.clicked.connect(self._browse_file)
        file_row.addWidget(self.browse_btn)
        layout.addLayout(file_row)

        format_row = QHBoxLayout()
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
        layout.addLayout(format_row)

        self.import_btn = QPushButton("Импортировать")
        self.import_btn.clicked.connect(self._run_import)
        layout.addWidget(self.import_btn)

        self.status_label = QLabel("")
        layout.addWidget(self.status_label)
        self.date_label = QLabel("")
        layout.addWidget(self.date_label)
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

        self.report_label = QLabel("")
        self.report_label.setWordWrap(True)
        layout.addWidget(self.report_label, stretch=1)

        self.close_btn = QPushButton("Закрыть")
        self.close_btn.clicked.connect(self.accept)
        layout.addWidget(self.close_btn)

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
        self.status_label.setText("Идёт импорт…")
        QApplication.processEvents()

        try:
            if self.rp5_radio.isChecked():
                importer = Rp5Importer(self.engine)
            else:
                importer = GgImporter(self.engine, name_resolver=self._resolve_station_name)
            report = importer.import_file(file_path)

            if report.imported_rows > 0 and report.wmo_index is not None:
                self._recompute_with_progress(report)
        except Exception as exc:  # noqa: BLE001 — показываем пользователю любую ошибку импорта
            self.status_label.setText("")
            QMessageBox.critical(self, "Ошибка импорта", str(exc))
            return
        finally:
            self._set_controls_enabled(True)
            self.date_label.setText("")
            self.progress_bar.setVisible(False)

        self.status_label.setText("")
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
        )

    def _resolve_station_name(self, wmo_index: int) -> str:
        name, ok = QInputDialog.getText(
            self,
            "Новая станция",
            f"Станция с индексом ВМО {wmo_index} не найдена в справочнике.\nВведите название станции:",
        )
        if not ok or not name.strip():
            raise ValueError(f"Название станции для wmo_index={wmo_index} не введено — импорт отменён")
        return name.strip()
