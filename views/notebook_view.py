from tkinter import ttk


class Notebook(ttk.Notebook):
    def __init__(self, master, **kwargs):
        super().__init__(master, **kwargs)

    def add(self, child, **kwargs):
        # Добавляем кастомный текст с отступами
        if "text" in kwargs:
            kwargs["text"] = f"  {kwargs['text']}  "
        super().add(child, **kwargs)
