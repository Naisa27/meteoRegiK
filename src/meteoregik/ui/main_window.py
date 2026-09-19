from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMainWindow, QVBoxLayout, QWidget
from sqlalchemy.engine import Engine

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
        layout.addWidget(self._build_header())

        placeholder = QLabel("meteoRegiK — экраны появятся здесь")
        placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(placeholder, stretch=1)

        self.setCentralWidget(central)

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("Файл")

        import_action = QAction("Импорт данных…", self)
        import_action.triggered.connect(self._open_import_dialog)
        file_menu.addAction(import_action)

    def _open_import_dialog(self) -> None:
        dialog = ImportDialog(self.engine, self)
        dialog.exec()

    def _build_header(self) -> QWidget:
        header = QWidget()
        header.setObjectName("header")
        header.setStyleSheet("#header { background: #f2f2f2; border-bottom: 1px solid #d0d0d0; }")

        row = QHBoxLayout(header)
        row.setContentsMargins(12, 8, 12, 8)
        row.setSpacing(10)

        title = QLabel("meteoRegiK")
        font = title.font()
        font.setPointSize(font.pointSize() + 4)
        font.setBold(True)
        title.setFont(font)

        row.addWidget(title)
        row.addStretch(1)
        return header
