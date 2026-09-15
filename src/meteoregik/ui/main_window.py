from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QMainWindow
from sqlalchemy.engine import Engine


class MainWindow(QMainWindow):
    """Каркас главного окна. Экраны (импорт, просмотр данных, агрегаты,
    дашборд, карта, экспорт, справочник станций) подключаются по мере
    реализации — см. раздел "Экраны UI" в .claude/docs/plan.md."""

    def __init__(self, engine: Engine) -> None:
        super().__init__()
        self.engine = engine
        self.setWindowTitle("meteoRegiK")
        self.resize(1100, 750)
        placeholder = QLabel("meteoRegiK — экраны появятся здесь")
        placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setCentralWidget(placeholder)
