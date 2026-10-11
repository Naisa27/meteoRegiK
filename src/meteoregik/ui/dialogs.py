"""Общие модальные окна с русскими подписями кнопок. Стандартные кнопки
QMessageBox ("Yes"/"No") без файлов перевода Qt остаются английскими, поэтому
вопросы "да/нет" показываем через ask_yes_no."""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import QDialog, QMessageBox, QWidget


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


def exec_window_modal(dialog: QDialog) -> int:
    """Показать окно модально **для своего окна-владельца**, а не для всего
    приложения, и дождаться закрытия — замена dialog.exec() для экранов и их
    форм. Обычный exec() блокирует все окна программы, в том числе
    "Путеводитель по приложению" (ui/guide_window.py), а по нему работают,
    держа его открытым рядом с экраном. Модальность "для окна" блокирует
    только владельца и его предков; путеводитель — окно без родителя — в эту
    цепочку не входит. exec() сохраняет заранее заданную модальность окна.

    Сообщения QMessageBox и окно выбора файла остаются модальными для всего
    приложения: они короткие, и с открытым вопросом читать инструкцию незачем."""
    dialog.setWindowModality(Qt.WindowModality.WindowModal)
    return dialog.exec()


def enable_min_max_buttons(dialog: QDialog) -> None:
    """Кнопки "свернуть"/"развернуть" в заголовке большого экрана — у QDialog
    по умолчанию только "?" и крестик. Размер окна после открытия меняется
    независимо от главного окна."""
    dialog.setWindowFlags(
        Qt.WindowType.Window
        | Qt.WindowType.WindowTitleHint
        | Qt.WindowType.WindowSystemMenuHint
        | Qt.WindowType.WindowMinMaxButtonsHint
        | Qt.WindowType.WindowCloseButtonHint
    )


def minimize_owner_instead(dialog: QDialog, event: QEvent) -> None:
    """Вызывать из changeEvent модального окна со свернуть/развернуть:
    "Свернуть" сворачивает всё приложение, а не одно это окно. Окно модальное
    и принадлежит главному, а свёрнутое принадлежащее окно Windows не выносит
    на панель задач — его было бы не найти, пока главное окно заблокировано
    модальностью. Свёрнутое главное окно прячет и это окно, а при
    разворачивании с панели задач возвращает оба."""
    if event.type() != QEvent.Type.WindowStateChange or not dialog.isMinimized():
        return
    owner = dialog.parentWidget()
    if owner is None:
        return
    dialog.setWindowState(dialog.windowState() & ~Qt.WindowState.WindowMinimized)
    owner.window().showMinimized()
