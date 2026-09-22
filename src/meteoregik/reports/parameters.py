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

PARAMETER_LABELS: dict[ParamKey, str] = {
    ("T", "avg"): "t средняя",
    ("T", "min"): "t Мин",
    ("T", "max"): "t макс",
}

# Порядок строк в таблице результатов = порядок словаря выше.
DEFAULT_PARAMETERS: list[ParamKey] = list(PARAMETER_LABELS)


def label_for(key: ParamKey) -> str:
    """Человекочитаемое название показателя; для ещё не зарегистрированного
    ключа — сам ключ в виде "parameter/stat_type" (не падать в UI)."""
    return PARAMETER_LABELS.get(key, f"{key[0]}/{key[1]}")
