"""Точка входа: uv run python -m meteoregik.main"""

from __future__ import annotations

import sys

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from meteoregik.db.migrations import run_migrations
from meteoregik.db.seed import ensure_seed_data
from meteoregik.db.session import get_engine
from meteoregik.resources import APP_ICON_PATH
from meteoregik.ui.main_window import MainWindow


def main() -> None:
    engine = get_engine()
    run_migrations(engine)
    ensure_seed_data(engine)

    app = QApplication(sys.argv)
    app.setWindowIcon(QIcon(str(APP_ICON_PATH)))
    window = MainWindow(engine)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
