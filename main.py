# import tkinter as tk
from tkinter import *
from tkinter import ttk, filedialog
from tkinter.messagebox import showinfo, showwarning, showerror, askyesno

from controllers.main_controller import MainController


# Диалоговые окна   следующий


def main():
    root = Tk()
    icon = PhotoImage( file="static/img/cloud-sun.png" )
    root.iconphoto( False, icon)
    root.title( "MeteoRegiK" )
    root.geometry( "800x600" )
    root.minsize(width=200, height=200)
    root.maxsize(width=1000, height=1000)



    app = MainController( root )
    app.run()

    root.mainloop()


if __name__ == "__main__":
    main()
