"""Реестр показателей, доступных для вывода на экране "Просмотр агрегатных
данных" и в экспорте в Excel.

Показатель в понимании пользователя ("t средняя", "t Мин", "t макс") — это
пара (parameter, stat_type) из computed_indicators с человекочитаемым
названием. Остальной код (reports/, ui/, export/) работает только с
абстрактным ParamKey и не знает про конкретные значения "T"/"avg" — новый
показатель добавляется одной строкой сюда (плюс сам расчёт в calculations/),
без правки раскладки таблицы или экрана.
"""

from __future__ import annotations

ParamKey = tuple[str, str]  # (parameter, stat_type), например ("T", "avg")

SNOW_PARAMETERS = frozenset({"S", "SNOW_DEPTH", "FROST_LOW_SNOW"})
SNOW_ASSUMPTION = (
    "Снег определяется только по архиву rp5. Пустые и текстовые значения высоты, "
    "а также отсутствующие дни внутри его границ приняты за 0. За границами "
    "архива данных о снеге нет. Дни без температуры не оценены для морозов "
    "при малоснежье. Неполные периоды поясняются в подсказках значений."
)

PARAMETER_LABELS: dict[ParamKey, str] = {
    ("T", "avg"): "t средняя",
    ("T", "min"): "t Мин",
    ("T", "max"): "t макс",
    ("R", "sum"): "сумма осадков",
    ("S", "sum"): "дни со снежным покровом",
    ("SNOW_DEPTH", "max"): "максимальная высота снега",
    ("FROST_LOW_SNOW", "sum"): "сильные морозы при малоснежье, дни",
}

# Порядок строк в таблице результатов = порядок словаря выше.
DEFAULT_PARAMETERS: list[ParamKey] = [("T", "avg"), ("T", "min"), ("T", "max"), ("R", "sum")]


def label_for(key: ParamKey) -> str:
    """Человекочитаемое название показателя; для ещё не зарегистрированного
    ключа — сам ключ в виде "parameter/stat_type" (не падать в UI)."""
    return PARAMETER_LABELS.get(key, f"{key[0]}/{key[1]}")


# Название величины для подписи оси значений на графике — по parameter
# (не stat_type: средняя/мин/макс температуры — одна и та же ось).
PARAMETER_QUANTITIES: dict[str, str] = {
    "T": "Температура, °C",
    "R": "Осадки, мм",
    "S": "Дни со снежным покровом",
    "SNOW_DEPTH": "Высота снега, см",
    "FROST_LOW_SNOW": "Сильные морозы при малоснежье, дни",
}

# Знаков после запятой при выводе: температура хранится до сотых, осадки
# измеряются с точностью 0.1 мм — "12.30 мм" обещало бы точность, которой нет.
PARAMETER_DECIMALS: dict[str, int] = {"T": 2, "R": 1, "S": 0, "SNOW_DEPTH": 1, "FROST_LOW_SNOW": 0}
DEFAULT_DECIMALS = 2

# Величины, которые на графике рисуются столбцами, а не линией: сумма за
# период — количество, а не состояние, между соседними суммами нет
# промежуточных значений, которые изображала бы линия.
BAR_PARAMETERS: frozenset[str] = frozenset({"R", "S", "FROST_LOW_SNOW"})


def decimals_for(key: ParamKey) -> int:
    return PARAMETER_DECIMALS.get(key[0], DEFAULT_DECIMALS)


def is_bar(key: ParamKey) -> bool:
    return key[0] in BAR_PARAMETERS


def quantity_title(keys: list[ParamKey]) -> str:
    """Подпись оси для показателей одной величины; пусто, если их нет."""
    return value_axis_title(keys) if keys else ""


def value_axis_title(parameters: list[ParamKey]) -> str:
    """Подпись оси значений: название величины, если все выбранные
    показатели — одна величина, иначе нейтральное "Значение" (разные
    единицы на одной оси подписать одной величиной нельзя)."""
    quantities = {PARAMETER_QUANTITIES.get(parameter) for parameter, _ in parameters}
    if len(quantities) == 1:
        (quantity,) = quantities
        if quantity is not None:
            return quantity
    return "Значение"
