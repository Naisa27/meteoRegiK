"""Заливка бандлящихся с приложением CSV-сидов stations/ru_regions в пустые
таблицы при первом запуске (см. resources.py: STATIONS_SEED_PATH,
RU_REGIONS_SEED_PATH). Это результат разовой заливки из исходных xlsx на
этапе создания проекта (см. plan.md, "Открытые вопросы для доработки") —
здесь никакие xlsx не парсятся, только собственный CSV-дамп проекта.

Идемпотентно: если таблица уже не пуста (пользователь уже работал с БД),
заливка пропускается — так пользовательские правки/новые станции не
затираются при каждом запуске.
"""

from __future__ import annotations

import csv

from sqlalchemy import func, select
from sqlalchemy.engine import Engine

from meteoregik.db.schema import ru_regions, stations
from meteoregik.resources import RU_REGIONS_SEED_PATH, STATIONS_SEED_PATH


def ensure_seed_data(engine: Engine) -> None:
    """Заливает stations и ru_regions из бандлящихся CSV, если таблицы пусты."""
    _seed_stations(engine)
    _seed_ru_regions(engine)


def _seed_stations(engine: Engine) -> None:
    with engine.begin() as conn:
        already_seeded = conn.execute(select(func.count()).select_from(stations)).scalar_one() > 0
        if already_seeded:
            return

        with STATIONS_SEED_PATH.open("r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            rows = [
                {
                    "wmo_index": int(row["wmo_index"]),
                    "name": row["name"],
                    "country": row["country"] or None,
                    "state": row["state"] or None,
                    "lat": float(row["lat"]) if row["lat"] else None,
                    "lon": float(row["lon"]) if row["lon"] else None,
                }
                for row in reader
            ]
        if rows:
            conn.execute(stations.insert(), rows)


def _seed_ru_regions(engine: Engine) -> None:
    with engine.begin() as conn:
        already_seeded = conn.execute(select(func.count()).select_from(ru_regions)).scalar_one() > 0
        if already_seeded:
            return

        with RU_REGIONS_SEED_PATH.open("r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            rows = [{"code": int(row["code"]), "region": row["region"]} for row in reader]
        if rows:
            conn.execute(ru_regions.insert(), rows)
