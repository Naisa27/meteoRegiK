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
