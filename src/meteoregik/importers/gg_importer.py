"""Импортёр формата ГГ (среднесуточные показатели) -> gg_readings.

Источник: "Индекс ВМО, Год, Месяц, День, Tavg, Tmax, RRR, Tmin". В файле нет
названия станции — matching.find_or_create_station(wmo_index) возвращает
station_id (создаёт новую станцию и запрашивает у пользователя название, если
не найдена); station_name резолвится через stations.name по station_id и
кэшируется в gg_readings вместе со station_id.
"""

from __future__ import annotations

from sqlalchemy.engine import Engine

from meteoregik.importers.base import ImportReport, Importer


class GgImporter(Importer):
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def import_file(self, file_path: str) -> ImportReport:
        raise NotImplementedError
