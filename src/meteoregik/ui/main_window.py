from __future__ import annotations

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMainWindow, QVBoxLayout, QWidget
from sqlalchemy.engine import Engine

from meteoregik.ui.greeting_widget import GreetingWidget
from meteoregik.ui.import_dialog import ImportDialog


class MainWindow(QMainWindow):
    """Каркас главного окна. Экраны (импорт, просмотр данных, агрегаты,
    дашборд, карта, экспорт, справочник станций) подключаются по мере
    реализации — см. раздел "Экраны UI" в .claude/docs/plan.md."""

    def __init__(self, engine: Engine) -> None:
        super().__init__()
        self.engine = engine
        self.setWindowTitle("meteoRegiK")
        self.resize(1100, 750)

        self._build_menu()

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(GreetingWidget(), stretch=1)

        self.setCentralWidget(central)

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("Файл")

        import_action = QAction("Импорт данных…", self)
        import_action.triggered.connect(self._open_import_dialog)
        file_menu.addAction(import_action)

    def _open_import_dialog(self) -> None:
        dialog = ImportDialog(self.engine, self)
        dialog.exec()
