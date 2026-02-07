from tkinter import *
from tkinter import ttk


class data_frame_view:
    def __init__(self, frame):
        self.frame = frame

    def create_tab(self):
        self.frame.grid_rowconfigure( index=0, weight=1 )
        self.frame.grid_columnconfigure( index=0, weight=1 )
        self.frame.grid_columnconfigure( index=1, weight=1 )

        add_button = ttk.Button(
            self.frame,
            text="Добавить данные"
        )

        add_button.grid(
            row=0,
            column=0,
            sticky=EW,
            padx=10,
            pady=10
        )

        select_button = ttk.Button(
            self.frame,
            text="Посмотреть существующие"
        )

        select_button.grid(
            row=0,
            column=1,
            sticky=EW,
            padx=10,
            pady=10
        )