"""Общие модальные окна с русскими подписями кнопок. Стандартные кнопки
QMessageBox ("Yes"/"No") без файлов перевода Qt остаются английскими, поэтому
вопросы "да/нет" показываем через ask_yes_no."""

from __future__ import annotations

from PySide6.QtWidgets import QMessageBox, QWidget


def ask_yes_no(parent: QWidget | None, title: str, text: str) -> bool:
    """Вопрос с кнопками "Да"/"Нет"; закрытие окна и Esc = "Нет" (кнопка "Нет"
    и по умолчанию — вопросы у нас про необратимые действия)."""
    box = QMessageBox(QMessageBox.Icon.Question, title, text, QMessageBox.StandardButton.NoButton, parent)
    yes_btn = box.addButton("Да", QMessageBox.ButtonRole.YesRole)
    no_btn = box.addButton("Нет", QMessageBox.ButtonRole.NoRole)
    for button, name in ((yes_btn, "primary"), (no_btn, "secondary")):
        button.setObjectName(name)
        # QMessageBox успевает применить стиль к кнопке ещё в addButton, до
        # смены имени — перерисовать по селектору QPushButton#<name>
        button.style().unpolish(button)
        button.style().polish(button)
    box.setDefaultButton(no_btn)
    box.setEscapeButton(no_btn)
    box.exec()
    return box.clickedButton() is yes_btn
