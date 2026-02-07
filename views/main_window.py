from tkinter import *
from tkinter import ttk
from tkinter.scrolledtext import ScrolledText

from views.data_frame_view import data_frame_view


class MainView(ttk.Frame):

    def __init__(self, parent, controller):
        super().__init__(parent)
        self.controller = controller
        self.images = {}
        self._create_widgets()

    def _create_widgets(self):
        self.images["logo"] = PhotoImage( file="static/img/snowman-alt_24.png" )
        title_label = ttk.Label(
            self,
            text="Herzlich willkommen!",
            font=("Verdana", 16, "bold"),
            image=self.images["logo"],
            compound="left",
            # borderwidth=5,
            # relief="groove",
            padding=8,
            foreground="#B71C1C"
        )
        title_label.pack( pady=20 )

        main_frame = ttk.Frame(
            self,
            # borderwidth=1,
            # relief=SOLID,
            padding=[8, 10]
        )

        notebook = ttk.Notebook(
            main_frame,
            height=200
        )
        notebook.pack(
            expand=True,
            fill=BOTH
        )

        tab_data_frame = ttk.Frame( notebook )
        tab_calculate_frame = ttk.Frame( notebook )

        tab_data_frame.pack(
            fill=BOTH,
            expand=True
        )
        tab_calculate_frame.pack(
            fill=BOTH,
            expand=True
        )

        self.images["tab1_logo"] = PhotoImage( file="static/img/cloud-sun.png" )
        self.images["tab2_logo"] = PhotoImage( file="static/img/snowflake.png" )

        notebook.add(
            tab_data_frame,
            text="Исходные данные",
            image=self.images["tab1_logo"],
            compound=LEFT
        )
        notebook.add(
            tab_calculate_frame,
            text="Расчёты",
            image=self.images["tab2_logo"],
            compound=RIGHT
        )

        tab_data = data_frame_view(tab_data_frame)
        tab_data.create_tab()

        main_frame.pack(
            anchor=NW,
            fill=X,
            padx=5,
            pady=5
        )
