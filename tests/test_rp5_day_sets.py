"""Выбор полного набора сроков rp5 за сутки и атомарность импорта."""
from __future__ import annotations

from datetime import date, time
from pathlib import Path

import pytest
from openpyxl import Workbook
from sqlalchemy import select, update
from sqlalchemy.engine import Engine

from meteoregik.db.migrations import run_migrations
from meteoregik.db.schema import computed_indicators, gg_readings, rp5_readings, stations
from meteoregik.db.session import get_engine
from meteoregik.importers.rp5_importer import Rp5Importer

WMO = 27704
DAY = date(2020, 1, 1)


def _engine(tmp_path: Path) -> Engine:
    engine = get_engine(tmp_path / 'days.sqlite3')
    run_migrations(engine)
    with engine.begin() as conn:
        conn.execute(stations.insert().values(wmo_index=WMO, name='Мосальск'))
    return engine


def _file(tmp_path: Path, name: str, hours: list[int], *, values: list[float] | None = None,
          day: int = 1) -> str:
    wb = Workbook()
    ws = wb.active
    ws.append(['# Метеостанция Мосальск, Россия, WMO_ID=27704, выборка'])
    ws.append(['#'])
    ws.append(['Местное время в Мосальске', 'T', 'RRR', 'tR'])
    for index, hour in enumerate(hours):
        ws.append([f'{day:02d}.01.2020 {hour:02d}:00', values[index] if values else float(hour), None, None])
    path = tmp_path / name
    wb.save(path)
    return str(path)


def _raw(engine: Engine) -> list[tuple[date, time, float | None]]:
    with engine.connect() as conn:
        return [tuple(row) for row in conn.execute(select(
            rp5_readings.c.obs_date, rp5_readings.c.obs_time, rp5_readings.c.T_temperature,
        ).order_by(rp5_readings.c.obs_date, rp5_readings.c.obs_time))]


