"""Точка входа: uv run python -m meteoregik.main"""

from __future__ import annotations

import sys

from PySide6.QtCore import QCoreApplication, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from meteoregik.db.migrations import run_migrations
from meteoregik.db.seed import ensure_seed_database
from meteoregik.db.session import default_db_path, get_engine
from meteoregik.resources import APP_ICON_PATH
from meteoregik.ui import theme
from meteoregik.ui.main_window import MainWindow


def main() -> None:
    db_path = default_db_path()
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
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
