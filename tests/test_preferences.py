from meteoregik.preferences import Preferences


def test_preferences_roundtrip_including_empty_list(tmp_path):
    path = tmp_path / "prefs.json"
    prefs = Preferences(path)
    assert prefs.get("k") is None

    prefs.set("k", [])
    prefs.set("other", ["a", "b"])

    reloaded = Preferences(path)
    assert reloaded.get("k") == []  # пустой выбор отличается от "ничего не выбирали"
    assert reloaded.get("other") == ["a", "b"]


def test_preferences_survive_corrupt_file(tmp_path):
    path = tmp_path / "prefs.json"
    path.write_text("{не json", encoding="utf-8")

    prefs = Preferences(path)
    assert prefs.get("k", "default") == "default"
    prefs.set("k", 1)
    assert Preferences(path).get("k") == 1


def test_preferences_in_memory_when_no_db_file():
    prefs = Preferences.next_to_database(None)
    prefs.set("k", 1)
    assert prefs.get("k") == 1
    assert Preferences.next_to_database(":memory:").get("k") is None


def test_preferences_next_to_database(tmp_path):
    prefs = Preferences.next_to_database(str(tmp_path / "db.sqlite3"))
    prefs.set("k", 2)
    assert (tmp_path / "meteoregik_settings.json").exists()
