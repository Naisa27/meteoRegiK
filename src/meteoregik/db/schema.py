"""DDL таблиц (SQLAlchemy Core), соответствующее разделу "Модель данных" в
.claude/docs/plan.md. Миграции применяются через db/migrations.py.
"""

from __future__ import annotations

from sqlalchemy import (
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    MetaData,
    Table,
    Text,
    Time,
    UniqueConstraint,
    func,
)

metadata = MetaData()

schema_version = Table(
    "schema_version",
    metadata,
    Column("version", Integer, primary_key=True),
    Column("applied_at", DateTime, nullable=False, server_default=func.current_timestamp()),
)

stations = Table(
    "stations",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("name", Text, nullable=False),
    Column("wmo_index", Integer, unique=True, nullable=True),
    Column("country", Text, nullable=True),
    # state: для РФ — код, совпадающий с ru_regions.code; для остальных стран —
    # "<country><номер>"; "none" из исходного файла -> NULL (см. plan.md).
    Column("state", Text, nullable=True),
    Column("lat", Float, nullable=True),
    Column("lon", Float, nullable=True),
    Column("notes", Text, nullable=True),
    Column("created_at", DateTime, nullable=False, server_default=func.current_timestamp()),
    Column("updated_at", DateTime, nullable=True),
)

ru_regions = Table(
    "ru_regions",
    metadata,
    Column("code", Integer, primary_key=True),
    Column("region", Text, nullable=False),
    Column("created_at", DateTime, nullable=False, server_default=func.current_timestamp()),
    Column("updated_at", DateTime, nullable=True),
)

# Стандартный набор параметров rp5.ru (имя колонки = столбец в файле + суффикс
# параметра в одно слово). Точный состав конкретного файла может отличаться —
# сопоставление в rp5_importer.py идёт по имени столбца, не по позиции.
# Зафиксировано по факту разбора .claude/docs/Мосальск_2005-2025_rp5.xls
# (см. plan.md): добавлен ff3 (порыв ветра между сроками, отсутствовал в
# первоначальной раскладке); RRR/VV/sss переведены из Float в Text — реальные
# выгрузки rp5.ru кладут туда текстовые значения ("Осадков нет", "Следы
# осадков" и т.п.) наравне с числами.
RP5_PARAMETER_COLUMNS: list[tuple[str, type]] = [
    ("T_temperature", Float),
    ("Po_pressure_station", Float),
    ("P_pressure_sealevel", Float),
    ("Pa_pressure_tendency", Float),
    ("U_humidity", Float),
    ("DD_wind_direction", Text),
    ("Ff_wind_speed", Float),
    ("ff10_wind_gust", Float),
    ("ff3_wind_gust", Float),
    ("N_cloudiness_total", Text),
    ("WW_weather_present", Text),
    ("W1_weather_past1", Text),
    ("W2_weather_past2", Text),
    ("Tn_temperature_min", Float),
    ("Tx_temperature_max", Float),
    ("Cl_clouds_low", Text),
    ("Nh_cloud_amount", Text),
    ("H_cloud_base_height", Text),
    ("Cm_clouds_mid", Text),
    ("Ch_clouds_high", Text),
    ("VV_visibility", Text),
    ("Td_dewpoint", Float),
    ("RRR_precipitation", Text),
    ("tR_precipitation_period", Float),
    ("E_soil_state", Text),
    ("Tg_soil_temp_min", Float),
    ("E2_soil_state_snow", Text),
    ("sss_snow_depth", Text),
]

rp5_readings = Table(
    "rp5_readings",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    # Без FK на stations.id — wmo_index/station_name парсятся напрямую из шапки
    # файла и однозначно идентифицируют станцию (см. plan.md, п. 11 решений).
    Column("wmo_index", Integer, nullable=True),
    Column("station_name", Text, nullable=True),
    Column("obs_date", Date, nullable=False),
    Column("obs_time", Time, nullable=False),
    Column("source_file", Text, nullable=True),
    *(Column(name, col_type, nullable=True) for name, col_type in RP5_PARAMETER_COLUMNS),
    Column("created_at", DateTime, nullable=False, server_default=func.current_timestamp()),
    Column("updated_at", DateTime, nullable=True),
    UniqueConstraint("wmo_index", "obs_date", "obs_time", name="uq_rp5_readings_key"),
)

gg_readings = Table(
    "gg_readings",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    # station_id — единственная связь со stations (в файле ГГ нет ни названия,
    # ни wmo_index, только "Индекс ВМО"); station_name кэшируется для читаемости.
    Column("station_id", Integer, ForeignKey("stations.id"), nullable=False),
    Column("station_name", Text, nullable=True),
    Column("obs_date", Date, nullable=False),
    Column("t_avg", Float, nullable=True),
    Column("t_min", Float, nullable=True),
    Column("t_max", Float, nullable=True),
    Column("rrr_precipitation", Float, nullable=True),
    Column("source_file", Text, nullable=True),
    Column("created_at", DateTime, nullable=False, server_default=func.current_timestamp()),
    Column("updated_at", DateTime, nullable=True),
    UniqueConstraint("station_id", "obs_date", name="uq_gg_readings_key"),
)

computed_indicators = Table(
    "computed_indicators",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("station_id", Integer, ForeignKey("stations.id"), nullable=False),
    Column("period_type", Text, nullable=False),  # 'day' | 'decade' | 'month' | 'year'
    Column("period_start", Date, nullable=False),
    Column("period_end", Date, nullable=False),
    Column("parameter", Text, nullable=False),  # 'T', позже 'RRR' и др.
    Column("stat_type", Text, nullable=False),  # 'avg' | 'min' | 'max'
    Column("value", Float, nullable=True),
    Column("created_at", DateTime, nullable=False, server_default=func.current_timestamp()),
)
