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

Если в файле есть и название станции (rp5), оно сверяется с названием в
справочнике — confirm_station_name: при расхождении загрузку по индексу
подтверждает пользователь.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass

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


def names_match(first: str, second: str) -> bool:
    """Одно ли это название. Регистр, "ё"/"е" и лишние пробелы различием не
    считаются: это разница набора, а не другая станция. Всё остальное —
    "Москва" и "Москва (ВДНХ)" — различие: решать, одна ли это станция,
    должен пользователь, а не программа."""

    def normalized(name: str) -> str:
        return " ".join(name.casefold().replace("ё", "е").split())

    return normalized(first) == normalized(second)


@dataclass(frozen=True)
class StationNameMismatch:
    """Индекс ВМО из файла найден в справочнике, но станция там названа
    иначе, чем в файле."""

    wmo_index: int
    name_in_file: str
    name_in_directory: str

    @property
    def description(self) -> str:
        """Что именно не сошлось — общее начало вопроса пользователю и
        текста ошибки."""
        return (
            f"В файле станция названа «{self.name_in_file}», а в справочнике под индексом ВМО "
            f"{self.wmo_index} записана станция «{self.name_in_directory}»."
        )


class StationNameMismatchError(LookupError):
    """Название станции в файле не совпало со справочником, и загрузку по
    индексу никто не подтвердил. Текст — готовое сообщение пользователю."""

    def __init__(self, mismatch: StationNameMismatch, *, index_location: str) -> None:
        self.mismatch = mismatch
        super().__init__(
            f"{mismatch.description} Файл не загружен.\n\n"
            f"Проверьте индекс и название станции {index_location}: возможно, в файле опечатка "
            "или это файл другой станции."
        )


def confirm_station_name(
    name_in_file: str,
    name_in_directory: str | None,
    wmo_index: int,
    *,
    index_location: str,
    confirm: Callable[[StationNameMismatch], bool] | None,
) -> None:
    """Сверка названия из файла с названием станции в справочнике
    (корректировка 2026-10-11): привязка идёт по индексу, и опечатка в
    индексе, попавшая на чужую существующую станцию, иначе молча записала бы
    ей чужие данные. При расхождении спрашивается confirm — загружать ли по
    индексу; отказ или отсутствие confirm (вызов не из окна импорта) —
    StationNameMismatchError: без явного согласия данные не пишутся.

    Станция без названия в справочнике не сверяется — сравнивать не с чем."""
    if not name_in_directory or names_match(name_in_file, name_in_directory):
        return
    mismatch = StationNameMismatch(wmo_index, name_in_file, name_in_directory)
    if confirm is None or not confirm(mismatch):
        raise StationNameMismatchError(mismatch, index_location=index_location)
