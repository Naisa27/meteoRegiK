from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

class SourceDataRp5Base(BaseModel):
    """ Базовая схема данных   """

    station_name: str = Field( ... )
    date: str = Field(...)
    dt: datetime | None = None
    time_point: int | None = Field(None, ge=0, le=23)
    T_temperature: float | None = None
    RRR_precipitation: str | None = None
    U_humidity: float | None = Field(None, ge=0, le=100)
    Po_pressure: float | None = None
    P_pressure: float | None = None
    Pa_pressure: float | None = None
    DD_wind_direction: str | None = None
    Ff_wind_speed: float | None = Field(None, ge=0)
    Ff10_wind_speed: float | None = Field(None, ge=0)
    Ff3_wind_speed: float | None = Field(None, ge=0)
    N_cloudy: str | None = None
    WW_current_weather: str | None = None
    W1_previous_weather: str | None = None
    W2_previous_weather: str | None = None
    Tn_min_temperature: float | None = None
    Tx_max_temperature: float | None = None
    Cl_cloudy: str | None = None
    Nh_cloudy: str | None = None
    H_height_cloud: str | None = None
    Cm_cloudy: str | None = None
    Ch_cloudy: str | None = None
    VV_visibility_range: str | None = None
    Td_dew_point: float | None = None
    tR_period_precipitation: float | None = Field(None, ge=0)
    E_surface_soil_empty: str | None = None
    Tg_min_temperature_surface_soil: float | None = None
    E1_surface_soil_full: str | None = None
    sss_height_snow: str | None = None


class SourceDataRp5Add(SourceDataRp5Base):
    pass


class SourceDataRp5(SourceDataRp5Base):
    """ Полная схема данных """
    id: int
    created_at: datetime = Field( ... )
    updated_at: datetime | None = None
    isActive: bool = Field( ... )

    model_config = ConfigDict( from_attributes=True )


class SourceDataRp5FindDouble(BaseModel):
    station_name: str = Field( ... )
    dt: datetime = Field( ... )
    time_point: int = Field( ... )

    model_config = ConfigDict( from_attributes=True )

    def __str__(self):
        return f"{self.__dict__}"

