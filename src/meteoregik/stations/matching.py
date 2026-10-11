"""Привязка импортируемого файла к станции справочника — по wmo_index, не по
названию, одинаково для rp5 и ГГ (см. plan.md, п.8 решений).

Станции, которой нет в справочнике, импорт **не создаёт** (решение
пользователя 2026-10-11; раньше rp5 молча создавал её с названием из шапки
файла, а ГГ спрашивал название). Созданная при импорте запись была без
страны, региона и координат — не попадала на карту и под фильтр "Россия" в
справочниках, — а при опечатке в индексе в справочнике появлялась лишняя
станция с чужими данными. Теперь импорт останавливается до записи в базу и
отправляет пользователя проверить индекс или добавить станцию в
"Справочниках" со всеми сведениями.
"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.engine import Connection

from meteoregik.db.schema import stations


class StationNotFoundError(LookupError):
    """Станций из файла нет в справочнике. Текст ошибки — готовое сообщение
    пользователю (как у ImportFormatError): что не найдено и что делать.

    index_location — где в файле записан индекс ("в первой строке файла…"),
    name_from_file — название станции из файла, если оно там есть (rp5):
    по нему видно, ту ли станцию имел в виду файл."""

    def __init__(
        self, wmo_indexes: Iterable[int], *, index_location: str, name_from_file: str | None = None
    ) -> None:
        self.wmo_indexes = sorted(wmo_indexes)
        self.name_from_file = name_from_file
        listed = ", ".join(str(index) for index in self.wmo_indexes)
        if len(self.wmo_indexes) == 1:
            name = f" (в файле она названа «{name_from_file}»)" if name_from_file else ""
            what = f"Станции с индексом ВМО {listed}{name} нет в справочнике."
            fix = "добавьте станцию"
        else:
            what = f"Станций с индексами ВМО {listed} нет в справочнике."
            fix = "добавьте эти станции"
        super().__init__(
            f"{what} Файл не загружен.\n\n"
            f"Проверьте индекс {index_location}: возможно, в нём опечатка.\n\n"
            f"Если индекс верный, {fix} в справочник — «Справочники» → «Добавить станцию», "
            "с названием, индексом ВМО, страной, регионом и координатами — и повторите импорт."
        )


def require_stations(
    conn: Connection,
    wmo_indexes: Iterable[int],
    *,
    index_location: str,
    name_from_file: str | None = None,
) -> dict[int, str]:
    """Названия станций справочника по индексам ВМО: {wmo_index: name}.
    Если хотя бы одной станции нет — StationNotFoundError со всеми
    ненайденными индексами сразу (в файле ГГ станций может быть несколько,
    и исправлять их по одной за попытку импорта было бы мучительно)."""
    wanted = set(wmo_indexes)
    found = {
        wmo: name
        for wmo, name in conn.execute(
            select(stations.c.wmo_index, stations.c.name).where(stations.c.wmo_index.in_(wanted))
        )
    }
    missing = wanted - found.keys()
    if missing:
        raise StationNotFoundError(missing, index_location=index_location, name_from_file=name_from_file)
    return found
