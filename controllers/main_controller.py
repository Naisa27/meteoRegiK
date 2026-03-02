from tkinter.constants import BOTH

from data.database import DatabaseManager


class MainController:

    def __init__(self, root):
        self.root = root
        self.current_view = None

        # Инициализация моделей и сервисов


        # self.show_main_view()

    def show_main_view(self):
        from views.main_window import MainView
        self._switch_view(MainView)

    def _switch_view(self, view_class):
        print(f"{self.current_view=}")
        if self.current_view:
            self.current_view.destroy()

        self.current_view = view_class(self.root, self)
        self.current_view.pack(fill=BOTH, expand=True)

    def run(self):
        self.show_main_view()
