from __future__ import annotations

from meteoregik.reports.parameters import DEFAULT_PARAMETERS, PARAMETER_LABELS, label_for


def test_default_parameters_match_registry_order() -> None:
    assert DEFAULT_PARAMETERS == list(PARAMETER_LABELS)


def test_label_for_known_parameter() -> None:
    assert label_for(("T", "avg")) == "t средняя"
    assert label_for(("T", "min")) == "t Мин"
    assert label_for(("T", "max")) == "t макс"


def test_label_for_unknown_parameter_falls_back_to_key() -> None:
    assert label_for(("RRR", "sum")) == "RRR/sum"


def test_value_axis_title_names_single_quantity_or_falls_back() -> None:
    from meteoregik.reports.parameters import value_axis_title

    assert value_axis_title([("T", "avg"), ("T", "max")]) == "Температура, °C"
    assert value_axis_title([("T", "avg"), ("RRR", "sum")]) == "Значение"
    assert value_axis_title([("RRR", "sum")]) == "Значение"


def test_precipitation_is_registered_with_its_own_quantity_and_format() -> None:
    from meteoregik.reports.parameters import decimals_for, is_bar, quantity_title, value_axis_title

    rain, temperature = ("R", "sum"), ("T", "avg")
    assert label_for(rain) == "сумма осадков"
    assert value_axis_title([rain]) == "Осадки, мм"
    assert value_axis_title([temperature, rain]) == "Значение"  # разные единицы на одной оси
    assert (decimals_for(temperature), decimals_for(rain)) == (2, 1)
    assert is_bar(rain) and not is_bar(temperature)
    assert quantity_title([]) == ""
