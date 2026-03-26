import tkinter as tk

from src.controllers.main_controller import MainController

# Диалоговые окна   следующий

"""
rp5 заливается

надо сделать заливку rgm

надо сделать проверку на повторы, чтобы уже залитые данные заново не заливались.

"""


def main():
    root = tk.Tk()
    icon = tk.PhotoImage(file="src/static/img/cloud-sun.png")
    # icon = PhotoImage( file="static/img/snowflake.png" )
    root.iconphoto(False, icon)
    root.title("MeteoRegiK")
    root.geometry("800x600")
    root.minsize(width=200, height=200)
    root.maxsize(width=1000, height=1000)

    app = MainController(root)
    app.run()

    root.mainloop()


if __name__ == "__main__":
    main()
