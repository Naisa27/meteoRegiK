from tkinter import *
from tkinter import ttk


class MainView(ttk.Frame):

    def __init__(self, parent, controller):
        super().__init__(parent)
        self.controller = controller
        self._create_widgets()

    def _create_widgets(self):
        logo = PhotoImage( file="static/img/snowman-alt_24.png" )
        title_label = ttk.Label(
            self,
            text="Herzlich willkommen!",
            font=("Verdana", 16, "bold"),
            image=logo,
            compound="left",
            # borderwidth=5,
            # relief="groove",
            padding=8,
            foreground="#B71C1C"
        )
        title_label.pack( pady=20 )

        main_frame = ttk.Frame(
            self,
            borderwidth=1,
            relief=SOLID,
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

        tab_frame1 = ttk.Frame( notebook )
        tab_frame2 = ttk.Frame( notebook )

        tab_frame1.pack(
            fill=BOTH,
            expand=True
        )
        tab_frame2.pack(
            fill=BOTH,
            expand=True
        )

        tab1_logo = PhotoImage( file="static/img/cloud-sun.png" )
        tab2_logo = PhotoImage( file="static/img/snowflake.png" )

        notebook.add(
            tab_frame1,
            text="Исходные данные",
            image=tab1_logo,
            compound=LEFT
        )
        notebook.add(
            tab_frame2,
            text="Расчёты",
            image=tab2_logo,
            compound=RIGHT
        )

        main_frame.pack(
            anchor=NW,
            fill=X,
            padx=5,
            pady=5
        )
