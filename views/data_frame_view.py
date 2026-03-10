import os
import threading
from tkinter import *
from tkinter import ttk, filedialog, messagebox

from data.data_import import data_import


class DataFrameView:
    def __init__(self, frame):
        self.frame = frame
        self._file_label = None
        self._data_formats_label = None
        self._progress_bar_label = None
        self.progress_bar = None
        self._filepath = None
        self.combobox = None
        self.load_button = None
        self.data_format_var = None

    def create_tab(self):
        self.frame.grid_rowconfigure( index=0, weight=1 )
        self.frame.grid_rowconfigure( index=1, weight=1 )
        self.frame.grid_rowconfigure( index=2, weight=1 )
        self.frame.grid_rowconfigure( index=3, weight=1 )
        self.frame.grid_rowconfigure( index=4, weight=1 )
        self.frame.grid_rowconfigure( index=5, weight=1 )
        self.frame.grid_rowconfigure( index=6, weight=1 )
        self.frame.grid_rowconfigure( index=7, weight=1 )
        self.frame.grid_rowconfigure( index=8, weight=1 )
        self.frame.grid_rowconfigure( index=9, weight=1 )
        self.frame.grid_columnconfigure( index=0, weight=1 )
        self.frame.grid_columnconfigure( index=1, weight=1 )

        style = ttk.Style()
        style.theme_use("clam")

        style.configure(
            "my.TButton",
            background="#f0f0f0",
            font=("Verdana", 12),
            foreground="#103bd9"
        )

        style.configure(
            "File.TLabel",
            font=("Verdana", 11),
            foreground="#103bd9"
        )

        add_button = ttk.Button(
            self.frame,
            text="Добавить данные",
            style="my.TButton",
            command=self.open_file
        )

        add_button.grid(
            row=0,
            column=0,
            sticky=EW,
            padx=10,
            pady=10
        )

        self._file_label = ttk.Label(
            self.frame,
            text="",
            style="File.TLabel"
        )
        self._file_label.grid(
            row=1,
            column=0,
            sticky=W,
            padx=10,
            pady=10
        )

        self._data_formats_label = ttk.Label(
            self.frame,
            text="",
            style="File.TLabel"
        )
        self._data_formats_label.grid(
            row=2,
            column=0,
            sticky=W,
            padx=10,
            pady=10
        )

        data_formats = [
            "rp5",
            "gg"
        ]

        self.data_format_var = StringVar()

        self.combobox = ttk.Combobox(
            self.frame,
            textvariable=self.data_format_var,
            values=data_formats,
        )

        # Биндим события
        self.combobox.bind( "<<ComboboxSelected>>", self._on_format_selected )

        self.load_button = ttk.Button(
            self.frame,
            text="Загрузить данные из файла",
            style="my.TButton",
            command=self.load_file
        )
        print( f'{ self.combobox.winfo_class()= }' )

        self._progress_bar_label = ttk.Label(
            self.frame,
            text="",
            style="File.TLabel"
        )
        self._progress_bar_label.grid(
            row=5,
            column=0,
            sticky=W,
            padx=10,
            pady=10
        )

        self.progress_bar = ttk.Progressbar(
            self.frame,
            orient="horizontal",
            length=300,
            mode="determinate"
        )


        select_button = ttk.Button(
            self.frame,
            text="Посмотреть существующие",
            style="my.TButton"
        )

        select_button.grid(
            row=0,
            column=1,
            sticky=EW,
            padx=10,
            pady=10
        )

    def open_file(self):
        self._filepath = filedialog.askopenfilename(
            title="Выберите файл Excel",
            filetypes=[
                ("Excel файлы", "*.xlsx *.xls"),  # Все Excel файлы
                ("Excel 2007+", "*.xlsx"),  # Только .xlsx
                ("Excel 97-2003", "*.xls"),  # Только .xls
                ("Все файлы", "*.*")  # На всякий случай
            ]
        )
        print(f"{self._filepath=}")
        if self._filepath:
            filename = os.path.basename(self._filepath)
            self._file_label.config(text=f"Для заливки выбран файл: {filename}")
            self._data_formats_label.config(text=f"Укажите формат данных в выбранном файле:")
            self.combobox.grid(
                row=3,
                column=0,
                sticky=W,
                padx=10,
                pady=10
            )

    def load_file(self) -> None:
        self._progress_bar_label.config( text="Идёт импорт данных..." )
        self._progress_bar_label.update_idletasks() # Принудительное обновление интерфейса

        self.progress_bar.grid(
            row=6,
            column=0,
            sticky=W,
            padx=10,
            pady=10
        )
        self.progress_bar.config( maximum=100 )

        if not self._filepath:
            messagebox.showerror( "Ошибка", "Выберите файл для импорта" )
            return

        # Запуск в потоке, чтобы GUI не завис
        thread = threading.Thread( target=self.task, daemon=True )
        thread.start()


    def task(self) -> None:
        data_format = self.data_format_var.get()
        result = data_import.import_from_excel(
            filepath=self._filepath,
            data_format=data_format,
            progress_callback=self.update_progress
        )
        self.frame.after( 0, lambda: print( "Готово:", result ) )
        self._progress_bar_label.config( text="Готово" )

    def update_progress( self, current, total ):
        self.frame.after( 0, lambda: self.progress_bar.config( value=(current / total * 100) ) )


    def _on_format_selected( self, event=None ):
        selected_format = self.combobox.get()
        if selected_format:
            print(f"{selected_format=}")
            print(f"{self.data_format_var.get()=}")
            # Показываем кнопку загрузки
            self.load_button.grid(
                row=4,
                column=0,
                sticky=W,
                padx=10,
                pady=10
            )

    @property
    def filepath(self):
        return self._filepath



