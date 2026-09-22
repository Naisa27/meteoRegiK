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
