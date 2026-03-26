from datetime import datetime
from tkinter import messagebox
from typing import Any

import pandas as pd

from src.data.database import Database
from src.data.db_manager import DBManager
from src.schemas.import_result import ImportResult
from src.schemas.source_data_rp5 import SourceDataRp5Add


class DataImport(Database):
    def __init__(self):
        super().__init__()
        self.station_name = None

    def import_from_excel(
        self, filepath: str, data_format: str, progress_callback=None
    ) -> ImportResult | None:
        try:
            df = None
            if data_format == "rp5":
                df = self.parser_rp5_excel(filepath)
            elif data_format == "rgm":
                """
                будет парсер excel формата rgm
                """
            else:
                messagebox.showerror(
                    "Ошибка",
                    f"Парсинг невозможен. {data_format} - Неизвестный формат данных. Допустимые варианты: rp5, rgm",
                )
                return ImportResult(
                    success=False,
                    errors=[
                        f"Неизвестный формат: {data_format}. Распарсить невозможно."
                    ],
                )

            if df.empty:
                messagebox.showwarning(
                    "Предупреждение", "Файл пуст или не содержит данных"
                )
                return ImportResult(
                    success=False, errors=["Файл пуст или не содержит данных"]
                )

            if data_format == "rp5":
                return self._import_rp5_to_sql(df, data_format, progress_callback)
            elif data_format == "rgm":
                """
                будет импорт данных формата rgm
                """
                return ImportResult(
                    success=False, errors=["Пока еще обработчик не написан"]
                )

        except Exception as e:
            messagebox.showerror(
                "Ошибка загрузки", f"Не удалось загрузить данные:\n{e!s}"
            )
            import_result = ImportResult(
                success=False, errors=[f"Не удалось загрузить данные:\n{e!s}"]
            )
            print(import_result)

            return import_result

            print(f"Ошибка: {e}")

    def parser_rp5_excel(self, filepath: str) -> pd.DataFrame:
        """
        Парсит Excel файл формата RP5

        - Пропускает строки, начинающиеся с #
        - Первая не-# строка - заголовки
        - Разделяет первый столбец на date и time_point
        """

        # Читаем файл без заголовков, чтобы найти строку с данными
        df_raw = pd.read_excel(filepath, header=None)

        # Находим индекс первой строки, которая НЕ начинается с #
        header_index = None
        for index, row in df_raw.iterrows():
            # из первой строки берем название метеостанции
            if index == 0:
                self.station_name = self.get_station_name(row[0])
            first_cell = str(row[0]).strip()
            if not first_cell.startswith("#"):
                header_index = index
                break

        if header_index is None:
            raise ValueError(
                "Данные на листе отсутствуют. Все строки являются комментариями, начинаются с #"
            )

        print(f"Заголовки на строке {header_index}")
        print(f"Данные начинаются со строки {header_index + 1}")

        # Читаем файл с правильными заголовками
        df = pd.read_excel(filepath, header=header_index)

        # Получаем название первого столбца
        first_col_name = df.columns[0]
        print(f"Первый столбец: '{first_col_name}'")

        dates, dts, time_points = [], [], []

        # Применяем разделение к каждой строке
        for val in df[first_col_name]:
            d, dt, t = self.split_datetime(val)
            dates.append(d)
            dts.append(dt)
            time_points.append(t)

        # Удаляем исходный первый столбец и добавляем новые
        df = df.drop(columns=[first_col_name])
        df.insert(0, "time_point", time_points)
        df.insert(0, "dt", dts)
        df.insert(0, "date", dates)
        df.insert(0, "station_name", self.station_name)

        return df

    @staticmethod
    def split_datetime(value: str) -> tuple[str | None, datetime | None, int | None]:
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
            dd, mm, yyyy = date.split(".")
            dt = datetime(int(yyyy), int(mm), int(dd)).strftime("%Y-%m-%d")

        return date, dt, time_point

    @staticmethod
    def get_station_name(value: str) -> str:
        value_split = value.replace("#", "").strip().split(",")[0]
        value_name = " ".join(value_split.split(" ")[1:])
        return value_name

    def _import_rp5_to_sql(
        self, df: pd.DataFrame, data_format: str, progress_callback=None
    ) -> ImportResult:
        """
        Импортирует DataFrame в таблицу tSourceData_rp5

        Returns:
            ImportResult: схема
        """
        errors: list[str] = []
        dt = []

        total_rows = len(df)

        if total_rows == 0:
            message = "Файл пуст. нет данных для заливки"
            messagebox.showinfo(title="Импорт даже не начинался", message=message)
            errors.append(message)
            import_result = ImportResult(
                success=False,
                table_name="tSourceData_rp5",
                rows_imported=0,
                columns_count=0,
                errors=errors,
            )
            return import_result

        with DBManager(self._session_factory) as db:
            data_exists = db.rp5FindDouble.get_filtered(station_name=self.station_name)
            data_exists_detail = []

            if len(data_exists) == total_rows:
                message = (
                    f"Все строки в количестве {len(data_exists)} уже существуют в БД"
                )
                messagebox.showinfo(title="Импорт даже не начинался", message=message)
                errors.append(message)
                import_result = ImportResult(
                    success=False,
                    table_name="tSourceData_rp5",
                    rows_imported=0,
                    columns_count=0,
                    errors=errors,
                )
                return import_result

            data_list = []

            for ind, row in df.iterrows():
                try:
                    dt.append(self.clean_excel_value(row.get("dt")))

                    data = SourceDataRp5Add(
                        station_name=self.clean_excel_value(row.get("station_name")),
                        date=self.clean_excel_value(row.get("date")),
                        dt=self.clean_excel_value(row.get("dt")),
                        time_point=self.clean_excel_value(row.get("time_point")),
                        T_temperature=self.clean_excel_value(row.get("T")),
                        RRR_precipitation=self.get_str_value(row.get("RRR")),
                        U_humidity=self.clean_excel_value(row.get("U")),
                        Po_pressure=self.clean_excel_value(row.get("Po")),
                        P_pressure=self.clean_excel_value(row.get("P")),
                        Pa_pressure=self.clean_excel_value(row.get("Pa")),
                        DD_wind_direction=self.get_str_value(row.get("DD")),
                        Ff_wind_speed=self.clean_excel_value(row.get("Ff")),
                        Ff10_wind_speed=self.clean_excel_value(row.get("ff10")),
                        Ff3_wind_speed=self.clean_excel_value(row.get("ff3")),
                        N_cloudy=self.get_str_value(row.get("N")),
                        WW_current_weather=self.get_str_value(row.get("WW")),
                        W1_previous_weather=self.get_str_value(row.get("W1")),
                        W2_previous_weather=self.get_str_value(row.get("W2")),
                        Tn_min_temperature=self.clean_excel_value(row.get("Tn")),
                        Tx_max_temperature=self.clean_excel_value(row.get("Tx")),
                        Cl_cloudy=self.get_str_value(row.get("Cl")),
                        Nh_cloudy=self.get_str_value(row.get("Nh")),
                        H_height_cloud=self.get_str_value(row.get("H")),
                        Cm_cloudy=self.get_str_value(row.get("Cm")),
                        Ch_cloudy=self.get_str_value(row.get("Ch")),
                        VV_visibility_range=self.get_str_value(row.get("VV")),
                        Td_dew_point=self.clean_excel_value(row.get("Td")),
                        tR_period_precipitation=self.clean_excel_value(row.get("tR")),
                        E_surface_soil_empty=self.get_str_value(row.get("E")),
                        Tg_min_temperature_surface_soil=self.clean_excel_value(
                            row.get("Tg")
                        ),
                        E1_surface_soil_full=self.get_str_value(row.get("E'")),
                        sss_height_snow=self.get_str_value(row.get("sss")),
                    )
                    if [
                        item
                        for item in data_exists
                        if item.dt == data.dt and item.time_point == data.time_point
                    ]:
                        data_exists_detail.append(
                            {
                                "dt": data.dt,
                                "time_point": data.time_point,
                            }
                        )
                    else:
                        data_list.append(data)
                    # db.rp5.add(data)
                    # db.commit()

                except Exception as e:
                    errors.append(f"Ошибка валидации строки {ind}: {e}")

                # Обновление прогресса
                if progress_callback:
                    progress_callback(ind + 1, total_rows)

            if errors:
                import_result = ImportResult(
                    success=False,
                    table_name="tSourceData_rp5",
                    rows_imported=0,
                    columns_count=len(df.columns),
                    error_row=len(errors),
                    errors=errors,
                )
                print(f"_import_rp5_to_sql = {import_result}")
                return import_result

            if not data_list:
                message = f"Все строки в количестве {len(data_exists_detail)} уже существуют в БД"
                messagebox.showinfo(title="Импорт даже не начинался", message=message)
                errors.append(message)
                import_result = ImportResult(
                    success=False,
                    table_name="tSourceData_rp5",
                    rows_imported=0,
                    columns_count=0,
                    errors=errors,
                )
                return import_result

            try:
                db.rp5.bulk_add(data_list)
                db.commit()
            except Exception as e:
                errors.append(f"Ошибка вставки данных массивом: {e}")
                import_result = ImportResult(
                    success=False,
                    table_name="tSourceData_rp5",
                    rows_imported=0,
                    columns_count=len(df.columns),
                    errors=errors,
                )
                print(f"_import_rp5_to_sql = {import_result}")
                return import_result

        import_result = ImportResult(
            success=True,
            table_name="tSourceData_rp5",
            rows_imported=len(data_list),
            columns_count=len(df.columns),
            rows_doubles=len(data_exists_detail),
            errors=errors,
        )
        print(f"_import_rp5_to_sql = {import_result}")
        min_dt = datetime.strptime(min(dt), "%Y-%m-%d").strftime("%d.%m.%Y")
        max_dt = datetime.strptime(max(dt), "%Y-%m-%d").strftime("%d.%m.%Y")
        message = f"""
        метеостанция {self.station_name}\n
        формат данных: {data_format}\n
        период с {min_dt} по {max_dt}\n
        {import_result.rows_imported} строк\n
        {import_result.columns_count} столбцов\n
        {"количество дублей - " + str(len(data_exists_detail)) + " строк" if data_exists_detail else "дублей нет"}
        таблица {import_result.table_name}
        """
        messagebox.showinfo(title="Импорт завершён", message=message)

        return import_result

    @staticmethod
    def clean_excel_value(value: Any) -> Any:
        """
        Преобразует пустые значения из Excel в None.
        """
        if value is None:
            return None
        if isinstance(value, str) and value.strip() == "":
            return None
        if pd.isna(value):  # Работает и с float nan, и с np.nan
            return None
        return value

    def get_str_value(self, value: Any) -> str | None:
        return str(value) if self.clean_excel_value(value) else None


data_import = DataImport()
