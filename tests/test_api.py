import io
import json

import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.store import Store
from app.keys import KeyStore
from app.settings import Settings


def build_client(tmp_path, fake_services, keys_payload=None):
    settings = Settings(data_dir=str(tmp_path))
    store = Store(settings.db_path)
    store.init_db()
    keyfile = tmp_path / "keys.json"
    if keys_payload:
        keyfile.write_text(json.dumps(keys_payload))
    keys = KeyStore(str(keyfile))
    app = create_app(settings=settings, store=store, keys=keys,
                     vsr=fake_services["vsr"],
                     transcribe_fn=fake_services["transcribe"],
                     refine_fn=fake_services["refine"],
                     synthesize_fn=fake_services["synthesize"],
                     make_llm_client=lambda key: object())
    return TestClient(app), store


def test_healthz(tmp_path, fake_services):
    client, _ = build_client(tmp_path, fake_services)
    assert client.get("/healthz").json()["status"] == "ok"


def test_config_reports_keys(tmp_path, fake_services):
    client, _ = build_client(tmp_path, fake_services, keys_payload={"gladia": "g"})
    cfg = client.get("/api/v1/config").json()
    assert cfg["keys_set"]["gladia"] is True
    assert cfg["keys_set"]["openai"] is False
    assert cfg["default_lang"] == "en"


def test_post_keys_persists(tmp_path, fake_services):
    client, _ = build_client(tmp_path, fake_services)
    resp = client.post("/api/v1/keys", json={"openai": "sk-test1234"})
    assert resp.status_code == 200
    assert resp.json()["keys_set"]["openai"] is True


def test_sessions_requires_keys(tmp_path, fake_services, monkeypatch):
    monkeypatch.setattr("app.api.audio.has_audio", lambda p: True)
    monkeypatch.setattr("app.api.audio.extract_audio", lambda v, o: True)
    monkeypatch.setattr("app.api.audio.probe_duration", lambda p: 3.0)
    client, _ = build_client(tmp_path, fake_services)  # no keys set
    files = {"file": ("clip.webm", io.BytesIO(b"fakevideo"), "video/webm")}
    resp = client.post("/api/v1/sessions", files=files, data={"target_lang": "en"})
    assert resp.status_code == 400
    assert "gladia" in resp.json()["detail"].lower() or "openai" in resp.json()["detail"].lower()


