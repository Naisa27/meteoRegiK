from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMainWindow, QVBoxLayout, QWidget
from sqlalchemy.engine import Engine

from meteoregik.ui.dashboard_view import DashboardDialog
from meteoregik.ui.data_browser import DataBrowserDialog
from meteoregik.ui.anomaly_calc_dialog import AnomalyCalcDialog
from meteoregik.ui.greeting_widget import GreetingWidget
from meteoregik.ui.import_dialog import ImportDialog
from meteoregik.ui.query_panel import AggregateViewDialog
from meteoregik.ui.station_directory import StationDirectoryDialog
from meteoregik.version import app_version


class MainWindow(QMainWindow):
    """Каркас главного окна. Экраны (импорт, просмотр данных, агрегаты,
    дашборд, карта, экспорт, справочник станций) подключаются по мере
    реализации — см. раздел "Экраны UI" в .claude/docs/plan.md.

    Без верхнего меню — действия (импорт, просмотр данных, просмотр
    агрегатных данных) доступны кнопками на приветственном экране (см.
    GreetingWidget.import_requested / .data_browser_requested /
    .aggregate_view_requested)."""

    def __init__(self, engine: Engine) -> None:
        super().__init__()
        self.engine = engine
        # Номер версии — чтобы пользователь мог назвать его, сообщая об ошибке
        self.setWindowTitle(f"meteoRegiK {app_version()}".strip())
        self.resize(1100, 750)

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        greeting = GreetingWidget()
        greeting.import_requested.connect(self._open_import_dialog)
        greeting.data_browser_requested.connect(self._open_data_browser)
        greeting.aggregate_view_requested.connect(self._open_aggregate_view)
        greeting.anomaly_calc_requested.connect(self._open_anomaly_calc)
        greeting.dashboard_requested.connect(self._open_dashboard)
        greeting.map_requested.connect(self._open_map)
        greeting.station_directory_requested.connect(self._open_station_directory)
        layout.addWidget(greeting, stretch=1)
        self._greeting = greeting
        self._initial_focus_set = False

        self.setCentralWidget(central)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._initial_focus_set:
            # Начальный фокус — на самом приветствии, а не на первой кнопке: иначе
            # при запуске "Импорт данных" сразу обведена кольцом фокуса, как будто
            # выбрана. Кнопки по-прежнему достижимы по Tab (тогда кольцо уместно).
            # До показа окна setFocus() ничего не делает — поэтому здесь.
            self._initial_focus_set = True
            self._greeting.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
            self._greeting.setFocus()

    def center_on_screen(self) -> None:
        """Окно по центру доступной области экрана (без панели задач). Без
        этого Windows ставит новое окно у левого верхнего угла. Если окно
        больше экрана — сначала сжимается до его размеров."""
        # Нативное окно создаётся ДО расчёта размера: при создании оно может
        # оказаться на мониторе с другим масштабом (например, ноутбук 125% +
        # монитор 100%), и Qt пересчитал бы уже заданный размер под этот
        # масштаб — окно становилось больше экрана (1920 -> 2400). Заодно
        # только у нативного окна известна ширина рамки/заголовка.
        self.winId()
        screen = self.screen() or QApplication.primaryScreen()
        available = screen.availableGeometry()
        self.resize(min(self.width(), available.width()), min(self.height(), available.height()))
        frame = self.frameGeometry()
        frame.moveCenter(available.center())
        self.move(max(frame.left(), available.left()), max(frame.top(), available.top()))

    def _open_import_dialog(self) -> None:
        dialog = ImportDialog(self.engine, self)
        dialog.exec()

    def _open_data_browser(self) -> None:
        dialog = DataBrowserDialog(self.engine, self)
        dialog.exec()

    def _open_aggregate_view(self) -> None:
        dialog = AggregateViewDialog(self.engine, self)
        dialog.exec()

    def _open_dashboard(self) -> None:
        dialog = DashboardDialog(self.engine, self)
        dialog.exec()

    def _open_anomaly_calc(self) -> None:
        dialog = AnomalyCalcDialog(self.engine, self)
        dialog.exec()

    def _open_map(self) -> None:
        # Импорт здесь, а не в начале модуля: карта тянет за собой браузерный
        # движок Qt (QtWebEngine), и запуск приложения не должен его ждать
        from meteoregik.ui.map_view import MapDialog

        dialog = MapDialog(self.engine, self)
        dialog.exec()

    def _open_station_directory(self) -> None:
        dialog = StationDirectoryDialog(self.engine, self)
        dialog.exec()
