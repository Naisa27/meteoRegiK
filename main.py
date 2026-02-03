# import tkinter as tk
from tkinter import *
from tkinter import ttk, messagebox

from controllers.main_controller import MainController


# Виджет Text   следующий

# почему то не отображаются картинки

def main():
    root = Tk()
    icon = PhotoImage( file="static/img/cloud-sun.png" )
    root.iconphoto( False, icon)
    root.title( "MeteoRegiK" )
    root.geometry( "800x600" )
    root.minsize(width=200, height=200)
    root.maxsize(width=1000, height=1000)

    """
    logo = PhotoImage( file="static/img/snowman-alt_24.png")
    title_label = ttk.Label(
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

    frame = ttk.Frame( borderwidth=1,
        relief=SOLID,
        padding=[8, 10] )

    notebook = ttk.Notebook( frame, height=200 )
    notebook.pack(expand=True, fill=BOTH)

    frame1 = ttk.Frame( notebook )
    frame2 = ttk.Frame( notebook )

    frame1.pack(fill=BOTH, expand=True)
    frame2.pack(fill=BOTH, expand=True)

    python_logo = PhotoImage( file="static/img/cloud-sun.png" )
    java_logo = PhotoImage( file="static/img/snowflake.png" )

    notebook.add(frame1, text="Исходные данные", image=python_logo, compound=LEFT)
    notebook.add(frame2, text="Расчёты", image=java_logo, compound=RIGHT)

    frame.pack( anchor=NW,
        fill=X,
        padx=5,
        pady=5 )
        
    """

    app = MainController( root )
    app.run()

    root.mainloop()


if __name__ == "__main__":
    main()
