import sys
from pathlib import Path

from meteoregik.db import session
from meteoregik.db.session import DEFAULT_DB_FILENAME, default_db_path


def test_default_db_path_dev_mode_is_project_root(monkeypatch):
    monkeypatch.delenv(session.DATA_DIR_ENV, raising=False)
    monkeypatch.delattr(sys, "frozen", raising=False)

    path = default_db_path()

    assert path.name == DEFAULT_DB_FILENAME
    # src/meteoregik/db/session.py -> src/meteoregik -> src -> <project root>
    assert path.parent == Path(__file__).resolve().parents[1]


def test_default_db_path_frozen_mode_is_in_user_documents(monkeypatch, tmp_path):
    """Установленное приложение держит базу в "Документах" пользователя, а
    не рядом с exe: в папку программы писать нельзя, и база должна
    переживать обновление и удаление программы."""
    documents = tmp_path / "Documents"
    monkeypatch.delenv(session.DATA_DIR_ENV, raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "Programs" / "meteoRegiK" / "meteoRegiK.exe"))
    monkeypatch.setattr(session, "documents_dir", lambda: documents)
    monkeypatch.setattr(session, "local_data_dir", lambda: tmp_path / "Local")

    assert default_db_path() == documents / "meteoRegiK" / DEFAULT_DB_FILENAME


def _installed(monkeypatch, tmp_path, *, documents_writable: bool) -> tuple[Path, Path]:
    """Установленное приложение с подменёнными папками; возвращает
    (папка в "Документах", запасная папка)."""
    documents = tmp_path / "Documents"
    local = tmp_path / "Local" / "meteoRegiK" / "data"
    monkeypatch.delenv(session.DATA_DIR_ENV, raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(session, "documents_dir", lambda: documents)
    monkeypatch.setattr(session, "local_data_dir", lambda: local)
    monkeypatch.setattr(session, "_is_writable", lambda folder: documents_writable)
    return documents / "meteoRegiK", local


def test_blocked_documents_fall_back_to_local_folder(monkeypatch, tmp_path):
    """"Контролируемый доступ к папкам" Защитника Windows не даёт
    неподписанной программе писать в "Документы" — приложение падало при
    первом запуске (2026-10-10)."""
    _, local = _installed(monkeypatch, tmp_path, documents_writable=False)

    assert default_db_path() == local / DEFAULT_DB_FILENAME


def test_existing_local_database_wins_over_writable_documents(monkeypatch, tmp_path):
    """Защиту отключили уже после того, как база появилась в запасной папке:
    приложение не должно открыть новую пустую базу в "Документах"."""
    _, local = _installed(monkeypatch, tmp_path, documents_writable=True)
    local.mkdir(parents=True)
    (local / DEFAULT_DB_FILENAME).write_bytes(b"")

    assert default_db_path() == local / DEFAULT_DB_FILENAME


def test_existing_documents_database_wins_without_write_probe(monkeypatch, tmp_path):
    documents, _ = _installed(monkeypatch, tmp_path, documents_writable=False)
    documents.mkdir(parents=True)
    (documents / DEFAULT_DB_FILENAME).write_bytes(b"")

    assert default_db_path() == documents / DEFAULT_DB_FILENAME


def test_is_writable_reports_folder_that_cannot_be_created(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_bytes(b"")  # файл на месте папки — создать её нельзя

    assert session._is_writable(tmp_path / "ok") is True
    assert not (tmp_path / "ok" / ".write_test").exists()
    assert session._is_writable(blocker / "inside") is False


def test_data_dir_environment_variable_overrides_default(monkeypatch, tmp_path):
    monkeypatch.setenv(session.DATA_DIR_ENV, str(tmp_path / "elsewhere"))
    monkeypatch.setattr(sys, "frozen", True, raising=False)

    assert default_db_path() == tmp_path / "elsewhere" / DEFAULT_DB_FILENAME


def test_documents_dir_is_an_existing_folder():
    assert session.documents_dir().is_dir()
