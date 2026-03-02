import sqlite3
from datetime import datetime
from tkinter import messagebox

import pandas as pd


class DatabaseManager:
    def __init__(self, db_path= "data/meteodata.db"):
        self._db_path = db_path
        self._init_database()

    def _init_database(self) -> None:
        with sqlite3.connect(self._db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                '''
                CREATE TABLE IF NOT EXISTS tSourceData_rp5 (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT NOT NULL,
                    dt TIMESTAMP,
                    time_point INTEGER NULL,
                    T_temperature REAL,
                    RRR_precipitation REAL,
                    U_humidity REAL,
                    P0_pressure REAL,
                    P_pressure REAL,
                    Pa_pressure REAL,
                    DD_wind_direction TEXT,
                    Ff_wind_speed REAL,
                    Ff10_wind_speed REAL,
                    Ff3_wind_speed REAL,
                    N_cloudy TEXT,
                    WW_current_weather TEXT,
                    W1_previous_weather TEXT,
                    W2_previous_weather TEXT,
                    Tn_min_temperature REAL,
                    Tx_max_temperature REAL,
                    Cl_cloudy TEXT,
                    Nh_cloudy TEXT,
                    H_height_cloud TEXT,
                    Cm_cloudy TEXT,
                    Ch_cloudy TEXT,
                    VV_visibility_range REAL,
                    Td_dew_point REAL,
                    tR_period_precipitation REAL,
                    E_surface_soil_empty TEXT,
                    Tg_min_temperature_surface_soil REAL,
                    E1_surface_soil_full TEXT,
                    sss_height_snow REAL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                '''
            )

            conn.commit()

    def import_from_excel(self, filepath: str, data_format: str) -> bool | None:
        try:
            df = None
            if data_format == "rp5":
                df = self.parser_rp5_excel(filepath)
            else:
                messagebox.showerror(
                    "Ошибка",
                    f"{data_format} - Неизвестный формат данных. Допустимые варианты: rp5, rgm"
                )

            if df.empty:
                messagebox.showwarning(
                    "Предупреждение",
                    "Файл пуст или не содержит данных"
                )
                return False

            with sqlite3.connect(self._db_path) as conn:
                if data_format == "rp5":
                    rows_count = self.import_rp5_to_sql(df, conn)
                else:
                    messagebox.showerror( "Ошибка",
                        f"{data_format} - Неизвестный формат данных. Допустимые варианты: rp5, rgm" )

                # Логируем импорт
                # now = datetime.now()
                # cursor = conn.cursor()
                # cursor.execute(
                #     "INSERT INTO imported_data (source_file, dt_import) VALUES (?, ?)",
                #     (filepath, now)
                # )
                # conn.commit()

            messagebox.showinfo(
                "Успех",
                f"Данные успешно загружены!\n\n"
                f"Таблица: tSourceData_{data_format}\n"
                f"Строк: {rows_count}\n"
                f"Столбцов: {len( df.columns )}"
            )
            return True

        except Exception as e:
            messagebox.showerror(
                "Ошибка загрузки",
                f"Не удалось загрузить данные:\n{str( e )}"
            )

            print( f"Ошибка: {e}" )

    def parser_rp5_excel( self, filepath: str ) -> pd.DataFrame:
        """
        Парсит Excel файл формата RP5

        - Пропускает строки, начинающиеся с #
        - Первая не-# строка - заголовки
        - Разделяет первый столбец на date и time_point
        """

        # Читаем файл без заголовков, чтобы найти строку с данными
        df_raw = pd.read_excel( filepath, header=None )

        # Находим индекс первой строки, которая НЕ начинается с #
        header_index = None
        for index, row in df_raw.iterrows():
            first_cell = str(row[0]).strip()
            if not first_cell.startswith("#"):
                header_index = index
                break

        if header_index is None:
            raise ValueError("Данные на листе отсутствуют. Все строки являются комментариями, начинаются с #")

        print( f"Заголовки на строке {header_index}" )
        print( f"Данные начинаются со строки {header_index + 1}" )

        # Читаем файл с правильными заголовками
        df = pd.read_excel( filepath, header=header_index )

        # Получаем название первого столбца
        first_col_name = df.columns[0]
        print( f"Первый столбец: '{first_col_name}'" )

        dates = []
        dts = []
        time_points = []

        # Применяем разделение к каждой строке
        for val in df[first_col_name]:
            d, dt, t = self.split_datetime(val)
            dates.append(d)
            dts.append(dt)
            time_points.append(t)

        # Удаляем исходный первый столбец и добавляем новые
        df = df.drop( columns=[ first_col_name ] )
        df.insert(0, "time_point", time_points)
        df.insert(0, "dt", dts)
        df.insert(0, "date", dates)

        return df

    @staticmethod
    def split_datetime( value: str ) -> tuple[str | None, datetime | None, int | None]:
        """
        Разделяет значение типа '01.01.2024 12:00' на date, dt и time_point
        date - текстовая дата
        dt - дата в формате даты
        time_point - число

        Returns:
            tuple: (date, dt, time_point)
        """
        if pd.isna(value):
            return None, None, None

        value_str = str(value).strip()

        parts = value_str.split(" ")

        date = None
        time_point = None
        dt = None

        if len(parts) >= 2:
            date = parts[0]
            time_point = int(parts[1].split(":")[0])
            dd, mm, yyyy = date.split(".")
            dt = datetime(int(yyyy), int(mm), int(dd)).strftime("%Y-%m-%d")
        elif len(parts) == 1:
            date = parts[0]
            dd, mm, yyyy = date.split( "." )
            dt = datetime( int( yyyy ), int( mm ), int( dd ) ).strftime("%Y-%m-%d")

        return date, dt, time_point

    @staticmethod
    def import_rp5_to_sql( df: pd.DataFrame, conn ) -> int:
        """
        Импортирует DataFrame в таблицу tSourceData_rp5

        Returns:
            int: количество добавленных строк
        """
        cursor = conn.cursor()

        inserted_rows_count = 0

        for _, row in df.iterrows():
            cursor.execute(
                """
                INSERT INTO tSourceData_rp5(
                    date,
                    dt,
                    time_point,
                    T_temperature,
                    RRR_precipitation,
                    U_humidity,
                    P0_pressure,
                    P_pressure,
                    Pa_pressure,
                    DD_wind_direction,
                    Ff_wind_speed,
                    ff10_wind_speed,
                    ff3_wind_speed,
                    N_cloudy,
                    WW_current_weather,
                    W1_previous_weather,
                    W2_previous_weather,
                    Tn_min_temperature,
                    Tx_max_temperature,
                    Cl_cloudy,
                    Nh_cloudy,
                    H_height_cloud,
                    Cm_cloudy,
                    Ch_cloudy,
                    VV_visibility_range,
                    Td_dew_point,
                    tR_period_precipitation,
                    E_surface_soil_empty,
                    Tg_min_temperature_surface_soil,
                    E1_surface_soil_full,
                    sss_height_snow
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    row.get("date"),
                    row.get("dt"),
                    row.get("time_point"),
                    row.get("T"),
                    row.get("RRR"),
                    row.get("U"),
                    row.get("Po"),
                    row.get("P"),
                    row.get("Pa"),
                    row.get("DD"),
                    row.get("Ff"),
                    row.get("ff10"),
                    row.get("ff3"),
                    row.get("N"),
                    row.get("WW"),
                    row.get("W1"),
                    row.get("W2"),
                    row.get("Tn"),
                    row.get("Tx"),
                    row.get("Cl"),
                    row.get("Nh"),
                    row.get("H"),
                    row.get("Cm"),
                    row.get("Ch"),
                    row.get("VV"),
                    row.get("Td"),
                    row.get("tR"),
                    row.get("E"),
                    row.get("Tg"),
                    row.get("E'"),
                    row.get("sss")
                )
            )
            inserted_rows_count += 1
        conn.commit()

        return inserted_rows_count

db_manager = DatabaseManager()
