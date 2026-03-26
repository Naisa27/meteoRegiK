from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.data.db_base import Base


class SourceDataRp5Orm(Base):
    """Модель таблицы tSourceData_rp5"""

    __tablename__ = "tSourceData_rp5"

    # Первичный ключ
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # название метеостанции
    station_name: Mapped[str] = mapped_column(Text, nullable=False)

    # Дата и время
    date: Mapped[str] = mapped_column(Text, nullable=False)
    dt: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    time_point: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Температура и осадки
    T_temperature: Mapped[float | None] = mapped_column(Float, nullable=True)
    RRR_precipitation: Mapped[str | None] = mapped_column(Text, nullable=True)
    U_humidity: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Давление
    Po_pressure: Mapped[float | None] = mapped_column(Float, nullable=True)
    P_pressure: Mapped[float | None] = mapped_column(Float, nullable=True)
    Pa_pressure: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Ветер
    DD_wind_direction: Mapped[str | None] = mapped_column(Text, nullable=True)
    Ff_wind_speed: Mapped[float | None] = mapped_column(Float, nullable=True)
    Ff10_wind_speed: Mapped[float | None] = mapped_column(Float, nullable=True)
    Ff3_wind_speed: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Облачность и погода
    N_cloudy: Mapped[str | None] = mapped_column(Text, nullable=True)
    WW_current_weather: Mapped[str | None] = mapped_column(Text, nullable=True)
    W1_previous_weather: Mapped[str | None] = mapped_column(Text, nullable=True)
    W2_previous_weather: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Температура мин/макс
    Tn_min_temperature: Mapped[float | None] = mapped_column(Float, nullable=True)
    Tx_max_temperature: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Облачность детальная
    Cl_cloudy: Mapped[str | None] = mapped_column(Text, nullable=True)
    Nh_cloudy: Mapped[str | None] = mapped_column(Text, nullable=True)
    H_height_cloud: Mapped[str | None] = mapped_column(Text, nullable=True)
    Cm_cloudy: Mapped[str | None] = mapped_column(Text, nullable=True)
    Ch_cloudy: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Видимость и точка росы
    VV_visibility_range: Mapped[str | None] = mapped_column(Text, nullable=True)
    Td_dew_point: Mapped[float | None] = mapped_column(Float, nullable=True)
    tR_period_precipitation: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Состояние поверхности
    E_surface_soil_empty: Mapped[str | None] = mapped_column(Text, nullable=True)
    Tg_min_temperature_surface_soil: Mapped[float | None] = mapped_column(
        Float, nullable=True
    )
    E1_surface_soil_full: Mapped[str | None] = mapped_column(Text, nullable=True)
    sss_height_snow: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Метаданные
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.now
    )
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    isActive: Mapped[bool | None] = mapped_column(Boolean, nullable=False, default=True)

    # def __str__(self):
    #     return str(self.__dict__)
