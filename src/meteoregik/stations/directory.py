"""Операции экрана "Справочник станций" над stations/ru_regions: загрузка,
проверка и сохранение правок, добавление станций и регионов. Qt здесь нет —
экран (ui/station_directory.py) только показывает результат и тексты
ошибок DirectoryValidationError.

Удаления нет ни для станций, ни для регионов (решение пользователя
2026-09-25): справочник только пополняется и правится.

Индекс ВМО — ключ, по которому к станции привязаны все данные (rp5_readings,
gg_readings, computed_indicators ссылаются на станцию только через
wmo_index, без FK). Поэтому менять его можно лишь у станции, по которой
данных ещё нет: иначе данные молча "отвязались" бы от станции.

Регион: для российских станций stations.state — код из ru_regions (выбирается
из списка), у остальных стран — произвольный текст (например "by01"). При
смене страны регион сбрасывается: код региона одной страны не имеет смысла
для другой.

Название в rp5_readings/gg_readings (station_name) — кэш на момент заливки,
при переименовании станции не переписывается: экраны берут название из
справочника (stations/choices.py), кэш нужен только если станции в
справочнике нет.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Integer, and_, cast, func, select, union
from sqlalchemy.engine import Connection, Engine

from meteoregik.db.schema import computed_indicators, gg_readings, rp5_readings, ru_regions, stations

RUSSIA = "ru"

# Страны исходного справочника (метеостанции_с_координатами.xlsx) — коды
# хранятся как в файле, на экране показываются названия.
COUNTRY_NAMES: dict[str, str] = {
    "ru": "Россия",
    "am": "Армения",
    "az": "Азербайджан",
    "by": "Беларусь",
    "ee": "Эстония",
    "ge": "Грузия",
    "kg": "Киргизия",
    "kz": "Казахстан",
    "lt": "Литва",
    "lv": "Латвия",
    "md": "Молдова",
    "tj": "Таджикистан",
    "tm": "Туркмения",
    "ua": "Украина",
    "uz": "Узбекистан",
}

MAX_WMO_INDEX = 99999  # индекс ВМО — пятизначный


class DirectoryValidationError(ValueError):
    """Недопустимое значение — текст для пользователя."""


@dataclass
class StationRecord:
    id: int
    wmo_index: int | None
    name: str
    country: str | None
    state: str | None
    lat: float | None
    lon: float | None
    notes: str | None
    updated_at: datetime | None
    region: str | None = None  # название региона РФ по state
    has_data: bool = False  # есть ли по индексу данные (тогда индекс не меняется)

    @property
    def has_coordinates(self) -> bool:
        return self.lat is not None and self.lon is not None


@dataclass
class RegionRecord:
    code: int
    region: str
    updated_at: datetime | None


def country_label(code: str | None) -> str:
    if not code:
        return ""
    return COUNTRY_NAMES.get(code, code)


_REGION_JOIN = and_(stations.c.country == RUSSIA, ru_regions.c.code == cast(stations.c.state, Integer))


def wmo_indexes_with_data(conn: Connection) -> set[int]:
    """Индексы ВМО, по которым есть исходные данные или агрегаты."""
    stmt = union(
        select(rp5_readings.c.wmo_index).distinct(),
        select(gg_readings.c.wmo_index).distinct(),
        select(computed_indicators.c.wmo_index).distinct(),
    )
    return {row[0] for row in conn.execute(stmt) if row[0] is not None}


def load_station_records(engine: Engine) -> list[StationRecord]:
    stmt = (
        select(
            stations.c.id,
            stations.c.wmo_index,
            stations.c.name,
            stations.c.country,
            stations.c.state,
            stations.c.lat,
            stations.c.lon,
            stations.c.notes,
            stations.c.updated_at,
            ru_regions.c.region,
        )
        .select_from(stations.outerjoin(ru_regions, _REGION_JOIN))
        .order_by(stations.c.name, stations.c.wmo_index)
    )
    with engine.connect() as conn:
        with_data = wmo_indexes_with_data(conn)
        records = [StationRecord(**row) for row in conn.execute(stmt).mappings()]
    for record in records:
        record.has_data = record.wmo_index in with_data
    return records


def load_region_records(engine: Engine) -> list[RegionRecord]:
    stmt = select(ru_regions.c.code, ru_regions.c.region, ru_regions.c.updated_at).order_by(ru_regions.c.code)
    with engine.connect() as conn:
        return [RegionRecord(**row) for row in conn.execute(stmt).mappings()]


# --- разбор и проверка значений ---


def _text(raw: object) -> str:
    return "" if raw is None else str(raw).strip()


def parse_name(raw: object) -> str:
    name = _text(raw)
    if not name:
        raise DirectoryValidationError("Название станции не может быть пустым.")
    return name


def parse_wmo_index(raw: object) -> int:
    text = _text(raw)
    if not text.isdigit() or not 0 < int(text) <= MAX_WMO_INDEX:
        raise DirectoryValidationError(f"Индекс ВМО — целое число от 1 до {MAX_WMO_INDEX}, а введено «{text}».")
    return int(text)


def _parse_coordinate(raw: object, what: str, limit: int) -> float | None:
    text = _text(raw).replace(",", ".")
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        raise DirectoryValidationError(f"{what} — число, например 54.08, а введено «{text}».") from None
    if not -limit <= value <= limit:
        raise DirectoryValidationError(f"{what} должна быть от −{limit} до {limit}, а введено {text}.")
    return value


def parse_lat(raw: object) -> float | None:
    return _parse_coordinate(raw, "Широта", 90)


def parse_lon(raw: object) -> float | None:
    return _parse_coordinate(raw, "Долгота", 180)


def parse_country(raw: object) -> str:
    code = _text(raw).lower()
    if not code:
        raise DirectoryValidationError("Укажите страну.")
    return code


def parse_optional_text(raw: object) -> str | None:
    return _text(raw) or None


def _check_ru_region(conn: Connection, state: str | None) -> None:
    if state is None:
        return
    if not state.isdigit() or conn.execute(
        select(ru_regions.c.code).where(ru_regions.c.code == int(state))
    ).scalar_one_or_none() is None:
        raise DirectoryValidationError(f"Региона с кодом «{state}» нет в справочнике регионов РФ.")


def _check_wmo_free(conn: Connection, wmo_index: int, own_id: int | None = None) -> None:
    stmt = select(stations.c.name).where(stations.c.wmo_index == wmo_index)
    if own_id is not None:
        stmt = stmt.where(stations.c.id != own_id)
    other = conn.execute(stmt).scalar_one_or_none()
    if other is not None:
        raise DirectoryValidationError(f"Индекс ВМО {wmo_index} уже занят станцией «{other}».")


# --- правка и добавление станций ---

_PARSERS = {
    "name": parse_name,
    "wmo_index": parse_wmo_index,
    "country": parse_country,
    "state": parse_optional_text,
    "lat": parse_lat,
    "lon": parse_lon,
    "notes": parse_optional_text,
}
EDITABLE_STATION_FIELDS = frozenset(_PARSERS)


def update_station_field(engine: Engine, station_id: int, field: str, raw_value: object) -> dict[str, object]:
    """Проверяет и сохраняет одно поле станции. Возвращает все изменённые
    поля (при смене страны вместе с ней сбрасывается регион). Значение без
    изменений ничего не пишет и не трогает updated_at."""
    if field not in EDITABLE_STATION_FIELDS:
        raise DirectoryValidationError(f"Поле «{field}» не редактируется.")
    value = _PARSERS[field](raw_value)

    with engine.begin() as conn:
        current = conn.execute(select(stations).where(stations.c.id == station_id)).mappings().one()
        if current[field] == value:
            return {}
        changes: dict[str, object] = {field: value}

        if field == "wmo_index":
            if current["wmo_index"] is not None and current["wmo_index"] in wmo_indexes_with_data(conn):
                raise DirectoryValidationError(
                    "Индекс ВМО нельзя изменить: по этой станции уже загружены данные, "
                    "они привязаны к станции именно по индексу."
                )
            _check_wmo_free(conn, value, own_id=station_id)
        elif field == "country":
            if current["state"] is not None:
                changes["state"] = None
        elif field == "state" and current["country"] == RUSSIA:
            _check_ru_region(conn, value)

        conn.execute(
            stations.update()
            .where(stations.c.id == station_id)
            .values(**changes, updated_at=func.current_timestamp())
        )
    return changes


def add_station(
    engine: Engine,
    *,
    wmo_index: object,
    name: object,
    country: object,
    state: object = None,
    lat: object = None,
    lon: object = None,
    notes: object = None,
) -> int:
    """Проверяет и добавляет станцию, возвращает её id. Значения — как ввёл
    пользователь (текст), разбор здесь же, чтобы правила были одни для
    формы добавления и правки в таблице."""
    values = {
        "wmo_index": parse_wmo_index(wmo_index),
        "name": parse_name(name),
        "country": parse_country(country),
        "state": parse_optional_text(state),
        "lat": parse_lat(lat),
        "lon": parse_lon(lon),
        "notes": parse_optional_text(notes),
    }
    with engine.begin() as conn:
        _check_wmo_free(conn, values["wmo_index"])
        if values["country"] == RUSSIA:
            _check_ru_region(conn, values["state"])
        result = conn.execute(stations.insert().values(**values))
        return result.inserted_primary_key[0]


# --- регионы ---


def parse_region_code(raw: object) -> int:
    text = _text(raw)
    if not text.isdigit():
        raise DirectoryValidationError(f"Код региона — целое неотрицательное число, а введено «{text}».")
    return int(text)


def parse_region_name(raw: object) -> str:
    region = _text(raw)
    if not region:
        raise DirectoryValidationError("Название региона не может быть пустым.")
    return region


def update_region_name(engine: Engine, code: int, raw_value: object) -> bool:
    """Сохраняет новое название региона; False — значение не изменилось."""
    region = parse_region_name(raw_value)
    with engine.begin() as conn:
        current = conn.execute(select(ru_regions.c.region).where(ru_regions.c.code == code)).scalar_one()
        if current == region:
            return False
        conn.execute(
            ru_regions.update()
            .where(ru_regions.c.code == code)
            .values(region=region, updated_at=func.current_timestamp())
        )
    return True


def add_region(engine: Engine, *, code: object, region: object) -> int:
    parsed_code = parse_region_code(code)
    parsed_region = parse_region_name(region)
    with engine.begin() as conn:
        existing = conn.execute(
            select(ru_regions.c.region).where(ru_regions.c.code == parsed_code)
        ).scalar_one_or_none()
        if existing is not None:
            raise DirectoryValidationError(f"Код {parsed_code} уже занят регионом «{existing}».")
        conn.execute(ru_regions.insert().values(code=parsed_code, region=parsed_region))
    return parsed_code

