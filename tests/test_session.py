import sys
from pathlib import Path

from meteoregik.db.session import DEFAULT_DB_FILENAME, default_db_path


def test_default_db_path_dev_mode_is_project_root(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)

    path = default_db_path()

    assert path.name == DEFAULT_DB_FILENAME
    # src/meteoregik/db/session.py -> src/meteoregik -> src -> <project root>
    assert path.parent == Path(__file__).resolve().parents[1]


def test_default_db_path_frozen_mode_is_next_to_executable(monkeypatch, tmp_path):
    fake_exe = tmp_path / "meteoregik.exe"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(fake_exe))

    path = default_db_path()

    assert path == tmp_path / DEFAULT_DB_FILENAME
