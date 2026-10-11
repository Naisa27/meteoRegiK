from PySide6.QtWidgets import QApplication

from meteoregik.reports.chart_data import ChartData
from meteoregik.ui.aggregate_chart_view import AggregateChartView, _color_for

AVG = ("T", "avg")
MIN = ("T", "min")
MAX = ("T", "max")


def _qapp():
    return QApplication.instance() or QApplication([])


def _sample_chart_data() -> ChartData:
    return ChartData(
        x_labels=["01.2020", "02.2020", "03.2020"],
        series={AVG: [1.0, 2.0, None], MIN: [-1.0, 0.0, 1.0], MAX: [3.0, 4.0, 5.0]},
    )


def test_color_for_is_fixed_per_parameter_regardless_of_selection():
    assert _color_for(AVG) != _color_for(MIN)
    assert _color_for(MIN) != _color_for(MAX)
    # цвет показателя не зависит от того, какие ещё показатели выбраны сейчас
    assert _color_for(MIN) == _color_for(MIN)


def test_plot_draws_one_line_per_selected_parameter():
    _qapp()
    view = AggregateChartView()
    view.plot(_sample_chart_data(), [AVG, MIN, MAX])

    assert len(view._ax.get_lines()) == 3


def test_plot_shows_legend_only_for_two_or_more_parameters():
    _qapp()
    view = AggregateChartView()

    view.plot(_sample_chart_data(), [AVG])
    assert view._ax.get_legend() is None

    view.plot(_sample_chart_data(), [AVG, MIN])
    assert view._ax.get_legend() is not None


def test_plot_replaces_previous_lines_on_replot():
    _qapp()
    view = AggregateChartView()
    view.plot(_sample_chart_data(), [AVG, MIN, MAX])
    view.plot(_sample_chart_data(), [AVG])

    assert len(view._ax.get_lines()) == 1


def test_plot_grab_smoke():
    _qapp()
    view = AggregateChartView()
    view.plot(_sample_chart_data(), [AVG, MIN, MAX])
    view.resize(300, 200)

    pixmap = view.grab()
    assert not pixmap.isNull()


# --- осадки: столбцами, вместе с температурой — на своей оси ---

RAIN = ("R", "sum")


def _chart_data_with_rain() -> ChartData:
    return ChartData(
        x_labels=["01.2020", "02.2020", "03.2020"],
        series={AVG: [1.0, 2.0, None], RAIN: [30.5, None, 12.0]},
    )


def test_precipitation_is_drawn_as_bars_on_its_own_right_axis_next_to_temperature():
    _qapp()
    view = AggregateChartView()
    view.plot(_chart_data_with_rain(), [AVG, RAIN])

    assert len(view._ax.get_lines()) == 1
    assert view._bar_ax is not None
    assert [bar.get_height() for bar in view._bar_ax.patches] == [30.5, 0.0, 12.0]
    assert view._ax.get_ylabel() == "Температура, °C"
    assert view._bar_ax.get_ylabel() == "Осадки, мм"
    # обе величины в одной легенде
    assert [text.get_text() for text in view._ax.get_legend().get_texts()] == ["t средняя", "сумма осадков"]


def test_precipitation_alone_uses_the_main_axis():
    _qapp()
    view = AggregateChartView()
    view.plot(_chart_data_with_rain(), [RAIN])

    assert view._bar_ax is None
    assert len(view._ax.patches) == 3
    assert view._ax.get_ylabel() == "Осадки, мм"


def test_second_axis_disappears_when_precipitation_is_deselected():
    _qapp()
    view = AggregateChartView()
    view.plot(_chart_data_with_rain(), [AVG, RAIN])
    view.plot(_chart_data_with_rain(), [AVG])

    assert view._bar_ax is None
    assert len(view._figure.axes) == 1


def test_x_axis_shows_period_labels():
    _qapp()
    view = AggregateChartView()
    view.plot(_chart_data_with_rain(), [AVG, RAIN])

    assert [label.get_text() for label in view._ax.get_xticklabels()] == ["01.2020", "02.2020", "03.2020"]
