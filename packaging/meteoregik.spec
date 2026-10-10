# -*- mode: python ; coding: utf-8 -*-
"""Сборка meteoRegiK: uv run pyinstaller packaging/meteoregik.spec --noconfirm

Результат — папка dist/meteoRegiK (exe и всё, что ему нужно), из которой
packaging/meteoregik.iss собирает установщик. Сборка папкой, а не одним
файлом (решение пользователя 2026-10-10): с картой в приложение входит
браузерный движок Qt, и однофайловый exe распаковывал бы сотни мегабайт во
временную папку при каждом запуске.

Ресурсы кладутся с той же относительной структурой, что в исходниках
(meteoregik/static, meteoregik/seed_data) — по ней их находит
meteoregik/resources.py.
"""
from pathlib import Path

ROOT = Path(SPECPATH).parent
PACKAGE = ROOT / "src" / "meteoregik"

a = Analysis(
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(ROOT / "src")],
    binaries=[],
    datas=[
        (str(PACKAGE / "static"), "meteoregik/static"),
        (str(PACKAGE / "seed_data"), "meteoregik/seed_data"),
    ],
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    # Не используются приложением, а в сборку попадают вслед за matplotlib и pandas
    excludes=["tkinter", "pytest", "IPython", "notebook"],
    noarchive=False,
)

# --- что из Qt в сборку не кладём ---
# PyInstaller забирает Qt с запасом: без отбора папка весила 643 МБ, из них
# 536 МБ — PySide6. Убирается только то, без чего приложение проверенно
# работает (packaging/build.ps1 после сборки запускает самопроверку с
# открытием карты): отладочные копии ресурсов браузерного движка, его
# переводы на все языки, кроме русского и английского, и модули Qt, которых
# в приложении нет (QML-интерфейсы, 3D, графики Qt). Сам движок и то, от
# чего он зависит (Quick, Qml, OpenGL, WebChannel, Positioning), остаются.
_KEPT_LOCALES = ("ru", "en-US", "en")
_UNUSED_QT_LIBRARIES = (
    "Qt63D", "Qt6Quick3D", "Qt6Charts", "Qt6Graphs", "Qt6DataVisualization",
    "Qt6QuickControls2", "Qt6QuickDialogs2", "Qt6QuickTemplates2", "Qt6QuickTimeline",
    "Qt6QuickParticles", "Qt6QuickEffects", "Qt6QuickLayouts", "Qt6QuickShapes", "Qt6QuickTest",
    "Qt6QuickVectorImage", "Qt6VirtualKeyboard", "Qt6Multimedia", "Qt6SpatialAudio",
    "Qt6Designer", "Qt6Help", "Qt6Sql", "Qt6Test", "Qt6Bluetooth", "Qt6Nfc", "Qt6Sensors",
    "Qt6SerialPort", "Qt6SerialBus", "Qt6RemoteObjects", "Qt6Scxml", "Qt6StateMachine",
    "Qt6TextToSpeech", "Qt6Location", "Qt6Pdf", "Qt6LabsPlatform", "Qt6Labs",
)


def _unneeded(dest: str) -> bool:
    path = Path(dest)
    parts = path.parts
    if "PySide6" not in parts:
        return False
    name = path.name
    if ".debug." in name:
        return True
    if "qml" in parts:  # QML-модули: интерфейс приложения — на виджетах
        return True
    if "qtwebengine_locales" in parts:
        return path.stem not in _KEPT_LOCALES
    if "translations" in parts and path.suffix == ".qm":
        return not name.endswith(tuple(f"_{locale.split('-')[0]}.qm" for locale in _KEPT_LOCALES))
    if name == "qtwebengine_devtools_resources.pak":  # инструменты разработчика браузера
        return True
    return name.startswith(_UNUSED_QT_LIBRARIES)


a.binaries = [entry for entry in a.binaries if not _unneeded(entry[0])]
a.datas = [entry for entry in a.datas if not _unneeded(entry[0])]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="meteoRegiK",
    icon=str(PACKAGE / "static" / "app_icon.ico"),
    console=False,  # оконное приложение — без чёрного окна консоли
    upx=False,  # сжатие UPX ломает библиотеки Qt и провоцирует антивирусы
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    upx=False,
    name="meteoRegiK",
)
