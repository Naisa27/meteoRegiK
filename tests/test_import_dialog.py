from datetime import date

from meteoregik.importers.base import ImportReport
from meteoregik.ui.import_dialog import format_import_report


def test_format_import_report_with_period():
    report = ImportReport(
        station_id=1,
        station_name="Мосальск",
        period_start=date(2020, 1, 1),
        period_end=date(2020, 12, 31),
        total_rows=100,
        imported_rows=90,
        skipped_rows=10,
    )

    text = format_import_report(report)

    assert "Мосальск" in text
    assert "2020-01-01 — 2020-12-31" in text
    assert "Всего строк в файле: 100" in text
    assert "Импортировано: 90" in text
    assert "Пропущено (уже существуют): 10" in text


def test_format_import_report_empty_file():
    report = ImportReport(
        station_id=None,
        station_name=None,
        period_start=None,
        period_end=None,
        total_rows=0,
        imported_rows=0,
        skipped_rows=0,
    )

    text = format_import_report(report)

    assert "Станция: —" in text
    assert "Период: —" in text
