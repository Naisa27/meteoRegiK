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

from meteoregik.resources import STATIC_DIR

BG = "#F6F8FA"
TEXT_PRIMARY = "#26333A"
TEXT_SECONDARY = "#5B6B74"
ACCENT = "#B8862E"  # урожайное золото — основное действие (импорт данных)
ACCENT_HOVER = "#A6761F"
ACCENT_PRESSED = "#8A5C14"
ACCENT_DISABLED = "#DDCBA6"
# Второй акцент — пасмурно-небесный синий, пара к "урожайному золоту" в той
# же агрометеорологической палитре (солнце/урожай vs небо/влага), не просто
# случайный "второй цвет". Используется для действий, отличных от основного
# ("Просмотр исходных данных" рядом с "Импорт данных" — см. правки 2026-09-19).
ACCENT_SKY = "#3E6B87"
ACCENT_SKY_HOVER = "#325571"
ACCENT_SKY_PRESSED = "#264357"
ACCENT_SKY_DISABLED = "#A9BEC9"
# Третий акцент — травяной зелёный, третий естественный элемент той же
# агрометеорологической палитры (солнце/урожай, небо/влага, растительность),
# для кнопки "Просмотр агрегатных данных" на приветственном экране (2026-09-22).
# Не переиспользует зелёный из BUTTON_QSS#add ("добавить строку" в
# data_browser.py) — та же семантика была бы обманчива на кнопке другого
# уровня важности. HEX подобран той же формулой оттенков, что и у
# ACCENT_SKY (hover ~0.81, pressed ~0.63, disabled — смешение с белым
# t≈0.44), контраст с белым текстом кнопки 5.77:1 (не ниже, чем у
# ACCENT_SKY 5.74:1) — с запасом выше минимума WCAG 2.2 для полужирного
# текста кнопки (3:1 для крупного текста).
ACCENT_GRASS = "#46703A"
ACCENT_GRASS_HOVER = "#395B2F"
ACCENT_GRASS_PRESSED = "#2C4624"
ACCENT_GRASS_DISABLED = "#AEC0A8"
BORDER = "#D7DEE3"
INPUT_BG = "#FFFFFF"
INPUT_BG_READONLY = "#EEF2F4"
SUCCESS = "#3B7A3B"
# Текст ошибки рядом с полем — тот же красный, что у кнопки "удалить"
# (BUTTON_QSS#danger), контраст к BG ≈ 6.7:1.
ERROR_TEXT = "#A03030"
# Шапка таблиц — светло-голубой тон в паре с ACCENT_SKY
HEADER_BG = "#DCE7EE"
HEADER_BORDER = "#C3D2DB"
CALENDAR_DISABLED_TEXT = "#B7C2C9"  # недоступные (будущие) дни календаря
# Кольцо фокуса кнопок: тёмное, контраст не ниже 3:1 и к фону окна, и к золотой
# заливке (WCAG 2.2, focus-appearance); плюс жирный шрифт — фокус виден не
# только цветом (см. ui-ux-pro-max: focus-states).
FOCUS_RING = "#111A20"  # почти чёрный: TEXT_PRIMARY давал на синей заливке лишь 2.2:1
FOCUS_RING_WIDTH = 2
# Стрелка выпадающих списков/календаря: при стилизации рамки Qt перестаёт
# рисовать родную стрелку, поэтому рисуем свою (static/arrow_down.png).
ARROW_DOWN = (STATIC_DIR / "arrow_down.png").as_posix()


def _padding_values(padding: str) -> tuple[int, int]:
    vertical, horizontal = (int(v.removesuffix("px")) for v in padding.split())
    return vertical, horizontal


def _shifted_padding(vertical: int, horizontal: int, shift: int = 0) -> str:
    """Отступы с вертикальным сдвигом содержимого (shift > 0 — вниз)."""
    return f"{vertical + shift}px {horizontal}px {vertical - shift}px {horizontal}px"


