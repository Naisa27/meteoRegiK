"""Восстановление очереди перед чтением расчётных данных."""
from __future__ import annotations

from collections.abc import Iterable, Callable
from datetime import date
from sqlalchemy import select
from sqlalchemy.engine import Engine
from meteoregik.db.schema import pending_recomputations


def ensure_computed_fresh(engine: Engine, wmo_indexes: Iterable[int] | None = None, *, recompute: Callable | None = None) -> None:
    """Завершить пересчёт, сохраняя непричастные суточные решения."""
    from meteoregik.calculations.recompute import recompute_range
    calculate = recompute or recompute_range
    table = pending_recomputations
    with engine.connect() as conn:
        query = select(table).order_by(table.c.wmo_index, table.c.obs_date)
        if wmo_indexes is not None:
            query = query.where(table.c.wmo_index.in_(list(wmo_indexes)))
        rows = conn.execute(query).mappings().all()
    groups: list[tuple[int, date, date, bool]] = []
    for row in rows:
        wmo, day, periods = row["wmo_index"], row["obs_date"], row["periods_only"]
        if groups and groups[-1][0] == wmo and groups[-1][3] == periods and (day - groups[-1][2]).days == 1:
            groups[-1] = wmo, groups[-1][1], day, periods
        else:
            groups.append((wmo, day, day, periods))
    for wmo, start, end, periods in groups:
        calculate(engine, wmo, start, end, periods_only=periods)
