"""Общие поправки размеров кнопок, которые нельзя выразить в QSS."""

from __future__ import annotations

from PySide6.QtWidgets import QPushButton

# Запас ширины под жирный шрифт фокуса (см. reserve_focus_width).
FOCUS_WIDTH_RESERVE = 20


def reserve_focus_width(*buttons: QPushButton) -> None:
    """`:focus` в theme.BUTTON_QSS утяжеляет шрифт кнопки (видимый фокус не
    только цветом), а на реальных Windows-шрифтах (Segoe UI) жирное
    начертание того же текста на несколько пикселей шире. sizeHint() кнопки
    при этом всегда считается под обычный вес — стилевой каскад Qt
    переопределяет программно выставленный QFont, а увеличение padding в QSS
    не помогает (область под текст всегда равна ширине текста обычного
    веса). Без запаса кнопка, стоящая в раскладке по своему sizeHint, в
    фокусе обрезает первую и последнюю буквы подписи (правки 2026-09-23 —
    "Просмотр агрегатных данных", 2026-09-25 — "Экспорт в Excel").

    Вызывать ПОСЛЕ того, как кнопка получила свой стиль (добавлена в
    раскладку окна со стилем или получила setStyleSheet), иначе sizeHint
    посчитается по чужим отступам."""
    for button in buttons:
        button.ensurePolished()
        button.setMinimumWidth(button.sizeHint().width() + FOCUS_WIDTH_RESERVE)
