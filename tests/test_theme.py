import re

from meteoregik.ui import theme


def test_every_button_style_has_hover_pressed_and_disabled_states():
    for selector in (
        "QPushButton", "QPushButton#secondary", "QPushButton#primary",
        "QPushButton#add", "QPushButton#danger",
    ):
        for state in ("hover", "pressed", "disabled"):
            assert f"{selector}:{state}" in theme.BUTTON_QSS, (selector, state)
    for qss in (theme.PRIMARY_BUTTON_QSS, theme.SKY_BUTTON_QSS, theme.GRASS_BUTTON_QSS):
        assert "QPushButton:pressed" in qss


def test_pressed_shifts_content_down_by_one_pixel():
    assert theme._pressed_padding("8px 20px") == "9px 20px 7px 20px"


def test_app_and_dialog_stylesheets_share_button_styles():
    assert theme.BUTTON_QSS in theme.APP_QSS
    assert theme.BUTTON_QSS in theme.DIALOG_QSS


def test_every_button_style_has_visible_focus_ring_and_bold_text():
    for selector in (
        "QPushButton", "QPushButton#secondary", "QPushButton#primary",
        "QPushButton#skySoft", "QPushButton#add", "QPushButton#danger",
    ):
        # селектор может стоять в списке ("QPushButton:focus, QPushButton#secondary:focus {")
        assert re.search(rf"{re.escape(selector)}:focus(?:,| \{{)", theme.BUTTON_QSS), selector
        assert f"{selector}:focus:pressed" in theme.BUTTON_QSS, selector
    assert f"border: {theme.FOCUS_RING_WIDTH}px solid {theme.FOCUS_RING}" in theme.BUTTON_QSS
    assert "font-weight: 700" in theme.BUTTON_QSS
    for qss in (theme.PRIMARY_BUTTON_QSS, theme.SKY_BUTTON_QSS, theme.GRASS_BUTTON_QSS):
        assert "QPushButton:focus" in qss


def _relative_luminance(hex_color: str) -> float:
    channels = [int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    r, g, b = (c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def test_focus_ring_has_3_to_1_contrast_against_window_and_button_fills():
    ring = _relative_luminance(theme.FOCUS_RING)
    for background in (theme.BG, theme.ACCENT, theme.ACCENT_SKY, theme.ACCENT_GRASS, "#DDF0DA", "#F9DDDD", "#DCE9F1"):
        lighter, darker = sorted((ring, _relative_luminance(background)), reverse=True)
        assert (lighter + 0.05) / (darker + 0.05) >= 3, background


def test_grass_accent_has_at_least_3_to_1_contrast_against_white_button_text():
    grass = _relative_luminance(theme.ACCENT_GRASS)
    white = _relative_luminance("#FFFFFF")
    lighter, darker = sorted((grass, white), reverse=True)
    assert (lighter + 0.05) / (darker + 0.05) >= 3


def test_focus_ring_does_not_change_button_size():
    """Кольцо шире рамки, поэтому отступы при фокусе уменьшаются на разницу:
    рамка + отступ одинаковы в обычном состоянии и в фокусе."""
    plain = theme._button_qss(
        ("QPushButton#x",), bg="#fff", hover="#fff", pressed="#fff", disabled_bg="#fff",
        text="#000", disabled_text="#000", padding="8px 20px",
    )
    bordered = theme._button_qss(
        ("QPushButton#y",), bg="#fff", hover="#fff", pressed="#fff", disabled_bg="#fff",
        text="#000", disabled_text="#000", border="1px solid #ccc", padding="8px 20px",
    )
    # без рамки: 0 + 8 == 2 + 6; с рамкой 1px: 1 + 8 == 2 + 7
    assert "QPushButton#x:focus {border: 2px solid" in plain and "padding: 6px 18px;" in plain
    assert "QPushButton#y:focus {border: 2px solid" in bordered and "padding: 7px 19px;" in bordered



def _contrast(first: str, second: str) -> float:
    lighter, darker = sorted((_relative_luminance(first), _relative_luminance(second)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def test_main_window_row_colors_keep_text_and_focus_ring_contrast():
    """Все оттенки рядов кнопок главного окна: белый текст ≥4.5:1, кольцо
    фокуса ≥3:1 к заливке; подпись ряда ≥4.5:1 к фону окна."""
    for colors, title in (
        (theme.DATA_ROW_COLORS, theme.DATA_ROW_TITLE),
        (theme.CALC_ROW_COLORS, theme.CALC_ROW_TITLE),
        (theme.DIRECTORY_ROW_COLORS, theme.DIRECTORY_ROW_TITLE),
    ):
        assert len(set(colors)) == len(colors)
        for color in colors:
            assert _contrast(color, "#FFFFFF") >= 4.5, color
            assert _contrast(color, theme.FOCUS_RING) >= 3, color
        assert _contrast(title, theme.BG) >= 4.5, title


def test_accent_button_qss_darkens_hover_and_pressed():
    """Производные оттенки — той же формулой, что ручные ACCENT_SKY_*
    (с точностью до округления каналов)."""
    assert theme._scaled(theme.ACCENT_SKY, 0.81) == "#32576D"  # вручную #325571
    assert theme._scaled(theme.ACCENT_SKY, 0.63) == "#274355"  # вручную #264357
    qss = theme.accent_button_qss(theme.ACCENT_SKY)
    assert "QPushButton:hover {background-color: #32576D;}" in qss
    assert "QPushButton:focus" in qss and "QPushButton:disabled" in qss
