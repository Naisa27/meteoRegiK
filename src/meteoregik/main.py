"""Точка входа: uv run python -m meteoregik.main"""

from __future__ import annotations

import sys

from PySide6.QtCore import QCoreApplication, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from meteoregik.db.migrations import run_migrations
from meteoregik.db.seed import ensure_seed_database
from meteoregik.db.session import default_db_path, get_engine, local_data_dir
from meteoregik.resources import APP_ICON_PATH
from meteoregik.smoke import schedule as schedule_smoke_test
from meteoregik.smoke import smoke_dir
from meteoregik.ui import theme
from meteoregik.ui.main_window import MainWindow


def main() -> None:
    db_path = default_db_path()
    # Базу впервые создаём не в "Документах" (туда нельзя писать) — пользователю
    # надо один раз сказать, где она: иначе он её не найдёт
    moved_from_documents = (
        getattr(sys, "frozen", False) and not db_path.exists() and db_path.parent == local_data_dir()
    )
    ensure_seed_database(db_path)  # до создания Engine — иначе миграции создадут пустой файл раньше
    engine = get_engine(db_path)
    run_migrations(engine)

    # Нужно экрану "Карта" (QtWebEngine) и только до создания QApplication
    QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    app = QApplication(sys.argv)
    app.setWindowIcon(QIcon(str(APP_ICON_PATH)))
    app.setStyleSheet(theme.APP_QSS)  # единый стиль кнопок (в т.ч. реакция на нажатие)
    window = MainWindow(engine)
    window.center_on_screen()
    window.show()
    if moved_from_documents and smoke_dir() is None:
        QMessageBox.information(
            window,
            "Где хранятся данные",
            "Windows не разрешила программе писать в папку «Документы» "
            "(включена защита папок), поэтому база данных создана здесь:\n\n"
            f"{db_path.parent}\n\n"
            "Сохраняйте эту папку, если делаете резервную копию.",
        )
    if (out_dir := smoke_dir()) is not None:  # самопроверка сборки, см. smoke.py
        schedule_smoke_test(app, window, out_dir)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
