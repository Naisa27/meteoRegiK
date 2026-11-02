from datetime import date, time
from pathlib import Path

import pytest
from openpyxl import Workbook
from sqlalchemy import select
from PySide6.QtWidgets import QApplication, QMessageBox

from meteoregik.calculations.recompute import ValueConflict
from meteoregik.calculations.recompute import recompute_range
from meteoregik.db.migrations import run_migrations
from meteoregik.db.session import get_engine
from meteoregik.importers.base import ImportReport, StationImportReport
from meteoregik.db.schema import computed_indicators, gg_readings, rp5_readings, stations
from meteoregik.importers.gg_importer import GgImporter
from meteoregik.stations.matching import StationNameMismatch
from meteoregik.ui.import_dialog import (
    ImportDialog,
    format_conflict_message,
    format_import_report,
    format_name_mismatch_question,
)


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
    assert "01.01.2020 — 31.12.2020" in text
    assert "Всего строк в файле: 100" in text
    assert "Импортировано: 90" in text
    assert "Пропущено (уже существуют): 10" in text


def test_format_import_report_shows_rows_without_data_only_when_known():
    base = dict(
        wmo_index=27704, station_name="Мосальск", period_start=date(2020, 1, 1),
        period_end=date(2020, 1, 2), total_rows=10, imported_rows=6, skipped_rows=1,
    )

    assert "Строк без данных (не загружены): 3" in format_import_report(ImportReport(**base, rows_without_data=3))
    assert "Строк без данных (не загружены): 0" in format_import_report(ImportReport(**base, rows_without_data=0))
    assert "без данных" not in format_import_report(ImportReport(**base))  # rp5: проверки нет


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
    assert "21.01.2015" in text
    assert "Сухиничи 1959-2025_формат_ГГ.xlsx" in text
    assert "-2.3" in text
    assert "Сухиничи_2015-2019_rp5.xls" in text
    assert "-2.5" in text


def test_format_conflict_message_names_precipitation():
    conflict = ValueConflict(
        wmo_index=27707, period_type="day", period_start=date(2020, 1, 10), period_end=date(2020, 1, 10),
        stat_type="sum", old_value=6.0, old_source_file="rp5.xls", new_value=6.5, new_source_file="gg.xlsx",
        parameter="R",
    )

    text = format_conflict_message(conflict)

    assert "Показатель «сумма осадков»" in text
    assert "температура" not in text


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
    assert "01.01.2015 — 10.01.2015" in text


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


def test_run_import_shows_clear_message_for_wrong_format(tmp_path, monkeypatch):
    _qapp()
    engine = get_engine(tmp_path / "test.sqlite3")
    run_migrations(engine)

    # ГГ-файл, но диалог настроен на импорт rp5 — неверный выбор формата.
    wb = Workbook()
    ws = wb.active
    ws.append(["Индекс ВМО", "Год", "Месяц", "День", "Tavg", "Tmax", "RRR", "Tmin"])
    ws.append([27707, 2020, 1, 1, 1.0, 2.0, 0.0, -1.0])
    file_path = str(tmp_path / "gg.xlsx")
    wb.save(file_path)

    dialog = ImportDialog(engine)
    dialog.file_edit.setText(file_path)
    dialog.rp5_radio.setChecked(True)

    calls = []
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda parent, title, text: calls.append((title, text))))

    dialog._run_import()

    assert len(calls) == 1
    title, text = calls[0]
    assert title == "Неверный формат файла"
    assert "неверный формат" in text
    # кнопки снова доступны после ошибки
    assert dialog.import_btn.isEnabled()
    assert dialog.browse_btn.isEnabled()
    assert dialog.close_btn.isEnabled()


def test_run_import_refuses_file_of_station_missing_in_directory(tmp_path, monkeypatch):
    _qapp()
    engine = get_engine(tmp_path / "test.sqlite3")
    run_migrations(engine)  # справочник пуст — станции 27707 в нём нет

    wb = Workbook()
    ws = wb.active
    ws.append(["Индекс ВМО", "Год", "Месяц", "День", "Tavg", "Tmax", "RRR", "Tmin"])
    ws.append([27707, 2020, 1, 1, 1.0, 2.0, 0.0, -1.0])
    file_path = str(tmp_path / "gg.xlsx")
    wb.save(file_path)

    dialog = ImportDialog(engine)
    dialog.file_edit.setText(file_path)
    dialog.gg_radio.setChecked(True)

    calls = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda parent, title, text: calls.append((title, text))))

    dialog._run_import()

    assert len(calls) == 1
    title, text = calls[0]
    assert title == "Станции нет в справочнике"
    assert "27707" in text and "Файл не загружен" in text
    assert "Добавить станцию" in text
    # отчёта об успехе нет, кнопки снова доступны
    assert dialog.success_label.text() == ""
    assert dialog.report_label.text() == ""
    assert dialog.import_btn.isEnabled()


