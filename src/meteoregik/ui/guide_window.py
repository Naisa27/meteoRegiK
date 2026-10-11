"""Окно "Путеводитель по приложению" — инструкция пользователя (кнопка под
часами на главном экране, решение пользователя 2026-10-11). Текст —
meteoregik/guide.py, здесь только показ: оглавление слева, раздел справа.

Окно **самостоятельное и немодальное, без родителя** — по инструкции работают,
держа её рядом с программой и нажимая кнопки на экранах. Экраны приложения
модальны только для главного окна (ui/dialogs.py::exec_window_modal), а такая
модальность блокирует окно-владельца и его предков; окно без родителя в эту
цепочку не входит и остаётся живым. С родителем MainWindow путеводитель
блокировался бы вместе с главным окном при открытии любого экрана. Обратная
сторона — окно не закрывается вместе с главным само, это делает
MainWindow.closeEvent.

Галочка "Поверх остальных окон" — на небольшом мониторе окно рядом с
программой не помещается, а без неё щелчок по программе прячет инструкцию.

Оглавление повторяет главный экран: те же подписи разделов тех же цветов
(theme.*_ROW_TITLE) и названия кнопок — раздел ищут по подписи на кнопке.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QBrush, QColor, QFont, QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSplitter,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from meteoregik import guide
from meteoregik.ui import theme
from meteoregik.ui.buttons import reserve_focus_width

_KEY_ROLE = Qt.ItemDataRole.UserRole
_GROUP_COLORS = {
    guide.GROUP_DATA: theme.DATA_ROW_TITLE,
    guide.GROUP_CALC: theme.CALC_ROW_TITLE,
    guide.GROUP_DIRECTORY: theme.DIRECTORY_ROW_TITLE,
    guide.GROUP_ANALYTICS: theme.ANALYTICS_ROW_TITLE,
}
_NAV_WIDTH = 236
_SIZE = (860, 700)
# Отступ окна от края экрана при первом показе
_SCREEN_MARGIN = 24
# Фон заметок в тексте и выбранного пункта оглавления; текст на нём — 11:1
_TINT = "#E6EEF3"

GUIDE_QSS = f"""
QWidget#guideWindow {{ background: {theme.BG}; }}
QSplitter::handle {{ background: transparent; }}
QListWidget#guideNav {{
    background: transparent;
    border: none;
    outline: none;
    color: {theme.TEXT_PRIMARY};
    font-size: 13px;
}}
QListWidget#guideNav::item {{
    padding: 7px 10px;
    border-left: 3px solid transparent;
    border-radius: 4px;
}}
QListWidget#guideNav::item:hover {{ background: #ECF1F4; }}
QListWidget#guideNav::item:selected {{
    background: {_TINT};
    color: {theme.TEXT_PRIMARY};
    font-weight: 600;
    border-left: 3px solid {theme.ACCENT_SKY};
}}
QListWidget#guideNav::item:selected:focus {{
    border: {theme.FOCUS_RING_WIDTH}px solid {theme.FOCUS_RING};
    border-left: 3px solid {theme.ACCENT_SKY};
}}
QListWidget#guideNav::item:disabled {{ background: transparent; padding: 14px 10px 3px 10px; }}
QTextBrowser#guideText {{
    background: {theme.INPUT_BG};
    border: 1px solid {theme.BORDER};
    border-radius: 8px;
    padding: 10px 6px 10px 18px;
}}
QTextBrowser#guideText:focus {{ border: {theme.FOCUS_RING_WIDTH}px solid {theme.ACCENT_SKY}; }}
QCheckBox {{ color: {theme.TEXT_PRIMARY}; background: transparent; spacing: 8px; }}
"""

# Стили текста раздела (подмножество CSS, которое понимает QTextDocument).
# Межстрочный интервал 135 % — длинный текст читают с экрана, а не просматривают.
_TEXT_CSS = f"""
h1 {{ font-family: Constantia; font-size: 21pt; font-weight: normal; color: {theme.TEXT_PRIMARY};
      margin-top: 0; margin-bottom: 10px; }}
h2 {{ font-size: 12pt; font-weight: 600; color: {theme.ACCENT_SKY_HOVER};
      margin-top: 20px; margin-bottom: 6px; }}
