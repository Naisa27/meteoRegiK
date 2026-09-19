from __future__ import annotations

from PySide6.QtWidgets import QMainWindow, QVBoxLayout, QWidget
from sqlalchemy.engine import Engine

from meteoregik.ui.greeting_widget import GreetingWidget
from meteoregik.ui.import_dialog import ImportDialog


class MainWindow(QMainWindow):
    """Каркас главного окна. Экраны (импорт, просмотр данных, агрегаты,
    дашборд, карта, экспорт, справочник станций) подключаются по мере
    реализации — см. раздел "Экраны UI" в .claude/docs/plan.md.

    Без верхнего меню — единственное действие пока (импорт данных) доступно
    кнопкой на приветственном экране (см. GreetingWidget.import_requested)."""

    def __init__(self, engine: Engine) -> None:
        super().__init__()
        self.engine = engine
        self.setWindowTitle("meteoRegiK")
        self.resize(1100, 750)

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        greeting = GreetingWidget()
        greeting.import_requested.connect(self._open_import_dialog)
        layout.addWidget(greeting, stretch=1)

        self.setCentralWidget(central)

    def _open_import_dialog(self) -> None:
        dialog = ImportDialog(self.engine, self)
        dialog.exec()
