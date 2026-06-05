import json

from app.keys import KeyStore


def test_get_from_file(tmp_path):
    f = tmp_path / "keys.json"
    f.write_text(json.dumps({"gladia": "gla_secret1234"}))
    ks = KeyStore(str(f))
    assert ks.get("gladia") == "gla_secret1234"
    assert ks.get("openai") is None


def test_env_overrides_file(tmp_path, monkeypatch):
    f = tmp_path / "keys.json"
    f.write_text(json.dumps({"openai": "file_key"}))
    monkeypatch.setenv("OPENAI_API_KEY", "env_key")
    ks = KeyStore(str(f))
    assert ks.get("openai") == "env_key"


def test_set_persists_to_file(tmp_path):
    f = tmp_path / "keys.json"
    ks = KeyStore(str(f))
    ks.set({"elevenlabs": "el_abcdef9999"})
    assert json.loads(f.read_text())["elevenlabs"] == "el_abcdef9999"
    assert ks.get("elevenlabs") == "el_abcdef9999"


def test_set_ignores_none_values(tmp_path):
    f = tmp_path / "keys.json"
    ks = KeyStore(str(f))
    ks.set({"gladia": "g1"})
    ks.set({"gladia": None, "openai": "o1"})
    assert ks.get("gladia") == "g1"
    assert ks.get("openai") == "o1"


def test_keys_set_reports_presence(tmp_path, monkeypatch):
    f = tmp_path / "keys.json"
    f.write_text(json.dumps({"gladia": "g1"}))
    monkeypatch.setenv("OPENAI_API_KEY", "o1")
    ks = KeyStore(str(f))
    status = ks.keys_set()
    assert status == {"gladia": True, "elevenlabs": False, "openai": True}


def test_masked(tmp_path):
    f = tmp_path / "keys.json"
    f.write_text(json.dumps({"gladia": "gla_secret1234"}))
    ks = KeyStore(str(f))
    masked = ks.masked("gladia")
    assert masked.endswith("1234")
    assert "secret" not in masked