def _pressed_padding(padding: str) -> str:
    """Эффект вдавливания кнопки: при нажатии содержимое сдвигается на 1px вниз
    (верхний отступ +1, нижний -1). Qt-стили не умеют transform/inset-тень,
    поэтому эффект нажатия = сдвиг текста + более тёмный фон и рамка."""
    return _shifted_padding(*_padding_values(padding), shift=1)


def _button_qss(
    selectors: tuple[str, ...],
    *,
    bg: str,
    hover: str,
    pressed: str,
    disabled_bg: str,
    text: str,
    disabled_text: str,
    border: str = "none",
    pressed_border: str | None = None,
    disabled_border: str | None = None,
    padding: str = "8px 20px",
    bold: bool = False,
) -> str:
    """Полный набор состояний кнопки (обычное/hover/фокус/нажатие/недоступна) —
    единый стандарт для всех кнопок приложения: нажатие и фокус всегда
    сопровождаются видимой реакцией (см. правки 2026-09-20 и 2026-09-21).

    Фокус — жирный шрифт и кольцо FOCUS_RING_WIDTH px. Рамка при фокусе шире
    обычной (у заливных кнопок её вовсе нет), поэтому отступы уменьшаются на
    разницу — размер кнопки при смене фокуса не «прыгает». Для нажатой кнопки
    с фокусом (а нажатая кнопка почти всегда в фокусе) — отдельное правило
    `:focus:pressed`: оно специфичнее и `:focus`, и `:pressed`."""

    def rule(state: str, body: str) -> str:
        return ", ".join(f"{sel}{state}" for sel in selectors) + " {" + body + "}\n"

    vertical, horizontal = _padding_values(padding)
    border_width = 0 if border == "none" else int(border.split("px")[0])
    grow = FOCUS_RING_WIDTH - border_width
    focus_v, focus_h = vertical - grow, horizontal - grow
    ring = f"border: {FOCUS_RING_WIDTH}px solid {FOCUS_RING};"

    weight = "font-weight: 600;" if bold else ""
    return (
        rule("", f"border-radius: 8px; padding: {padding}; background-color: {bg};"
                 f" color: {text}; border: {border}; {weight}")
        + rule(":hover", f"background-color: {hover};")
        + rule(":focus", f"{ring} padding: {focus_v}px {focus_h}px; font-weight: 700;")
        + rule(":pressed", f"background-color: {pressed}; padding: {_pressed_padding(padding)};"
                           + (f" border-color: {pressed_border};" if pressed_border else ""))
        + rule(":focus:pressed", f"background-color: {pressed}; {ring}"
                                 f" padding: {_shifted_padding(focus_v, focus_h, 1)};")
        + rule(":disabled", f"background-color: {disabled_bg}; color: {disabled_text};"
                            + (f" border-color: {disabled_border};" if disabled_border else ""))
    )


def _filled_button_qss(base: str, hover: str, pressed: str, disabled: str) -> str:
    return _button_qss(
        ("QPushButton",),
        bg=base, hover=hover, pressed=pressed, disabled_bg=disabled,
        text="#FFFFFF", disabled_text="#FFFFFF", padding="10px 28px", bold=True,
    )


PRIMARY_BUTTON_QSS = _filled_button_qss(ACCENT, ACCENT_HOVER, ACCENT_PRESSED, ACCENT_DISABLED)
SKY_BUTTON_QSS = _filled_button_qss(ACCENT_SKY, ACCENT_SKY_HOVER, ACCENT_SKY_PRESSED, ACCENT_SKY_DISABLED)
GRASS_BUTTON_QSS = _filled_button_qss(ACCENT_GRASS, ACCENT_GRASS_HOVER, ACCENT_GRASS_PRESSED, ACCENT_GRASS_DISABLED)


def _scaled(hex_color: str, factor: float) -> str:
    channels = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    return "#" + "".join(f"{round(c * factor):02X}" for c in channels)


def _mixed_with_white(hex_color: str, t: float) -> str:
    channels = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    return "#" + "".join(f"{round(c + (255 - c) * t):02X}" for c in channels)


