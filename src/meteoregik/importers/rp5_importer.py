"""Импортёр формата rp5 (срочные наблюдения) -> rp5_readings.

Из первой строки шапки (между последними "#"-строками) регэкспом парсится и
название станции, и WMO_ID:
    "Метеостанция (?P<name>.+?), Россия, WMO_ID=(?P<wmo>\\d+),"
Далее matching.find_or_create_station(wmo, name) только гарантирует, что
запись в stations существует — сама rp5_readings хранит wmo_index и
station_name напрямую, без FK station_id (см. plan.md, п.11 решений).
"""

from __future__ import annotations

from sqlalchemy.engine import Engine

from meteoregik.importers.base import ImportReport, Importer

RP5_HEADER_RE = r"Метеостанция\s+(?P<name>.+?),\s*Россия,\s*WMO_ID=(?P<wmo>\d+),"


class Rp5Importer(Importer):
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def import_file(self, file_path: str) -> ImportReport:
        raise NotImplementedError
