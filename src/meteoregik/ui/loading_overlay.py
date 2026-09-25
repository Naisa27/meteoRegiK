"""Лоадер поверх области результата: полупрозрачная подложка, по центру —
надпись и бегущая полоса (QProgressBar в неопределённом режиме). Показывается
сразу по началу обработки запроса, чтобы было видно, что фильтр принят и
данные уже грузятся (правка 2026-09-25, п.6).

Надпись обязательна, не только анимация (ui-ux-pro-max: loading feedback,
не передавать состояние одной анимацией/цветом). Подложка перекрывает
прежний результат — щелчки по устаревшей таблице во время загрузки не
проходят, а старые данные видны приглушёнными, без "пустого экрана".
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPaintEvent
from PySide6.QtWidgets import QFrame, QLabel, QProgressBar, QVBoxLayout, QWidget

from meteoregik.ui import theme


class LoadingOverlay(QWidget):
    def __init__(self, text: str = "Загрузка данных…", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.BusyCursor)

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        card = QFrame()
        card.setObjectName("loadingCard")
        card.setStyleSheet(
            f"QFrame#loadingCard {{ background: {theme.INPUT_BG}; border: 1px solid {theme.BORDER};"
            " border-radius: 10px; }"
        )
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(28, 20, 28, 20)
        card_layout.setSpacing(12)

        self.label = QLabel(text)
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label.setStyleSheet(f"color: {theme.TEXT_PRIMARY}; font-size: 14px; font-weight: 600;")
        card_layout.addWidget(self.label)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)  # неопределённый режим — бегущая полоса
        self.progress.setTextVisible(False)
        self.progress.setFixedWidth(240)
        card_layout.addWidget(self.progress, alignment=Qt.AlignmentFlag.AlignHCenter)

        layout.addWidget(card, alignment=Qt.AlignmentFlag.AlignCenter)
        self.hide()

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        background = QColor(theme.BG)
        background.setAlpha(200)
        painter.fillRect(self.rect(), background)
        super().paintEvent(event)