def test_format_name_mismatch_question_names_both_stations_and_the_index():
    text = format_name_mismatch_question(StationNameMismatch(27707, "Мосальск", "Сухиничи"))

    assert "«Мосальск»" in text and "«Сухиничи»" in text and "27707" in text
    assert "записаны станции «Сухиничи»" in text


def _dialog_with_mismatching_rp5_file(tmp_path):
    """В справочнике под индексом 27707 — Сухиничи, а файл подписан Мосальском."""
    engine = get_engine(tmp_path / "test.sqlite3")
    run_migrations(engine)
    with engine.begin() as conn:
        conn.execute(stations.insert().values(name="Сухиничи", wmo_index=27707))
    wb = Workbook()
    ws = wb.active
    ws.append(["# Метеостанция Мосальск, Россия, WMO_ID=27707, выборка с 01.01.2020 по 31.12.2020, все дни"])
    ws.append(["#"])
    ws.append(["Местное время в Мосальске", "T"])
    ws.append(["01.01.2020 09:00", -1.2])
    file_path = str(tmp_path / "rp5.xlsx")
    wb.save(file_path)

    dialog = ImportDialog(engine)
    dialog.file_edit.setText(file_path)
    dialog.rp5_radio.setChecked(True)
    return engine, dialog


def _rp5_row_count(engine) -> int:
    with engine.connect() as conn:
        return len(conn.execute(select(rp5_readings.c.id)).all())


def test_run_import_asks_about_station_name_and_does_not_load_on_refusal(tmp_path, monkeypatch):
    _qapp()
    engine, dialog = _dialog_with_mismatching_rp5_file(tmp_path)
    shown = []

    def fake_exec(self):
        shown.append((self.windowTitle(), self.text(), self.defaultButton().text()))
        _click_button_containing(self, "Не загружать")
        return 0

    monkeypatch.setattr(QMessageBox, "exec", fake_exec)

    dialog._run_import()

    assert len(shown) == 1
    title, text, default_button = shown[0]
    assert title == "Название станции не совпадает"
    assert "«Мосальск»" in text and "«Сухиничи»" in text
    assert default_button == "Не загружать"  # Enter не заливает данные чужой станции
    assert _rp5_row_count(engine) == 0
    assert dialog.status_label.text() == "Файл не загружен"
    assert dialog.success_label.text() == ""
    assert dialog.import_btn.isEnabled()


def test_run_import_loads_file_by_index_after_confirmation(tmp_path, monkeypatch):
    _qapp()
    engine, dialog = _dialog_with_mismatching_rp5_file(tmp_path)

    def fake_exec(self):
        _click_button_containing(self, "Загрузить по индексу")
        return 0

    monkeypatch.setattr(QMessageBox, "exec", fake_exec)

    dialog._run_import()

    assert _rp5_row_count(engine) == 1
    assert dialog.success_label.text() == "Импорт завершён успешно"
    assert "Станция: Сухиничи" in dialog.report_label.text()


def test_import_dialog_buttons_have_rounded_corners():
    _qapp()
    engine = get_engine(":memory:")
    dialog = ImportDialog(engine)

    assert dialog.import_btn.objectName() == "primary"
    assert dialog.browse_btn.objectName() == "secondary"
    assert dialog.close_btn.objectName() == "secondary"
    assert "border-radius" in dialog.styleSheet()


def test_import_dialog_fits_full_report_without_clipping():
    _qapp()
    engine = get_engine(":memory:")
    dialog = ImportDialog(engine)
    dialog.show()
    report = ImportReport(
        27707, "Сухиничи", date(1959, 7, 1), date(2025, 2, 28),
        total_rows=24472, imported_rows=23688, skipped_rows=0, rows_without_data=784,
    )

    dialog.report_label.setText(format_import_report(report))
    QApplication.processEvents()

    assert "Строк без данных (не загружены): 784" in dialog.report_label.text()
    assert dialog.report_label.height() >= dialog.report_label.sizeHint().height()
    assert dialog.report_label.height() >= dialog.report_label.fontMetrics().lineSpacing() * 6


