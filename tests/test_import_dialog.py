from datetime import date

from PySide6.QtWidgets import QApplication, QMessageBox

from meteoregik.calculations.recompute import ValueConflict
from meteoregik.db.session import get_engine
from meteoregik.importers.base import ImportReport
from meteoregik.ui.import_dialog import ImportDialog, format_conflict_message, format_import_report


def _qapp():
    return QApplication.instance() or QApplication([])


def _click_button_containing(box: QMessageBox, text: str) -> None:
    for btn in box.buttons():
        if text in btn.text():
            btn.click()
            return
    raise AssertionError(f"кнопка с текстом, содержащим {text!r}, не найдена")


def test_format_import_report_with_period():
    report = ImportReport(
        wmo_index=27704,
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
        wmo_index=None,
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


def test_format_conflict_message_day_level():
    conflict = ValueConflict(
        wmo_index=27707,
        period_type="day",
        period_start=date(2015, 1, 21),
        period_end=date(2015, 1, 21),
        stat_type="max",
        old_value=-2.3,
        old_source_file="Сухиничи 1959-2025_формат_ГГ.xlsx",
        new_value=-2.5,
        new_source_file="Сухиничи_2015-2019_rp5.xls",
    )

    text = format_conflict_message(conflict)

    assert "максимальная температура" in text
    assert "2015-01-21" in text
    assert "Сухиничи 1959-2025_формат_ГГ.xlsx" in text
    assert "-2.3" in text
    assert "Сухиничи_2015-2019_rp5.xls" in text
    assert "-2.5" in text


def test_format_conflict_message_without_source_file():
    conflict = ValueConflict(
        wmo_index=27707,
        period_type="decade",
        period_start=date(2015, 1, 1),
        period_end=date(2015, 1, 10),
        stat_type="avg",
        old_value=1.0,
        old_source_file=None,
        new_value=2.0,
        new_source_file=None,
    )

    text = format_conflict_message(conflict)

    assert "неизвестный файл" in text
    assert "2015-01-01 — 2015-01-10" in text


def _make_conflict(day: int, old_value: float, new_value: float) -> ValueConflict:
    return ValueConflict(
        wmo_index=27707,
        period_type="day",
        period_start=date(2015, 1, day),
        period_end=date(2015, 1, day),
        stat_type="max",
        old_value=old_value,
        old_source_file="rp5.xls",
        new_value=new_value,
        new_source_file="gg.xlsx",
    )


def test_conflict_resolver_asks_every_time_without_apply_to_all(tmp_path, monkeypatch):
    _qapp()
    engine = get_engine(tmp_path / "test.sqlite3")
    dialog = ImportDialog(engine)

    call_count = {"n": 0}

    def fake_exec(self):
        call_count["n"] += 1
        _click_button_containing(self, "Взять новое")  # не "для всех"
        return 0

    monkeypatch.setattr(QMessageBox, "exec", fake_exec)

    resolver = dialog._make_conflict_resolver()
    assert resolver(_make_conflict(21, -2.5, -2.3)) == "new"
    assert resolver(_make_conflict(22, 1.0, 2.0)) == "new"
    assert call_count["n"] == 2  # диалог показывался на каждый вызов


def test_conflict_resolver_remembers_apply_to_all_choice(tmp_path, monkeypatch):
    _qapp()
    engine = get_engine(tmp_path / "test.sqlite3")
    dialog = ImportDialog(engine)

    call_count = {"n": 0}

    def fake_exec(self):
        call_count["n"] += 1
        _click_button_containing(self, "Оставить сохранённое для всех оставшихся")
        return 0

    monkeypatch.setattr(QMessageBox, "exec", fake_exec)

    resolver = dialog._make_conflict_resolver()
    assert resolver(_make_conflict(21, -2.5, -2.3)) == "old"
    assert resolver(_make_conflict(22, 1.0, 2.0)) == "old"  # без повторного диалога
    assert resolver(_make_conflict(23, 5.0, 6.0)) == "old"
    assert call_count["n"] == 1  # диалог показан только один раз
