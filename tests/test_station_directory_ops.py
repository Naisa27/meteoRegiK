"""Операции справочника станций/регионов без интерфейса (stations/directory.py):
загрузка, проверки, правка и добавление."""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select

from meteoregik.db.migrations import run_migrations
from meteoregik.db.schema import computed_indicators, gg_readings, ru_regions, stations
from meteoregik.db.session import get_engine
from meteoregik.stations.directory import (
    DirectoryValidationError,
    add_region,
    add_station,
    country_label,
    load_region_records,
    load_station_records,
    update_region_name,
    update_station_field,
)


@pytest.fixture
def engine(tmp_path):
    engine = get_engine(tmp_path / "test.sqlite3")
    run_migrations(engine)
    with engine.begin() as conn:
        conn.execute(ru_regions.insert(), [{"code": 40, "region": "Калужская область"}, {"code": 32, "region": "Брянская область"}])
        conn.execute(
            stations.insert(),
            [
                {"name": "Мосальск", "wmo_index": 27704, "country": "ru", "state": "40", "lat": 54.5, "lon": 34.98},
                {"name": "Минск", "wmo_index": 26850, "country": "by", "state": "by01", "lat": 53.9, "lon": 27.5},
                {"name": "Новая", "wmo_index": 11111, "country": "ru", "state": None, "lat": None, "lon": None},
            ],
        )
        # по Мосальску есть данные — его индекс менять нельзя
        conn.execute(gg_readings.insert().values(wmo_index=27704, obs_date=date(2020, 1, 1), t_avg=1.0))
    return engine


def _station(engine, wmo_index):
    with engine.connect() as conn:
        return conn.execute(select(stations).where(stations.c.wmo_index == wmo_index)).mappings().one()


def test_load_station_records_resolves_region_and_data_flag(engine):
    records = {r.wmo_index: r for r in load_station_records(engine)}
    assert records[27704].region == "Калужская область"
    assert records[27704].has_data is True
    assert records[26850].region is None  # у не-российских станций регион — текст state
    assert records[26850].state == "by01"
    assert records[11111].has_data is False
    assert records[11111].has_coordinates is False


def test_data_flag_counts_computed_indicators_too(engine):
    with engine.begin() as conn:
        conn.execute(
            computed_indicators.insert().values(
                wmo_index=11111, period_type="day", period_start=date(2020, 1, 1),
                period_end=date(2020, 1, 1), parameter="T", stat_type="avg", value=1.0,
            )
        )
    records = {r.wmo_index: r for r in load_station_records(engine)}
    assert records[11111].has_data is True


def test_country_label():
    assert country_label("ru") == "Россия"
    assert country_label("xx") == "xx"
    assert country_label(None) == ""


# --- правка станции ---


def test_update_name_sets_updated_at(engine):
    station = _station(engine, 27704)
    changes = update_station_field(engine, station["id"], "name", "  Мосальск-2 ")
    assert changes == {"name": "Мосальск-2"}
    updated = _station(engine, 27704)
    assert updated["name"] == "Мосальск-2"
    assert updated["updated_at"] is not None


def test_update_same_value_writes_nothing(engine):
    station = _station(engine, 27704)
    assert update_station_field(engine, station["id"], "name", "Мосальск") == {}
    assert _station(engine, 27704)["updated_at"] is None


@pytest.mark.parametrize(
    ("field", "raw", "message"),
    [
        ("name", "   ", "Название станции не может быть пустым"),
        ("lat", "95", "Широта должна быть от"),
        ("lon", "абв", "Долгота — число"),
        ("wmo_index", "12a", "Индекс ВМО — целое число"),
        ("wmo_index", "100000", "Индекс ВМО — целое число"),
        ("country", "", "Укажите страну"),
    ],
)
def test_update_rejects_invalid_values(engine, field, raw, message):
    station = _station(engine, 11111)
    with pytest.raises(DirectoryValidationError, match=message):
        update_station_field(engine, station["id"], field, raw)


def test_coordinates_accept_comma_and_empty(engine):
    station = _station(engine, 11111)
    update_station_field(engine, station["id"], "lat", "54,08")
    assert _station(engine, 11111)["lat"] == 54.08
    update_station_field(engine, station["id"], "lat", "")
    assert _station(engine, 11111)["lat"] is None


def test_wmo_index_locked_when_station_has_data(engine):
    station = _station(engine, 27704)
    with pytest.raises(DirectoryValidationError, match="уже загружены данные"):
        update_station_field(engine, station["id"], "wmo_index", "27705")


def test_wmo_index_editable_without_data_but_must_be_unique(engine):
    station = _station(engine, 11111)
    with pytest.raises(DirectoryValidationError, match="уже занят станцией «Минск»"):
        update_station_field(engine, station["id"], "wmo_index", "26850")
    update_station_field(engine, station["id"], "wmo_index", "11112")
    assert _station(engine, 11112)["name"] == "Новая"


def test_ru_region_must_exist(engine):
    station = _station(engine, 11111)
    with pytest.raises(DirectoryValidationError, match="Региона с кодом «99» нет"):
        update_station_field(engine, station["id"], "state", "99")
    update_station_field(engine, station["id"], "state", "32")
    assert _station(engine, 11111)["state"] == "32"


def test_foreign_state_is_free_text(engine):
    station = _station(engine, 26850)
    update_station_field(engine, station["id"], "state", "by02")
    assert _station(engine, 26850)["state"] == "by02"


def test_changing_country_resets_region(engine):
    station = _station(engine, 27704)
    changes = update_station_field(engine, station["id"], "country", "BY")
    assert changes == {"country": "by", "state": None}
    assert _station(engine, 27704)["state"] is None


# --- добавление станции ---


def test_add_station(engine):
    add_station(engine, wmo_index="27707", name="Сухиничи", country="ru", state="40", lat="54,1", lon="35.35")
    station = _station(engine, 27707)
    assert (station["name"], station["state"], station["lat"], station["lon"]) == ("Сухиничи", "40", 54.1, 35.35)


@pytest.mark.parametrize(
    ("values", "message"),
    [
        ({"wmo_index": "27704", "name": "Дубль", "country": "ru"}, "уже занят станцией «Мосальск»"),
        ({"wmo_index": "", "name": "Без индекса", "country": "ru"}, "Индекс ВМО"),
        ({"wmo_index": "27707", "name": "", "country": "ru"}, "Название станции"),
        ({"wmo_index": "27707", "name": "Х", "country": "ru", "state": "77"}, "Региона с кодом «77» нет"),
    ],
)
def test_add_station_rejects_invalid(engine, values, message):
    with pytest.raises(DirectoryValidationError, match=message):
        add_station(engine, **values)


# --- регионы ---


def test_region_rename_and_add(engine):
    assert update_region_name(engine, 40, "Калужская обл.") is True
    assert update_region_name(engine, 40, "Калужская обл.") is False
    add_region(engine, code="91", region="Новый регион")
    regions = {r.code: r for r in load_region_records(engine)}
    assert regions[40].region == "Калужская обл."
    assert regions[40].updated_at is not None
    assert regions[91].region == "Новый регион"


@pytest.mark.parametrize(
    ("code", "region", "message"),
    [("40", "Дубль", "уже занят регионом «Калужская область»"), ("-1", "Х", "Код региона"), ("92", " ", "Название региона")],
)
def test_add_region_rejects_invalid(engine, code, region, message):
    with pytest.raises(DirectoryValidationError, match=message):
        add_region(engine, code=code, region=region)