def _multi_station_dialog(tmp_path: Path) -> ImportDialog:
    """Разные даты станций выявляют пересчёт по ошибочному общему периоду."""
    _qapp()
    engine = get_engine(tmp_path / "multi.sqlite3")
    run_migrations(engine)
    with engine.begin() as conn:
        conn.execute(stations.insert(), [
            {"wmo_index": 27705, "name": "Калуга"},
            {"wmo_index": 27707, "name": "Сухиничи"},
        ])
    wb = Workbook()
    ws = wb.active
    ws.append(["Индекс ВМО", "Год", "Месяц", "День", "Tavg", "Tmax", "RRR", "Tmin"])
    ws.append([27705, 2020, 1, 1, 1.0, 2.0, 0.0, -1.0])
    ws.append([27707, 2020, 3, 2, 3.0, 4.0, 1.0, 2.0])
    path = tmp_path / "multi.xlsx"
    wb.save(path)
    dialog = ImportDialog(engine)
    dialog.file_edit.setText(str(path))
    dialog.gg_radio.setChecked(True)
    return dialog


def _calculated_stations(dialog: ImportDialog) -> set[int]:
    with dialog.engine.connect() as conn:
        return set(conn.scalars(select(computed_indicators.c.wmo_index).distinct()))


def test_multi_station_import_recomputes_every_station(tmp_path: Path) -> None:
    dialog = _multi_station_dialog(tmp_path)

    dialog._run_import()

    assert _calculated_stations(dialog) == {27705, 27707}
    assert dialog.success_label.text() == "Импорт завершён успешно"
    assert "Калуга" in dialog.report_label.text() and "Сухиничи" in dialog.report_label.text()


