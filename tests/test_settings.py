from app.settings import Settings


def test_defaults(tmp_path):
    s = Settings(data_dir=str(tmp_path))
    assert s.default_target_lang == "en"
    assert s.openai_model.startswith("gpt")
    assert s.gladia_base_url.startswith("https://")
    assert s.elevenlabs_voice_id


def test_paths_derive_from_data_dir(tmp_path):
    s = Settings(data_dir=str(tmp_path))
    assert s.db_path.endswith("chaplin.db")
    assert s.recordings_dir.endswith("recordings")
    assert str(tmp_path) in s.db_path


def test_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("CHAPLIN_DEFAULT_TARGET_LANG", "es")
    s = Settings(data_dir=str(tmp_path))
    assert s.default_target_lang == "es"
