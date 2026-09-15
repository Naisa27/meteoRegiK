from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMainWindow, QVBoxLayout, QWidget
from sqlalchemy.engine import Engine

from meteoregik.resources import LOGO_PATH

LOGO_HEIGHT = 40


class MainWindow(QMainWindow):
    """Каркас главного окна. Экраны (импорт, просмотр данных, агрегаты,
    дашборд, карта, экспорт, справочник станций) подключаются по мере
    реализации — см. раздел "Экраны UI" в .claude/docs/plan.md."""

    def __init__(self, engine: Engine) -> None:
        super().__init__()
        self.engine = engine
        self.setWindowTitle("meteoRegiK")
        self.resize(1100, 750)

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._build_header())

        placeholder = QLabel("meteoRegiK — экраны появятся здесь")
        placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(placeholder, stretch=1)

        self.setCentralWidget(central)

    def _build_header(self) -> QWidget:
        header = QWidget()
        header.setObjectName("header")
        header.setStyleSheet("#header { background: #f2f2f2; border-bottom: 1px solid #d0d0d0; }")

        row = QHBoxLayout(header)
        row.setContentsMargins(12, 8, 12, 8)
        row.setSpacing(10)

        logo = QLabel()
        pixmap = QPixmap(str(LOGO_PATH))
        if not pixmap.isNull():
            logo.setPixmap(
                pixmap.scaledToHeight(LOGO_HEIGHT, Qt.TransformationMode.SmoothTransformation)
            )

        title = QLabel("meteoRegiK")
        font = title.font()
        font.setPointSize(font.pointSize() + 4)
        font.setBold(True)
        title.setFont(font)

        row.addWidget(logo)
        row.addWidget(title)
        row.addStretch(1)
        return header