def test_multi_station_progress_uses_own_ranges_and_shared_resolver(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    dialog = _multi_station_dialog(tmp_path)
    calls = []
    progress = []
    monkeypatch.setattr(QMessageBox, "critical", lambda *args: None)

    def fake_recompute(engine, wmo, start, end, *, progress_callback, conflict_resolver, periods_only=False):
        assert not dialog.success_label.text()
        calls.append((wmo, start, end, conflict_resolver))
        progress_callback(end)
        progress.append(dialog.progress_bar.value())
        assert str(wmo) in dialog.status_label.text()

    monkeypatch.setattr("meteoregik.ui.import_dialog.recompute_range", fake_recompute)

    dialog._run_import()

    assert [call[:3] for call in calls] == [
        (27705, date(2020, 1, 1), date(2020, 1, 1)),
        (27707, date(2020, 3, 2), date(2020, 3, 2)),
    ]
    assert calls[0][3] is calls[1][3]
    assert progress == [1, 2]
    assert dialog.progress_bar.maximum() == 2


def test_repeated_import_without_repair_does_not_recompute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    dialog = _multi_station_dialog(tmp_path)
    dialog._run_import()

    def unexpected_recompute(*args, **kwargs):
        pytest.fail("Обычный повторный импорт не должен пересчитывать архив")

    monkeypatch.setattr("meteoregik.ui.import_dialog.recompute_range", unexpected_recompute)

    dialog._run_import()

    assert dialog.success_label.text() == "Импорт завершён успешно"


def test_repair_recomputes_previously_imported_stations_without_touching_raw_data(tmp_path: Path) -> None:
    dialog = _multi_station_dialog(tmp_path)
    GgImporter(dialog.engine).import_file(dialog.file_edit.text())
    with dialog.engine.connect() as conn:
        before = conn.execute(select(gg_readings).order_by(gg_readings.c.id)).all()
    assert not _calculated_stations(dialog)

    dialog._run_import()

    assert _calculated_stations(dialog) == {27705, 27707}
    with dialog.engine.connect() as conn:
        assert conn.execute(select(gg_readings).order_by(gg_readings.c.id)).all() == before
    assert "Импортировано: 0" in dialog.report_label.text()
    assert dialog.success_label.text() == "Импорт завершён успешно"


def test_failed_recompute_does_not_report_success_and_explains_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    dialog = _multi_station_dialog(tmp_path)
    messages = []

    def fail_on_second(engine, wmo, *args, **kwargs):
        if wmo == 27707:
            raise RuntimeError("Проверочная ошибка пересчёта")

    monkeypatch.setattr("meteoregik.ui.import_dialog.recompute_range", fail_on_second)
    monkeypatch.setattr(QMessageBox, "critical", lambda parent, title, text: messages.append((title, text)))

    dialog._run_import()

    assert not dialog.success_label.text()
    assert len(messages) == 1
    assert messages[0][0] == "Ошибка пересчёта"
    assert "автоматически" in messages[0][1]
    assert "Импортировано: 2" in dialog.report_label.text()


def test_partial_import_only_recomputes_new_station_ranges(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    dialog = _multi_station_dialog(tmp_path)
    dialog._run_import()
    wb = Workbook()
    ws = wb.active
    ws.append(["Индекс ВМО", "Год", "Месяц", "День", "Tavg", "Tmax", "RRR", "Tmin"])
    ws.append([27705, 2020, 1, 1, 1.0, 2.0, 0.0, -1.0])
    ws.append([27707, 2020, 3, 2, 3.0, 4.0, 1.0, 2.0])
    ws.append([27707, 2020, 6, 1, 5.0, 6.0, 2.0, 4.0])
    path = tmp_path / "partial.xlsx"
    wb.save(path)
    dialog.file_edit.setText(str(path))
    calls = []

    def record(engine, wmo, start, end, **kwargs):
        calls.append((wmo, start, end))

    monkeypatch.setattr("meteoregik.ui.import_dialog.recompute_range", record)

    dialog._run_import()

    assert calls == [(27707, date(2020, 6, 1), date(2020, 6, 1))]


def test_conflicting_rows_have_specific_message_and_no_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    dialog = _multi_station_dialog(tmp_path)
    from openpyxl import load_workbook
    wb = load_workbook(dialog.file_edit.text())
    wb.active.append([27707, 2020, 3, 2, 99.0, 4.0, 1.0, 2.0])
    wb.save(dialog.file_edit.text())
    messages = []
    monkeypatch.setattr(QMessageBox, "critical", lambda parent, title, text: messages.append((title, text)))

    dialog._run_import()

    assert len(messages) == 1 and messages[0][0] == "Противоречивые строки файла"
    assert "27707" in messages[0][1] and "3 и 4" in messages[0][1]
    assert not dialog.success_label.text()
    with dialog.engine.connect() as conn:
        assert conn.execute(select(gg_readings)).first() is None


def test_duplicate_report_separates_existing_keys_from_file_repeats() -> None:
    report = ImportReport(27705, "Калуга", date(2020, 1, 1), date(2020, 1, 2),
                          total_rows=5, imported_rows=2, skipped_rows=3, duplicates_in_file=1)

    text = format_import_report(report)

    assert "Пропущено всего: 3" in text
    assert "Из них уже в базе: 2" in text
    assert "Одинаковые повторы внутри файла: 1" in text


def test_many_station_report_scrolls_and_last_station_remains_accessible() -> None:
    _qapp()
    station_reports = tuple(StationImportReport(
        wmo_index=27000 + index, station_name=f"Станция {index}",
        period_start=date(2020, 1, 1), period_end=date(2020, 1, 1),
        imported_rows=1, skipped_rows=0,
        new_period_start=date(2020, 1, 1), new_period_end=date(2020, 1, 1),
    ) for index in range(30))
    report = ImportReport(None, None, date(2020, 1, 1), date(2020, 1, 1),
                          total_rows=30, imported_rows=30, skipped_rows=0, stations=station_reports)
    dialog = ImportDialog(get_engine(":memory:"))
    dialog.report_label.setText(format_import_report(report))
    dialog.show()
    QApplication.processEvents()

    scrollbar = dialog.report_area.verticalScrollBar()
    assert scrollbar.maximum() > 0
    scrollbar.setValue(scrollbar.maximum())
    QApplication.processEvents()
    last_line_y = dialog.report_label.height() - dialog.report_label.fontMetrics().lineSpacing()
    assert last_line_y - scrollbar.value() < dialog.report_area.viewport().height()
    assert "Станция 29 (ВМО 27029)" in dialog.report_label.text()
    assert dialog.report_label.text().count("Период: 01.01.2020 — 01.01.2020") == 31
    dialog.close()


def test_repeated_import_repairs_all_missing_stations_without_checkbox(tmp_path: Path) -> None:
    dialog = _multi_station_dialog(tmp_path)
    GgImporter(dialog.engine).import_file(dialog.file_edit.text())
    assert not _calculated_stations(dialog)

    dialog._run_import()

    assert _calculated_stations(dialog) == {27705, 27707}
    assert "Импортировано: 0" in dialog.report_label.text()


def _rp5_day_dialog(tmp_path: Path, old_hours: list[int], new_hours: list[int]) -> ImportDialog:
    from meteoregik.importers.rp5_importer import Rp5Importer
    _qapp()
    engine = get_engine(tmp_path / "sets.sqlite3")
    run_migrations(engine)
    with engine.begin() as conn:
        conn.execute(stations.insert().values(wmo_index=27704, name="Мосальск"))
    paths = []
    for label, hours in (("old", old_hours), ("new", new_hours)):
        wb = Workbook()
        ws = wb.active
        ws.append(["# Метеостанция Мосальск, Россия, WMO_ID=27704, выборка"])
        ws.append(["#"])
        ws.append(["Местное время в Мосальске", "T"])
        for hour in hours:
            ws.append([f"01.01.2020 {hour:02d}:00", hour])
        path = tmp_path / f"{label}.xlsx"
        wb.save(path)
        paths.append(str(path))
    Rp5Importer(engine).import_file(paths[0])
    recompute_range(engine, 27704, date(2020, 1, 1), date(2020, 1, 1))
    dialog = ImportDialog(engine)
    dialog.file_edit.setText(paths[1])
    return dialog


@pytest.mark.parametrize("choice", ["Оставить старые", "изменить на новые"])
def test_rp5_dialog_selects_complete_day_and_recomputes_without_second_question(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, choice: str,
) -> None:
    old_hours, new_hours = [0, 3, 6, 9, 12, 15], [0, 6, 12, 18]
    dialog = _rp5_day_dialog(tmp_path, old_hours, new_hours)
    shown = []
    errors = []

    def choose(box: QMessageBox) -> int:
        assert box.windowTitle() == "Изменение сроков наблюдений"
        assert box.defaultButton().text() == box.escapeButton().text() == "Оставить старые"
        shown.append(box.text())
        _click_button_containing(box, choice)
        return 0

    monkeypatch.setattr(QMessageBox, "exec", choose)
    monkeypatch.setattr(QMessageBox, "critical", lambda parent, title, text: errors.append((title, text)))
    dialog._run_import()

    assert not errors and len(shown) == 1
    assert "01.01.2020" in shown[0] and "7.50" in shown[0] and "9.00" in shown[0]
    assert all(f"{hour:02d}:00" in shown[0] for hour in old_hours + new_hours)
    with dialog.engine.connect() as conn:
        actual = list(conn.scalars(select(rp5_readings.c.obs_time).order_by(rp5_readings.c.obs_time)))
        value = conn.scalar(select(computed_indicators.c.value).where(
            computed_indicators.c.wmo_index == 27704, computed_indicators.c.parameter == "T",
            computed_indicators.c.stat_type == "avg", computed_indicators.c.period_type == "day",
        ))
    assert actual == [time(hour) for hour in (old_hours if choice == "Оставить старые" else new_hours)]
    assert value == (7.5 if choice == "Оставить старые" else 9.0)
    assert dialog.success_label.text() == "Импорт завершён успешно"


def test_rp5_more_complete_day_updates_silently(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    dialog = _rp5_day_dialog(tmp_path, [0, 6, 12, 18], list(range(0, 24, 3)))

    def unexpected(*args: object) -> int:
        pytest.fail("Более полный набор не должен вызывать подтверждений")

    monkeypatch.setattr(QMessageBox, "exec", unexpected)
    dialog._run_import()
    with dialog.engine.connect() as conn:
        assert len(conn.execute(select(rp5_readings)).all()) == 8
        assert conn.scalar(select(computed_indicators.c.value).where(
            computed_indicators.c.parameter == "T", computed_indicators.c.stat_type == "avg",
            computed_indicators.c.period_type == "day")) == 10.5
    assert "Заменено прежних строк: 4" in dialog.report_label.text()

