from tkinter import ttk
from tkinter import *

from views.data_frame_view import DataFrameView
from views.notebook_view import Notebook


class MainNotebook:

    def __init__(self, master, **kwargs):
        self.master = master
        self.images = {}

    def create( self ) -> None:
        style = ttk.Style()
        style.theme_use("clam")

        # Современный плоский стиль
        style.configure("Flat.TNotebook", background="#f0f0f0")
        style.configure("Flat.TNotebook.Tab",
            background="#e0e0e0",
            foreground="#333333",
            padding=[30, 15],
            font=('Verdana', 11, 'bold'),
            borderwidth=0
        )
        style.map("Flat.TNotebook.Tab",
            foreground=[("selected", "#c40d3c")],
            expand=[("selected", [1, 1, 0, 0])],  # Границы для эффекта выступа
            background = [("selected", "#ffffff")],
        )

        self.images["tab1_logo"] = PhotoImage( file="static/img/cloud-sun.png" )
        self.images["tab2_logo"] = PhotoImage( file="static/img/snowflake.png" )

        notebook = Notebook(self.master, style="Flat.TNotebook")
        notebook.pack(fill='both', expand=True)

        tab_data_frame = ttk.Frame( notebook )
        tab_calculate_frame = ttk.Frame( notebook )

        tab_data_frame.pack( fill='both', expand=True )
        tab_calculate_frame.pack( fill='both', expand=True )

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

        tab_data = DataFrameView( tab_data_frame )
        tab_data.create_tab()