def test_sessions_happy_path_audio(tmp_path, fake_services, monkeypatch):
    monkeypatch.setattr("app.api.audio.has_audio", lambda p: True)
    monkeypatch.setattr("app.api.audio.extract_audio", lambda v, o: True)
    monkeypatch.setattr("app.api.audio.probe_duration", lambda p: 3.0)
    client, store = build_client(tmp_path, fake_services,
                                 keys_payload={"gladia": "g", "openai": "o"})
    files = {"file": ("clip.webm", io.BytesIO(b"fakevideo"), "video/webm")}
    resp = client.post("/api/v1/sessions", files=files, data={"target_lang": "en"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["original_text"] == "你好世界"      # from fake transcribe
    assert body["refined_text"] == "Hello, world."  # from fake refine
    assert body["vsr_raw_text"] == "HELLO WORLD"    # from fake vsr
    assert body["source_lang"] == "zh"
    assert body["input_source"] == "audio"
    # persisted
    assert len(store.list_sessions()) == 1
    assert len(store.list_vocab()) == 1


def test_sessions_silent_falls_back_to_lip(tmp_path, fake_services, monkeypatch):
    monkeypatch.setattr("app.api.audio.has_audio", lambda p: False)
    monkeypatch.setattr("app.api.audio.probe_duration", lambda p: 3.0)
    client, store = build_client(tmp_path, fake_services,
                                 keys_payload={"openai": "o"})  # gladia not needed
    files = {"file": ("clip.webm", io.BytesIO(b"fakevideo"), "video/webm")}
    resp = client.post("/api/v1/sessions", files=files, data={"target_lang": "en"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["input_source"] == "lip"
    assert body["original_text"] == "HELLO WORLD"  # lip-read becomes the input
    assert body["source_lang"] == "en"


def test_tts_endpoint_returns_audio(tmp_path, fake_services):
    client, _ = build_client(tmp_path, fake_services, keys_payload={"elevenlabs": "e"})
    resp = client.post("/api/v1/tts", json={"text": "Hello."})
    assert resp.status_code == 200
    assert resp.content == b"ID3fake-audio"
    assert resp.headers["content-type"] == "audio/mpeg"


def _parse_ndjson(text):
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def test_sessions_stream_emits_events_in_order(tmp_path, fake_services, monkeypatch):
    monkeypatch.setattr("app.api.audio.has_audio", lambda p: True)
    monkeypatch.setattr("app.api.audio.extract_audio", lambda v, o: True)
    monkeypatch.setattr("app.api.audio.probe_duration", lambda p: 3.0)
    client, store = build_client(tmp_path, fake_services,
                                 keys_payload={"gladia": "g", "openai": "o"})
    files = {"file": ("clip.webm", io.BytesIO(b"fakevideo"), "video/webm")}
    resp = client.post("/api/v1/sessions/stream", files=files,
                       data={"target_lang": "en", "source_lang": "auto"})
    assert resp.status_code == 200
    events = _parse_ndjson(resp.text)
    types = [e["type"] for e in events]
    assert "lip" in types and "original" in types and "refined" in types
    assert types[-1] == "done"
    by_type = {e["type"]: e for e in events}
    assert by_type["original"]["text"] == "你好世界"
    assert by_type["refined"]["text"] == "Hello, world."
    assert "stage_ms" in by_type["lip"]
    assert len(store.list_sessions()) == 1


def test_sessions_stream_forced_language_skips_autodetect(tmp_path, fake_services, monkeypatch):
    monkeypatch.setattr("app.api.audio.has_audio", lambda p: True)
    monkeypatch.setattr("app.api.audio.extract_audio", lambda v, o: True)
    monkeypatch.setattr("app.api.audio.probe_duration", lambda p: 3.0)
    captured = {}

    async def capturing_transcribe(audio_path, key, base_url, **kw):
        captured["language"] = kw.get("language")
        from app.transcribe import Transcript
        return Transcript(text="你好", language="zh", confidence=0.9)

    fake_services["transcribe"] = capturing_transcribe
    client, _ = build_client(tmp_path, fake_services,
                             keys_payload={"gladia": "g", "openai": "o"})
    files = {"file": ("clip.webm", io.BytesIO(b"fakevideo"), "video/webm")}
    resp = client.post("/api/v1/sessions/stream", files=files,
                       data={"target_lang": "en", "source_lang": "zh"})
    events = _parse_ndjson(resp.text)
    by_type = {e["type"]: e for e in events}
    assert captured["language"] == "zh"            # forced, not auto-detected
    assert by_type["original"]["source_lang"] == "zh"


def test_sessions_stream_silent_falls_back_to_lip(tmp_path, fake_services, monkeypatch):
    monkeypatch.setattr("app.api.audio.has_audio", lambda p: False)
    monkeypatch.setattr("app.api.audio.probe_duration", lambda p: 3.0)
    client, _ = build_client(tmp_path, fake_services, keys_payload={"openai": "o"})
    files = {"file": ("clip.webm", io.BytesIO(b"fakevideo"), "video/webm")}
    resp = client.post("/api/v1/sessions/stream", files=files,
                       data={"target_lang": "en", "source_lang": "auto"})
    events = _parse_ndjson(resp.text)
    by_type = {e["type"]: e for e in events}
    assert by_type["original"]["from_lip"] is True
    assert by_type["original"]["text"] == "HELLO WORLD"
    assert by_type["done"]["input_source"] == "lip"


def test_vocab_list_and_star(tmp_path, fake_services, monkeypatch):
    monkeypatch.setattr("app.api.audio.has_audio", lambda p: True)
    monkeypatch.setattr("app.api.audio.extract_audio", lambda v, o: True)
    monkeypatch.setattr("app.api.audio.probe_duration", lambda p: 3.0)
    client, _ = build_client(tmp_path, fake_services,
                             keys_payload={"gladia": "g", "openai": "o"})
    files = {"file": ("clip.webm", io.BytesIO(b"fakevideo"), "video/webm")}
    client.post("/api/v1/sessions", files=files, data={"target_lang": "en"})
    vocab = client.get("/api/v1/vocab").json()
    assert len(vocab) == 1
    vid = vocab[0]["id"]
    starred = client.patch(f"/api/v1/vocab/{vid}", json={"starred": True}).json()
    assert starred["starred"] is True
    assert len(client.get("/api/v1/vocab?starred=true").json()) == 1
