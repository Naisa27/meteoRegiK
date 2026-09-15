"""Пути к статическим ресурсам приложения (иконки, логотип)."""

from __future__ import annotations

from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parent / "static"

APP_ICON_PATH = STATIC_DIR / "app_icon.ico"
LOGO_PATH = STATIC_DIR / "barometr.jpg"
