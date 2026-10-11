"""DDL таблиц (SQLAlchemy Core), соответствующее разделу "Модель данных" в
.claude/docs/plan.md. Миграции применяются через db/migrations.py.
"""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
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
    # Без FK на stations.id — станция идентифицируется wmo_index напрямую
    # ("Индекс ВМО" в файле ГГ), тем же паттерном, что и rp5_readings (см.
    # plan.md, п.11 решений: пересмотрено 2026-09-19 — station_id как FK на
    # автоинкрементный PK stations давал в этом поле "случайные" числа,
    # непонятные при прямом просмотре данных). station_name кэшируется для
    # читаемости.
    Column("wmo_index", Integer, nullable=False),
    Column("station_name", Text, nullable=True),
    Column("obs_date", Date, nullable=False),
    Column("t_avg", Float, nullable=True),
    Column("t_min", Float, nullable=True),
    Column("t_max", Float, nullable=True),
    Column("rrr_precipitation", Float, nullable=True),
    Column("source_file", Text, nullable=True),
    Column("created_at", DateTime, nullable=False, server_default=func.current_timestamp()),
    Column("updated_at", DateTime, nullable=True),
    UniqueConstraint("wmo_index", "obs_date", name="uq_gg_readings_key"),
)

computed_indicators = Table(
    "computed_indicators",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    # wmo_index, не station_id — см. комментарий у gg_readings выше.
    Column("wmo_index", Integer, nullable=False),
    Column("period_type", Text, nullable=False),  # 'day' | 'decade' | 'month' | 'year' | 'season'
    Column("period_start", Date, nullable=False),
    Column("period_end", Date, nullable=False),
    Column("parameter", Text, nullable=False),  # 'T', позже 'RRR' и др.
    Column("stat_type", Text, nullable=False),  # 'avg' | 'min' | 'max'
    Column("value", Float, nullable=True),
    # Файл, давший текущее значение (для day — конкретный исходный файл
    # rp5/ГГ; для decade/month/year — NULL, единого файла-источника у
    # агрегата нет). Нужен calculations/recompute.py для диалога разрешения
    # конфликтов, когда пересчёт по новым данным расходится с уже
    # сохранённым значением (см. plan.md, правки от 2026-09-19).
    Column("source_file", Text, nullable=True),
    Column("created_at", DateTime, nullable=False, server_default=func.current_timestamp()),
    UniqueConstraint(
        "wmo_index", "period_type", "period_start", "period_end", "parameter", "stat_type",
        name="uq_computed_indicators_key",
    ),
)

# --- сохранённый расчёт отклонений от нормы (миграция 6, calculations/anomalies.py) ---

# Нормы станции. period_type: 'month' (month = 1–12), 'decade' (month и
# decade = 1–3), 'season' (month — первый месяц сезона: 12, 3, 6 или 9;
# только осадки) или 'year' (month = decade = 0). Нули вместо NULL — чтобы
# уникальность работала (в SQLite NULL в UNIQUE не равен NULL).
station_norms = Table(
    "station_norms",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("wmo_index", Integer, nullable=False),
    Column("parameter", Text, nullable=False),
    Column("period_type", Text, nullable=False),
    Column("month", Integer, nullable=False),
    Column("decade", Integer, nullable=False),
    Column("value", Float, nullable=False),
    Column("created_at", DateTime, nullable=False, server_default=func.current_timestamp()),
    UniqueConstraint("wmo_index", "parameter", "period_type", "month", "decade", name="uq_station_norms_key"),
)

# Полнота месяца и года: без неё средняя неполного периода выдавалась бы за
# настоящую (правило ВМО "3/5" по месяцу нельзя восстановить по числу дней —
# важны и пропуски подряд, поэтому признак хранится, а не выводится).
period_completeness = Table(
    "period_completeness",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("wmo_index", Integer, nullable=False),
    Column("parameter", Text, nullable=False),
    Column("period_type", Text, nullable=False),  # 'month' | 'year' | 'season' (осадки)
    Column("period_start", Date, nullable=False),
    Column("days_with_data", Integer, nullable=False),
    Column("complete", Boolean, nullable=False),
    Column("created_at", DateTime, nullable=False, server_default=func.current_timestamp()),
    UniqueConstraint("wmo_index", "parameter", "period_type", "period_start", name="uq_period_completeness_key"),
)

# Состояние расчёта станции. Строки нет (или rule_version старая) — нормы и
# отклонения станции устарели и требуют расчёта: calculations/recompute.py
# удаляет строку при любом изменении суточных значений станции.
anomaly_state = Table(
    "anomaly_state",
    metadata,
    Column("wmo_index", Integer, primary_key=True, autoincrement=False),
    Column("calculated_at", DateTime, nullable=False),
    Column("rule_version", Integer, nullable=False),
    Column("created_at", DateTime, nullable=False, server_default=func.current_timestamp()),
)

# База нормы станции по параметру (миграция 8; до неё — столбцы anomaly_state,
# когда параметр был один): полнота у температуры и осадков считается
# по-разному, и полных лет у них разное число — у одной станции температура
# может иметь норму ВМО, а осадки — только "по архиву".
# standard: True — ВМО 1991–2020, False — весь архив; years = 0 и пустые
# границы — полных лет нет, годовой нормы нет.
norm_bases = Table(
    "norm_bases",
    metadata,
    Column("wmo_index", Integer, primary_key=True, autoincrement=False),
    Column("parameter", Text, primary_key=True),
    Column("standard", Boolean, nullable=False),
    Column("first_year", Integer, nullable=True),
    Column("last_year", Integer, nullable=True),
    Column("years", Integer, nullable=False),
    Column("created_at", DateTime, nullable=False, server_default=func.current_timestamp()),
)
