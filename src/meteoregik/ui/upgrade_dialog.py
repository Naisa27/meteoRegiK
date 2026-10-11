"""Окно "Обновление базы данных" — первый запуск новой версии программы на
базе прошлой версии, в которой уже есть данные (решение пользователя
2026-10-11).

Новая версия может считать показатели, которых раньше не было (первый
случай — суммы осадков). По уже залитым данным их надо досчитать один раз;
делают это миграции базы (db/migrations.py). Молча это шло бы до появления
главного окна: несколько секунд программа выглядела бы незапустившейся, а
сбой расчёта — как "не открывается". Поэтому здесь:
- перед началом делается копия базы (рядом с ней, одна);
- окно прямо называет, какие показатели считаются, и показывает ход по
  станциям;
- при сбое — сообщение с причиной и путём к копии; база при этом не
  изменена (миграции идут одной транзакцией), программа закрывается.

Расчёт идёт в GUI-потоке с обновлением окна после каждой станции, как
пересчёт в ui/import_dialog.py: прервать его на середине нельзя, закрыть
окно тоже.
"""

from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog, QLabel, QMessageBox, QProgressBar, QVBoxLayout, QWidget
from sqlalchemy.engine import Engine

from meteoregik.db.migrations import backup_database, run_migrations
from meteoregik.stations.choices import load_directory_stations
from meteoregik.ui import theme

TITLE = "Обновление базы данных"
FAILED_TITLE = "Не удалось обновить базу данных"
# Окно не должно мелькнуть и исчезнуть: на небольшой базе расчёт занимает
# доли секунды, а прочитать, что произошло, пользователь должен успеть
MIN_VISIBLE_SECONDS = 2.5

UPGRADE_QSS = f"""
QLabel#upgradeTitle {{ color: {theme.TEXT_PRIMARY}; font-size: 16px; font-weight: 600; }}
QLabel#upgradeText {{ color: {theme.TEXT_PRIMARY}; font-size: 13px; }}
QLabel#upgradeStatus {{ color: {theme.TEXT_SECONDARY}; font-size: 12px; }}
"""


def upgrade_text(updates: list[str]) -> str:
    """Что происходит — вынесено из окна, чтобы тестировать без QApplication."""
    items = "\n".join(f"  •  {update}" for update in updates)
    return (
        "Программа обновлена. Идёт расчёт новых показателей по уже загруженным данным:\n\n"
        f"{items}\n\n"
        "Это делается один раз. Пожалуйста, не закрывайте программу."
    )


def failure_text(error: BaseException, backup: Path | None) -> str:
    text = (
        "Новые показатели не рассчитаны, программа будет закрыта. База данных не изменена — "
        "при следующем запуске расчёт начнётся заново.\n\n"
        f"Причина: {error}"
    )
    if backup is not None:
        text += f"\n\nКопия базы, сделанная перед обновлением:\n{backup}"
    return text


class UpgradeDialog(QDialog):
    def __init__(self, updates: list[str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # Без крестика и системного меню: закрыть окно во время расчёта нельзя
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.CustomizeWindowHint | Qt.WindowType.WindowTitleHint)
        self.setWindowTitle(TITLE)
        self._finished = False
        self.setStyleSheet(theme.DIALOG_QSS + UPGRADE_QSS)
        self.setMinimumWidth(520)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        title = QLabel(TITLE)
        title.setObjectName("upgradeTitle")
        layout.addWidget(title)

        self.text_label = QLabel(upgrade_text(updates))
        self.text_label.setObjectName("upgradeText")
        self.text_label.setWordWrap(True)
        layout.addWidget(self.text_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)  # до первой станции — "идёт работа" без процента
        layout.addWidget(self.progress_bar)

        self.status_label = QLabel("Подготовка…")
        self.status_label.setObjectName("upgradeStatus")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

    def show_station(self, number: int, total: int, label: str) -> None:
        self.progress_bar.setRange(0, total)
        self.progress_bar.setValue(number - 1)
        self.status_label.setText(f"Станция {number} из {total}: {label}")

    def show_done(self) -> None:
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(1)
        self.status_label.setText("Готово.")

    def reject(self) -> None:
        """Esc окно не закрывает — расчёт прервать нельзя."""

    def closeEvent(self, event) -> None:  # noqa: N802 — имя метода Qt
        if self.isVisible() and not self._finished:
            event.ignore()
            return
        super().closeEvent(event)

    def finish(self) -> None:
        self._finished = True
        # Не close(): он закрывает диалог через reject(), а тот здесь отключён
        self.done(QDialog.DialogCode.Accepted)


def run_upgrade(engine: Engine, db_path: Path, updates: list[str]) -> bool:
    """Обновить базу с окном хода расчёта. False — обновление не удалось
    (пользователю уже сказано почему), запускать программу дальше нельзя:
    код новой версии рассчитан на новую базу."""
    dialog = UpgradeDialog(updates)
    dialog.show()
    QApplication.processEvents()
    started = time.monotonic()

    backup: Path | None = None
    try:
        dialog.status_label.setText("Сохраняем копию базы данных…")
        QApplication.processEvents()
        try:
            backup = backup_database(db_path)
        except OSError:
            # Копия — страховка, а не условие: нет места или прав на запись
            # рядом с базой — обновляем без неё (база при сбое и так не меняется)
            backup = None

        labels = {choice.wmo_index: choice.label for choice in load_directory_stations(engine)}

        def on_progress(number: int, total: int, wmo_index: int) -> None:
            dialog.show_station(number, total, labels.get(wmo_index, str(wmo_index)))
            QApplication.processEvents()

        run_migrations(engine, progress=on_progress)
    except Exception as exc:  # noqa: BLE001 — любая причина сбоя показывается пользователю
        dialog.finish()
        QMessageBox.critical(None, FAILED_TITLE, failure_text(exc, backup))
        return False

    dialog.show_done()
    while time.monotonic() - started < MIN_VISIBLE_SECONDS:
        QApplication.processEvents()
        time.sleep(0.02)
    dialog.finish()
    return True