def accent_button_qss(base: str) -> str:
    """Заливная кнопка по одному базовому цвету: hover/pressed/disabled —
    той же формулой, что подобраны ACCENT_SKY/ACCENT_GRASS вручную
    (затемнение ×0.81 и ×0.63, недоступная — смешение с белым)."""
    return _filled_button_qss(base, _scaled(base, 0.81), _scaled(base, 0.63), _mixed_with_white(base, 0.55))


# Ряды кнопок главного окна (правка 2026-09-30): каждый ряд — своя гамма,
# кнопки внутри ряда — разные оттенки этой гаммы. Оттенки различаются в
# основном тоном, а не светлотой: заливка должна держать и контраст ≥4.5:1 к
# белому тексту, и ≥3:1 к кольцу фокуса FOCUS_RING — это коридор светлоты
# шириной всего ~5.0–5.8:1 к белому, "светлая и тёмная" версии одного цвета в
# него не помещаются (проверяется в tests/test_theme.py).
# Ряд 1 "Работа с данными" — небо/вода: индиго, пасмурно-синий, морская волна,
# сумеречный сине-фиолетовый ("Дашборд", 2026-10-01).
DATA_ROW_COLORS = ("#4B63B0", ACCENT_SKY, "#2A7480")
DATA_ROW_TITLE = ACCENT_SKY
# Ряд 2 "Расчёт показателей" — растительность (кнопок пока нет, гамма
# зарезервирована): травяной, зелёно-бирюзовый, оливковый.
CALC_ROW_COLORS = (ACCENT_GRASS, "#3E7258", "#5E6E2A")
CALC_ROW_TITLE = ACCENT_GRASS
# Ряд 3 "Справочники" — урожай: тёмное золото (ACCENT для белого текста
# светловат — 3.2:1).
DIRECTORY_ROW_COLORS = ("#94661C", "#8A5A2B", "#7C6A1E")
DIRECTORY_ROW_TITLE = "#94661C"
# Ряд 4 "Аналитика" (дашборд) — сумеречный фиолетовый, бывший четвёртый
# оттенок ряда данных; белый текст на нём — 5.8:1, подпись на фоне — 5.5:1.
ANALYTICS_ROW_COLORS = ("#665BA6",)
ANALYTICS_ROW_TITLE = "#665BA6"

# Стили кнопок по objectName. Голый QPushButton (без objectName — например,
# кнопки QMessageBox) выглядит как "secondary", чтобы все кнопки приложения
# были в одном стиле и с одинаковой реакцией на нажатие.
BUTTON_QSS = (
    _button_qss(
        ("QPushButton", "QPushButton#secondary"),
        bg="transparent", hover="#ECF1F4", pressed="#D5DFE5", disabled_bg="transparent",
        text=TEXT_PRIMARY, disabled_text=TEXT_SECONDARY,
        border=f"1px solid {BORDER}", pressed_border="#AEBCC5", disabled_border="#E7ECEF",
    )
    + _button_qss(
        ("QPushButton#primary",),
        bg=ACCENT, hover=ACCENT_HOVER, pressed=ACCENT_PRESSED, disabled_bg=ACCENT_DISABLED,
        text="#FFFFFF", disabled_text="#FFFFFF", bold=True,
    )
    + _button_qss(
        ("QPushButton#skySoft",),  # светло-голубая: вспомогательные фильтры (в тон ACCENT_SKY)
        bg="#DCE9F1", hover="#CADDE9", pressed="#AECBDC", disabled_bg="#EDF3F7",
        text="#2F5F7C", disabled_text="#9DB5C3",
        border="1px solid #B5CFDE", pressed_border="#8FB5CB", disabled_border="#DDE9F0",
    )
    + _button_qss(
        ("QPushButton#add",),  # светло-зелёная: добавление
        bg="#DDF0DA", hover="#CDE8C9", pressed="#B5D7AF", disabled_bg="#EFF6EE",
        text="#2B6A2E", disabled_text="#9DB39B",
        border="1px solid #B3D8AD", pressed_border="#88BA80", disabled_border="#DDE9DA",
    )
    + _button_qss(
        ("QPushButton#danger",),  # светло-красная: удаление
        bg="#F9DDDD", hover="#F4CBCB", pressed="#EBAFAF", disabled_bg="#FCF0F0",
        text="#A03030", disabled_text="#CDA5A5",
        border="1px solid #E8B3B3", pressed_border="#D68888", disabled_border="#F3DCDC",
    )
)