def test_more_terms_replaces_full_day_automatically(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    Rp5Importer(engine).import_file(_file(tmp_path, 'old.xlsx', [0, 6, 12, 18]))
    report = Rp5Importer(engine).import_file(_file(tmp_path, 'new.xlsx', list(range(0, 24, 3))))
    assert len(_raw(engine)) == 8
    assert report.imported_rows == 8 and report.replaced_rows == 4


@pytest.mark.parametrize('choice', ['old', 'new'])
def test_fewer_terms_keeps_or_replaces_exact_day(tmp_path: Path, choice: str) -> None:
    engine = _engine(tmp_path)
    old_hours = [0, 3, 6, 9, 12, 15]
    new_hours = [0, 6, 12, 18]
    Rp5Importer(engine).import_file(_file(tmp_path, 'old.xlsx', old_hours))
    asked = []
    def confirm(conflict):
        asked.append(conflict)
        return choice
    report = Rp5Importer(engine, confirm_day_replacement=confirm).import_file(
        _file(tmp_path, 'new.xlsx', new_hours))
    assert len(asked) == 1
    assert asked[0].old_times == tuple(time(hour) for hour in old_hours)
    assert asked[0].new_times == tuple(time(hour) for hour in new_hours)
    assert [row[1].hour for row in _raw(engine)] == (old_hours if choice == 'old' else new_hours)
    assert report.imported_rows == (0 if choice == 'old' else 4)
    assert report.replaced_rows == (0 if choice == 'old' else 6)


@pytest.mark.parametrize('choice', ['old', 'new'])
def test_equal_counts_with_different_hours_require_choice(tmp_path: Path, choice: str) -> None:
    engine = _engine(tmp_path)
    Rp5Importer(engine).import_file(_file(tmp_path, 'old.xlsx', [0, 6, 12, 18]))
    asked = []
    def confirm(conflict):
        asked.append(conflict)
        return choice
    Rp5Importer(engine, confirm_day_replacement=confirm).import_file(_file(tmp_path, 'new.xlsx', [0, 9, 18, 21]))
    assert len(asked) == 1
    assert [row[1].hour for row in _raw(engine)] == ([0, 6, 12, 18] if choice == 'old' else [0, 9, 18, 21])


def test_equal_hours_changed_values_require_confirmation(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    Rp5Importer(engine).import_file(_file(tmp_path, 'old.xlsx', [0, 6], values=[1, 3]))
    asked = []
    def confirm(conflict):
        asked.append(conflict)
        return 'new'
    Rp5Importer(engine, confirm_day_replacement=confirm).import_file(
        _file(tmp_path, 'new.xlsx', [0, 6], values=[5, 7]))
    assert len(asked) == 1
    assert asked[0].old_stats[('T', 'avg')] == 2
    assert asked[0].new_stats[('T', 'avg')] == 6
    assert [row[2] for row in _raw(engine)] == [5, 7]


def test_missing_confirmation_does_not_partially_import_other_dates(tmp_path: Path) -> None:
    from meteoregik.importers.base import Rp5DayChoiceRequiredError
    engine = _engine(tmp_path)
    Rp5Importer(engine).import_file(_file(tmp_path, 'old.xlsx', [0, 6]))
    before = _raw(engine)
    path = _file(tmp_path, 'new.xlsx', [0], values=[10])
    from openpyxl import load_workbook
    wb = load_workbook(path)
    wb.active.append(['02.01.2020 09:00', 4, None, None])
    wb.save(path)
    with pytest.raises(Rp5DayChoiceRequiredError):
        Rp5Importer(engine).import_file(path)
    assert _raw(engine) == before


def test_identical_raw_without_computed_values_does_not_ask(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    path = _file(tmp_path, 'same.xlsx', [0, 6])
    importer = Rp5Importer(engine)
    importer.import_file(path)
    report = importer.import_file(path)
    assert report.imported_rows == 0


def test_identical_raw_wrong_saved_aggregate_requires_choice(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    path = _file(tmp_path, 'same.xlsx', [0, 6], values=[1, 3])
    Rp5Importer(engine).import_file(path)
    from meteoregik.calculations.recompute import recompute_range
    recompute_range(engine, WMO, DAY, DAY)
    with engine.begin() as conn:
        conn.execute(computed_indicators.update().where(
            computed_indicators.c.wmo_index == WMO, computed_indicators.c.parameter == 'T',
            computed_indicators.c.stat_type == 'avg', computed_indicators.c.period_type == 'day',
            computed_indicators.c.period_start == DAY,
        ).values(value=99, source_file='previous.xlsx'))
    asked = []
    def confirm(conflict):
        asked.append(conflict)
        return 'new'
    report = Rp5Importer(engine, confirm_day_replacement=confirm).import_file(path)
    assert len(asked) == 1
    assert asked[0].old_stats[('T', 'avg')] == 99
    assert asked[0].new_stats[('T', 'avg')] == 2
    assert report.imported_rows == 0 and report.stations[0].new_period_start == DAY


def test_gg_priority_is_preserved_in_candidate_statistics(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    Rp5Importer(engine).import_file(_file(tmp_path, 'old.xlsx', [0, 6], values=[1, 3]))
    with engine.begin() as conn:
        conn.execute(gg_readings.insert().values(
            wmo_index=WMO, station_name='Мосальск', obs_date=DAY,
            t_avg=10, t_min=None, t_max=15, rrr_precipitation=0, source_file='gg.xlsx',
        ))
    asked = []
    def confirm(conflict):
        asked.append(conflict)
        return 'new'
    Rp5Importer(engine, confirm_day_replacement=confirm).import_file(
        _file(tmp_path, 'new.xlsx', [0], values=[20]))
    assert asked[0].new_stats == {('T', 'avg'): 10, ('T', 'min'): None, ('T', 'max'): 15, ('R', 'sum'): 0}


def test_empty_temperature_does_not_make_new_day_more_complete(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    Rp5Importer(engine).import_file(_file(tmp_path, 'old.xlsx', [0, 6], values=[1, 3]))
    path = _file(tmp_path, 'new.xlsx', [0, 3, 6], values=[1, None, 3])
    asked = []
    def confirm(conflict):
        asked.append(conflict)
        return 'old'
    Rp5Importer(engine, confirm_day_replacement=confirm).import_file(path)
    assert len(asked) == 1 and len(asked[0].new_times) == 2
    assert len(_raw(engine)) == 2
