import tkinter as tk
from tkinter import ttk

from src.views.main_notebook_view import MainNotebook


class MainView(ttk.Frame):
    def __init__(self, parent, controller):
        super().__init__(parent)
        self.controller = controller
        self.images = {}
        self._create_widgets()

    def _create_widgets(self):
        self.images["logo"] = tk.PhotoImage(file="src/static/img/snowman-alt_24.png")
        title_label = ttk.Label(
            self,
            text="Herzlich willkommen!",
            font=("Verdana", 16, "bold"),
            image=self.images["logo"],
            compound="left",
            # borderwidth=5,
            # relief="groove",
            padding=8,
            foreground="#B71C1C",
        )
        title_label.pack(pady=20)

        main_frame = ttk.Frame(
            self,
            # borderwidth=1,
            # relief=SOLID,
            padding=[8, 10],
        )

        main_notebook = MainNotebook(main_frame)
        main_notebook.create()

        # print( f'{ notebook.winfo_class()= }')
        print(ttk.Style().theme_names())

        main_frame.pack(anchor=tk.NW, fill=tk.X, padx=5, pady=5)
