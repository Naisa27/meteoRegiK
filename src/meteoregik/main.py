"""Точка входа: uv run python -m meteoregik.main"""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from meteoregik.db.migrations import run_migrations
from meteoregik.db.session import get_engine
from meteoregik.ui.main_window import MainWindow


def main() -> None:
    engine = get_engine()
    run_migrations(engine)

    app = QApplication(sys.argv)
    window = MainWindow(engine)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
