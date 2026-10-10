"""Номер версии приложения — для заголовка главного окна."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version


def app_version() -> str:
    """Версия из pyproject.toml — единственное место, где она записана (по
    ней же называется установщик, packaging/build.ps1). Читается из
    метаданных установленного пакета; в сборку они попадают отдельной
    строкой в packaging/meteoregik.spec (copy_metadata).

    Пустая строка, если метаданных нет: отсутствие номера в заголовке не
    повод не запускать приложение. Что в собранном exe номер на месте,
    проверяет самопроверка сборки."""
    try:
        return version("meteoregik")
    except PackageNotFoundError:
        return ""