p  {{ line-height: 135%; margin-top: 0; margin-bottom: 9px; color: {theme.TEXT_PRIMARY}; }}
li {{ line-height: 135%; margin-bottom: 6px; color: {theme.TEXT_PRIMARY}; }}
a  {{ color: {theme.ACCENT_SKY_HOVER}; }}
table.note {{ margin-top: 4px; margin-bottom: 12px; }}
table.note td {{ background-color: {_TINT}; color: {theme.TEXT_PRIMARY}; line-height: 140%; }}
"""


class GuideWindow(QWidget):
    def __init__(self, data_dir: Path | None = None) -> None:
        super().__init__(None)
        self._data_dir = data_dir
        self.setObjectName("guideWindow")
        self.setWindowTitle(guide.GUIDE_TITLE)
        self.setStyleSheet(GUIDE_QSS)
        self.resize(*_SIZE)
        self._build_ui()
        self.show_section(guide.SECTIONS[0].key)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 12)
        layout.setSpacing(10)

        self.nav = QListWidget()
        self.nav.setObjectName("guideNav")
        self.nav.setAccessibleName("Разделы путеводителя")
        self.nav.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._fill_nav()
        self.nav.currentItemChanged.connect(self._on_nav_changed)

        self.text = QTextBrowser()
        self.text.setObjectName("guideText")
        self.text.setFont(QFont("Segoe UI", 11))
        self.text.setOpenLinks(False)  # ссылки — только между разделами, см. _on_link
        self.text.document().setDefaultStyleSheet(_TEXT_CSS)
        self.text.document().setDocumentMargin(8)
        self.text.anchorClicked.connect(self._on_link)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self.nav)
        splitter.addWidget(self.text)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([_NAV_WIDTH, _SIZE[0] - _NAV_WIDTH])
        layout.addWidget(splitter, stretch=1)

        bottom = QHBoxLayout()
        self.on_top_check = QCheckBox("Поверх остальных окон")
        self.on_top_check.setToolTip("Окно путеводителя остаётся видно, когда вы работаете в программе")
        self.on_top_check.toggled.connect(self._set_on_top)
        bottom.addWidget(self.on_top_check)
        bottom.addStretch(1)
        close_btn = QPushButton("Закрыть")
        close_btn.setObjectName("secondary")
        close_btn.clicked.connect(self.close)
        bottom.addWidget(close_btn)
        layout.addLayout(bottom)
        reserve_focus_width(close_btn)

    def _fill_nav(self) -> None:
        """Пункты оглавления; перед первым разделом каждой группы — её
        подпись (не выбирается, клавиши-стрелки её перешагивают)."""
        caption_font = QFont("Segoe UI", 8)
        caption_font.setWeight(QFont.Weight.DemiBold)
        caption_font.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 108)
        previous_group: str | None = None
        for section in guide.SECTIONS:
            if section.group != previous_group and (section.group is not None or previous_group is not None):
                # Пустая подпись отделяет раздел вне групп от последней группы
                caption = QListWidgetItem((section.group or "").upper())
                caption.setFlags(Qt.ItemFlag.NoItemFlags)
                caption.setFont(caption_font)
                caption.setForeground(QBrush(QColor(_GROUP_COLORS.get(section.group, theme.TEXT_SECONDARY))))
                self.nav.addItem(caption)
            previous_group = section.group
            item = QListWidgetItem(section.title)
            item.setData(_KEY_ROLE, section.key)
            self.nav.addItem(item)

    def current_key(self) -> str | None:
        item = self.nav.currentItem()
        return item.data(_KEY_ROLE) if item is not None else None

    def show_section(self, key: str) -> None:
        """Открыть раздел по ключу; неизвестный ключ ничего не меняет."""
        for row in range(self.nav.count()):
            if self.nav.item(row).data(_KEY_ROLE) == key:
                self.nav.setCurrentRow(row)
                return

    def _on_nav_changed(self, current: QListWidgetItem | None, _previous: QListWidgetItem | None) -> None:
        section = guide.find_section(current.data(_KEY_ROLE)) if current is not None else None
        if section is None:
            return
        self.text.setHtml(guide.section_html(section, self._data_dir))
        self.text.verticalScrollBar().setValue(0)

    def _on_link(self, url: QUrl) -> None:
        if url.scheme() == guide.LINK_SCHEME:
            self.show_section(url.path())

    def _set_on_top(self, on: bool) -> None:
        # Смена флага окна прячет его — показать заново, если оно было видно
        visible = self.isVisible()
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, on)
        if visible:
            self.show()

    def place_beside(self, owner: QWidget) -> None:
        """Первый показ — у правого края экрана, на высоте главного окна:
        путеводитель читают рядом с программой, а не поверх её середины."""
        screen = owner.screen() or QGuiApplication.primaryScreen()
        available = screen.availableGeometry()
        width = min(self.width(), available.width() - 2 * _SCREEN_MARGIN)
        height = min(self.height(), available.height() - 2 * _SCREEN_MARGIN)
        self.resize(width, height)
        frame_extra = owner.frameGeometry().height() - owner.height()  # заголовок и рамка
        left = available.right() - width - _SCREEN_MARGIN
        top = min(max(owner.frameGeometry().top(), available.top()), available.bottom() - height - frame_extra)
        self.move(left, max(top, available.top()))

    def bring_to_front(self) -> None:
        if self.isMinimized():
            self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()
