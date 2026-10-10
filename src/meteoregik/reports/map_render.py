"""Оформление экрана "Карта" (2026-10-10): цвет, размер, подпись и
всплывающая карточка каждого маркера и легенда — готовыми словарями и HTML
для страницы карты (static/map/map.html). Qt-независимо: страница только
рисует то, что здесь посчитано, поэтому цвет маркера и легенда не могут
разойтись, а правила раскраски проверяются тестами без браузера.

Цвет — по задаче цвета (skill dataviz):
- "Станции": вид залитых данных (rp5 / ГГ / оба) — категории, первые три
  цвета категориальной палитры; у каждой категории подпись в легенде и в
  карточке станции, цвет не единственный носитель.
- "Показатель": отклонение от нормы — полярность, расходящаяся шкала
  "синий ↔ красный" с нейтральной серединой в нуле, симметричная. Сама
  температура — та же шкала, но растянутая от минимума до максимума среди
  показанных станций: с серединой в 0 °C станции со средними +5.4 и +5.9
  были бы одного цвета, а карта нужна, чтобы их сравнить. Для ползунка
  времени шкала одна на все шаги — иначе цвет нельзя сравнивать между годами.
- "Полнота": доля дней с данными — величина, один тон "светлый → тёмный".
  Размер кружка — длина архива.
Рядом с маркером подписано число: цвет читается только вместе с ним.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from html import escape
from typing import Any

from meteoregik.reports.dashboard_data import format_signed
from meteoregik.reports.map_data import ANOMALY, INDICATOR_LABELS, MapData, MapStation, StationValue
from meteoregik.reports.source_data import FORMAT_GG, FORMAT_RP5, StationSourceRow, format_share

DIVERGING_STOPS = ("#0d366b", "#256abf", "#86b6ef", "#f0efec", "#f2a39a", "#e34948", "#8c1d1c")
FILL_STOPS = ("#e6f0fc", "#86b6ef", "#256abf", "#0d366b")
FORMAT_COLORS = {
    (FORMAT_RP5,): "#2a78d6",
    (FORMAT_GG,): "#eb6834",
    (FORMAT_RP5, FORMAT_GG): "#1baf7a",
}
FORMAT_TITLES = {
    (FORMAT_RP5,): "только rp5",
    (FORMAT_GG,): "только ГГ",
    (FORMAT_RP5, FORMAT_GG): "rp5 и ГГ",
}
MUTED_COLOR = "#aab4bb"  # станция есть, значения нет
# Доля дней с данными ниже этой красится одинаково — самым светлым: почти
# все станции лежат в 80–100 %, и шкала от нуля сделала бы их неразличимыми
FILL_SCALE_FLOOR = 0.5

RADIUS = 9
RADIUS_MIN, RADIUS_MAX = 6, 15
DASHBOARD_LINK = "app://dashboard/{wmo_index}"


def _rgb(color: str) -> tuple[int, int, int]:
    return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)


@dataclass(frozen=True)
class ColorScale:
    """Непрерывная шкала: значение low — первый цвет, high — последний,
    между опорными цветами — линейно. Значения за краями прижимаются к краю."""

    stops: tuple[str, ...]
    low: float
    high: float

    def color(self, value: float) -> str:
        if self.high <= self.low:
            position = 0.5
        else:
            position = min(max((value - self.low) / (self.high - self.low), 0.0), 1.0)
        scaled = position * (len(self.stops) - 1)
        index = min(int(scaled), len(self.stops) - 2)
        fraction = scaled - index
        start, end = _rgb(self.stops[index]), _rgb(self.stops[index + 1])
        return "#" + "".join(f"{round(a + (b - a) * fraction):02x}" for a, b in zip(start, end))

    @property
    def css_gradient(self) -> str:
        return f"linear-gradient(to right, {', '.join(self.stops)})"


FILL_SCALE = ColorScale(FILL_STOPS, FILL_SCALE_FLOOR, 1.0)


def indicator_scale(indicator: str, values: Iterable[float]) -> ColorScale:
    numbers = list(values)
    if indicator == ANOMALY:
        # Не уже ±0.5 °C: иначе отклонение в сотые доли красилось бы как крайнее
        half = max((abs(v) for v in numbers), default=0.0)
        half = max(half, 0.5)
        return ColorScale(DIVERGING_STOPS, -half, half)
    if not numbers:
        return ColorScale(DIVERGING_STOPS, 0.0, 0.0)
    return ColorScale(DIVERGING_STOPS, min(numbers), max(numbers))


def station_name(label: str) -> str:
    """"Сухиничи, Калужская область (27707)" -> "Сухиничи" — подпись у маркера."""
    return label.split(",")[0].split(" (")[0]


def _count(value: int) -> str:
    return f"{value:,}".replace(",", " ")


def _period(period: tuple[Any, Any] | None) -> str:
    return f"{period[0]:%d.%m.%Y}–{period[1]:%d.%m.%Y}" if period else "—"


def popup_html(station: MapStation, row: StationSourceRow | None, extra: tuple[str, str] | None = None) -> str:
    """Карточка станции по щелчку. extra — (название, значение) показателя
    текущего режима, первой строкой."""
    lines: list[tuple[str, str]] = [extra] if extra else []
    if row is None:
        lines.append(("Данные", "не залиты"))
    else:
        lines.append(("Форматы", ", ".join(row.formats) or "—"))
        if row.rp5_period:
            lines.append(("Период rp5", _period(row.rp5_period)))
        if row.gg_period:
            lines.append(("Период ГГ", _period(row.gg_period)))
        if row.days_total:
            lines.append(("Расчётная таблица", _period(row.computed_period)))
            lines.append(("Пропущено дней", f"{_count(row.days_missing)} из {_count(row.days_total)}"))
        if row.obs_days:
            lines.append(("Пропущено сроков", _count(row.obs_missing)))
    rows = "".join(f"<tr><th>{escape(name)}</th><td>{escape(value)}</td></tr>" for name, value in lines)
    link = (
        f"<a class='open' href='{DASHBOARD_LINK.format(wmo_index=station.wmo_index)}'>Открыть в дашборде</a>"
        if row is not None and row.days_total
        else ""
    )
    return f"<div class='pop'><b>{escape(station.label)}</b><table>{rows}</table>{link}</div>"


def _marker(station: MapStation, **fields: Any) -> dict[str, Any]:
    return {"wmo": station.wmo_index, "lat": station.lat, "lon": station.lon, **fields}


def _legend(title: str, body: str, note: str = "") -> str:
    note_html = f"<div class='note'>{escape(note)}</div>" if note else ""
    return f"<div class='title'>{escape(title)}</div>{body}{note_html}"


def _swatch(color: str, text: str) -> str:
    return f"<div class='item'><span class='dot' style='background:{color}'></span>{escape(text)}</div>"


def _gradient(scale: ColorScale, low: str, high: str, middle: str = "") -> str:
    return (
        f"<div class='bar' style='background:{scale.css_gradient}'></div>"
        f"<div class='ticks'><span>{escape(low)}</span><span>{escape(middle)}</span><span>{escape(high)}</span></div>"
    )


@dataclass(frozen=True)
class MapLayer:
    """Всё, что страница карты рисует поверх подложки."""

    markers: list[dict[str, Any]]
    legend: str


def background_points(data: MapData) -> list[list[Any]]:
    """Все станции справочника — мелкие серые точки: [широта, долгота, подпись]."""
    return [[station.lat, station.lon, station.label] for station in data.stations]


def stations_layer(data: MapData) -> MapLayer:
    located = data.located
    markers = []
    used: set[tuple[str, ...]] = set()
    for wmo, row in data.sources.items():
        station = located.get(wmo)
        if station is None or not row.formats:
            continue
        used.add(row.formats)
        markers.append(_marker(
            station, color=FORMAT_COLORS[row.formats], radius=RADIUS, label=station_name(station.label),
            tip=f"{station.label}: {FORMAT_TITLES[row.formats]}", popup=popup_html(station, row), muted=False,
        ))
    body = "".join(_swatch(color, FORMAT_TITLES[key]) for key, color in FORMAT_COLORS.items() if key in used)
    body += _swatch(MUTED_COLOR, "станция справочника без данных")
    return MapLayer(markers, _legend("Залитые данные", body))


def indicator_layer(
    data: MapData,
    indicator: str,
    values: Mapping[int, StationValue],
    scale: ColorScale,
    period_title: str,
) -> MapLayer:
    located = data.located
    title = INDICATOR_LABELS[indicator]
    markers = []
    any_muted = False
    for wmo, item in values.items():
        station = located.get(wmo)
        if station is None:
            continue
        if item.value is None:
            any_muted = True
            text = item.note or "нет значения"
            markers.append(_marker(
                station, color=MUTED_COLOR, radius=RADIUS - 2, label="", tip=f"{station.label}: {text}",
                popup=popup_html(station, data.sources.get(wmo), (title, text)), muted=True,
            ))
            continue
        number = f"{format_signed(item.value)} °C"
        detail = f"{number} ({item.note})" if item.note else number
        markers.append(_marker(
            station, color=scale.color(item.value), radius=RADIUS, label=format_signed(item.value),
            tip=f"{station.label}: {detail}", popup=popup_html(station, data.sources.get(wmo), (title, detail)),
            muted=False,
        ))
    middle = "0" if indicator == ANOMALY else ""
    body = _gradient(scale, f"{format_signed(scale.low)} °C", f"{format_signed(scale.high)} °C", middle)
    if any_muted:
        body += _swatch(MUTED_COLOR, "значения нет")
    return MapLayer(markers, _legend(f"{title}, °C", body, period_title))


def fill_layer(data: MapData) -> MapLayer:
    located = data.located
    rows = [(located[wmo], row) for wmo, row in data.sources.items() if wmo in located]
    longest = max((row.days_total for _, row in rows), default=0)
    markers = []
    any_muted = False
    for station, row in rows:
        if not row.days_total:
            any_muted = True
            markers.append(_marker(
                station, color=MUTED_COLOR, radius=RADIUS_MIN, label="",
                tip=f"{station.label}: суточные значения не посчитаны", popup=popup_html(station, row), muted=True,
            ))
            continue
        share = row.days_with_data / row.days_total
        years = row.days_total / 365.25
        # Площадь кружка, а не радиус, пропорциональна длине архива
        radius = RADIUS_MIN + (RADIUS_MAX - RADIUS_MIN) * (row.days_total / longest) ** 0.5
        text = format_share(share)
        detail = f"{text} дней с данными, архив {years:.0f} лет" if years >= 1 else f"{text} дней с данными"
        markers.append(_marker(
            station, color=FILL_SCALE.color(share), radius=round(radius, 1), label=text,
            tip=f"{station.label}: {detail}", popup=popup_html(station, row, ("Полнота", detail)), muted=False,
        ))
    body = _gradient(FILL_SCALE, f"≤ {FILL_SCALE_FLOOR:.0%}".replace("%", " %"), "100 %")
    if any_muted:
        body += _swatch(MUTED_COLOR, "не посчитано")
    return MapLayer(markers, _legend("Дней с данными", body, "Размер кружка — длина архива станции"))
