"""Общая палитра и стили Qt-виджетов для всех экранов — единый вид вместо
дефолтного системного. Основано на палитре, уже принятой для
greeting_widget.py (спокойные бело-голубые тона + акцент цвета урожайного
золота, тема агрометеорологии, не дефолтный SaaS-стиль).

Структурная логика — по принципам стиля "Minimalism & Swiss" (функциональные
профессиональные инструменты: чистая сетка, один акцентный цвет, чёткая
иерархия, никакой лишней декорации), но со скруглёнными, не острыми углами
у кнопок/полей/полосы прогресса — явное решение пользователя (2026-09-19),
осознанное отступление от "минималистичных" острых углов.

Группировка полей — тонкой рамкой (BORDER), не отличающимся цветом фона:
разный цвет фона у вложенных блоков уже давал "некрасивые" светлые плашки на
приветственном экране (см. правки от 2026-09-19) — здесь тот же фон, что и у
диалога целиком, разделение только рамкой и отступами.
"""

from __future__ import annotations

BG = "#F6F8FA"
TEXT_PRIMARY = "#26333A"
TEXT_SECONDARY = "#5B6B74"
ACCENT = "#B8862E"
ACCENT_HOVER = "#A6761F"
ACCENT_DISABLED = "#DDCBA6"
BORDER = "#D7DEE3"
INPUT_BG = "#FFFFFF"
INPUT_BG_READONLY = "#EEF2F4"
SUCCESS = "#3B7A3B"

PRIMARY_BUTTON_QSS = f"""
QPushButton {{
    border-radius: 8px;
    padding: 10px 28px;
    background-color: {ACCENT};
    color: #FFFFFF;
    border: none;
    font-weight: 600;
}}
QPushButton:hover {{
    background-color: {ACCENT_HOVER};
}}
QPushButton:disabled {{
    background-color: {ACCENT_DISABLED};
    color: #FFFFFF;
}}
"""

DIALOG_QSS = f"""
QDialog {{
    background: {BG};
}}
QLabel {{
    color: {TEXT_PRIMARY};
    background: transparent;
}}
QFrame#card {{
    background: transparent;
    border: 1px solid {BORDER};
    border-radius: 10px;
}}
QLineEdit {{
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 6px 10px;
    background: {INPUT_BG};
    color: {TEXT_PRIMARY};
}}
QLineEdit:read-only {{
    background: {INPUT_BG_READONLY};
    color: {TEXT_SECONDARY};
}}
QRadioButton {{
    color: {TEXT_PRIMARY};
    background: transparent;
    spacing: 8px;
}}
QProgressBar {{
    border: 1px solid {BORDER};
    border-radius: 7px;
    background: {INPUT_BG};
    text-align: center;
    color: {TEXT_SECONDARY};
    height: 18px;
}}
QProgressBar::chunk {{
    background-color: {ACCENT};
    border-radius: 6px;
}}
QPushButton {{
    border-radius: 8px;
    padding: 8px 20px;
}}
QPushButton#primary {{
    background-color: {ACCENT};
    color: #FFFFFF;
    border: none;
    font-weight: 600;
}}
QPushButton#primary:hover {{
    background-color: {ACCENT_HOVER};
}}
QPushButton#primary:disabled {{
    background-color: {ACCENT_DISABLED};
    color: #FFFFFF;
}}
QPushButton#secondary {{
    background-color: transparent;
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER};
}}
QPushButton#secondary:hover {{
    background-color: #ECF1F4;
}}
QPushButton#secondary:disabled {{
    color: {TEXT_SECONDARY};
    border-color: #E7ECEF;
}}
"""
