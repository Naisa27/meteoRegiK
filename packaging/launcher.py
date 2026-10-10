"""Точка входа собранного приложения (packaging/meteoregik.spec).

Отдельный файл, а не src/meteoregik/main.py напрямую: PyInstaller запускает
точку входа как скрипт верхнего уровня, и пакет meteoregik должен
импортироваться по своему полному имени.
"""

from meteoregik.main import main

if __name__ == "__main__":
    main()