# Стиль всего приложения (QApplication.setStyleSheet, см. main.py) — чтобы
# любая новая кнопка автоматически получала стандартную реакцию на нажатие.
APP_QSS = BUTTON_QSS

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
QLineEdit, QComboBox {{
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 6px 10px;
    background: {INPUT_BG};
    color: {TEXT_PRIMARY};
}}
QComboBox::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: center right;
    width: 28px;
    border: none;
}}
QComboBox::down-arrow {{
    image: url("{ARROW_DOWN}");
    width: 12px;
    height: 8px;
}}
QFrame#group {{
    background: transparent;
    border: 1px solid {BORDER};
    border-radius: 8px;
}}
QHeaderView::section {{
    background-color: {HEADER_BG};
    color: {TEXT_PRIMARY};
    font-weight: 600;
    padding: 6px 8px;
    border: none;
    border-right: 1px solid {HEADER_BORDER};
    border-bottom: 1px solid {HEADER_BORDER};
}}
QTableCornerButton::section {{
    background-color: {HEADER_BG};
    border: none;
    border-right: 1px solid {HEADER_BORDER};
    border-bottom: 1px solid {HEADER_BORDER};
}}
QTabWidget::pane {{
    border: 1px solid {BORDER};
    border-radius: 8px;
    top: -1px;
    background: transparent;
}}
QTabBar::tab {{
    background: transparent;
    color: {TEXT_SECONDARY};
    font-weight: 600;
    padding: 8px 18px;
    margin-right: 4px;
    border: none;
    border-bottom: 3px solid transparent;
}}
QTabBar::tab:hover {{
    color: {TEXT_PRIMARY};
}}
QTabBar::tab:selected {{
    color: {TEXT_PRIMARY};
    border-bottom: 3px solid {ACCENT_SKY};
}}
QTabBar::tab:focus {{
    background: {HEADER_BG};
    border-radius: 6px;
}}
QTableView {{
    alternate-background-color: #F9FBFC;
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
{BUTTON_QSS}
"""

# Календарь-выпадашка поля даты (ui/date_field.py): голубая панель навигации,
# выбранный день — основным акцентом, будущие (недоступные) дни бледные.
CALENDAR_QSS = f"""
QFrame#calendarPopup {{
    background: {INPUT_BG};
    border: 1px solid {BORDER};
}}
QCalendarWidget QWidget#qt_calendar_navigationbar {{
    background-color: {HEADER_BG};
    border-radius: 6px;
}}
QCalendarWidget QToolButton {{
    color: {TEXT_PRIMARY};
    background: transparent;
    border: none;
    border-radius: 6px;
    padding: 4px 10px;
    font-weight: 600;
}}
QCalendarWidget QToolButton:hover {{
    background-color: #C9D9E3;
}}
QCalendarWidget QToolButton:pressed {{
    background-color: #B3C8D6;
}}
QCalendarWidget QToolButton::menu-indicator {{
    image: none;
}}
QCalendarWidget QMenu {{
    background: {INPUT_BG};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER};
}}
QCalendarWidget QSpinBox {{
    background: {INPUT_BG};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER};
    border-radius: 4px;
    padding: 2px 4px;
}}
QCalendarWidget QAbstractItemView:enabled {{
    background: {INPUT_BG};
    color: {TEXT_PRIMARY};
    selection-background-color: {ACCENT};
    selection-color: #FFFFFF;
    outline: none;
}}
QCalendarWidget QAbstractItemView:disabled {{
    color: {CALENDAR_DISABLED_TEXT};
}}
"""
