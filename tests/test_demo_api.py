import io
import json

from fastapi.testclient import TestClient

from app.api import create_app
from app.keys import KeyStore
from app.settings import Settings
from app.store import Store
from app.transcribe import DemoTranscript, RawUtterance


def _fake_vsr():
    class V:
        def read_lips(self, p):
            raise AssertionError("VSR must not run on the demo path")
    return V()


async def fake_transcribe_demo(audio_path, key, base_url, **kw):
    return DemoTranscript(language="zh", utterances=[
        RawUtterance(start_s=0.2, end_s=1.1, original_text="你好",
                     english_text="Hello", source_lang="zh"),
        RawUtterance(start_s=1.4, end_s=2.8, original_text="打开设置",
                     english_text="Open Settings", source_lang="zh"),
    ])


async def fake_refine(text, source_lang, target_lang, *, client, model):
    from app.schemas import LanguageTrainerOutput
    return LanguageTrainerOutput(
        source_language=source_lang, original_text=text,
        refined_text=text if text.endswith(".") else text + ".",
        highlights=[], explanation="ok",
    )


async def fake_synthesize(text, voice_id, key, model_id="m"):
    return b"ID3fake-audio"


async def fake_export_demo(**kwargs):
    import os
    demo_dir = kwargs["demo_dir"]
    out = os.path.join(demo_dir, "export.mp4")
    with open(out, "wb") as fh:
        fh.write(b"ftypfake")
    return out


def build_demo_client(tmp_path, keys_payload=None, export_fn=fake_export_demo):
    settings = Settings(data_dir=str(tmp_path))
    store = Store(settings.db_path)
    store.init_db()
    keyfile = tmp_path / "keys.json"
    if keys_payload:
        keyfile.write_text(json.dumps(keys_payload))
    keys = KeyStore(str(keyfile))
    app = create_app(
        settings=settings, store=store, keys=keys, vsr=_fake_vsr(),
        refine_fn=fake_refine, synthesize_fn=fake_synthesize,
        transcribe_demo_fn=fake_transcribe_demo, export_demo_fn=export_fn,
        make_llm_client=lambda key: object(),
    )
    return TestClient(app), store


def test_demo_page_served(tmp_path):
    client, _ = build_demo_client(tmp_path)
    resp = client.get("/demo")
    assert resp.status_code == 200
    assert b"Demo Studio" in resp.content


def test_post_demo_happy_path(tmp_path, monkeypatch):
    monkeypatch.setattr("app.api.audio.has_audio", lambda p: True)
    monkeypatch.setattr("app.api.audio.extract_audio", lambda v, o: True)
    monkeypatch.setattr("app.api.audio.probe_duration", lambda p: 4.0)
    client, store = build_demo_client(
        tmp_path, keys_payload={"gladia": "g", "openai": "o"})
    files = {"file": ("screen.webm", io.BytesIO(b"fakevideo"), "video/webm")}
    resp = client.post("/api/v1/demos", files=files,
                       data={"source_lang": "auto", "target_lang": "en"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "transcribed"
    assert len(body["utterances"]) == 2
    assert body["utterances"][0]["start_s"] == 0.2
    assert body["utterances"][0]["english_text"].endswith(".")
    assert store.get_demo(body["id"])["screen_path"].endswith("screen.webm")


def test_post_demo_saves_before_missing_key(tmp_path, monkeypatch):
    monkeypatch.setattr("app.api.audio.probe_duration", lambda p: 4.0)
    client, store = build_demo_client(tmp_path)
    files = {"file": ("screen.webm", io.BytesIO(b"fakevideo"), "video/webm")}
    resp = client.post("/api/v1/demos", files=files)
    assert resp.status_code == 400
    assert "gladia" in resp.json()["detail"].lower()
    with store._conn() as conn:
        assert len(conn.execute("SELECT id FROM demos").fetchall()) == 1


def test_patch_utterances_ignores_timestamps(tmp_path, monkeypatch):
    monkeypatch.setattr("app.api.audio.has_audio", lambda p: True)
    monkeypatch.setattr("app.api.audio.extract_audio", lambda v, o: True)
    monkeypatch.setattr("app.api.audio.probe_duration", lambda p: 4.0)
    client, _ = build_demo_client(
        tmp_path, keys_payload={"gladia": "g", "openai": "o"})
    created = client.post(
        "/api/v1/demos",
        files={"file": ("screen.webm", io.BytesIO(b"x"), "video/webm")},
    ).json()
    uid = created["utterances"][0]["id"]
    resp = client.patch(
        f"/api/v1/demos/{created['id']}/utterances",
        json={"utterances": [{"id": uid, "english_text": "Hi there."}]},
    )
    assert resp.status_code == 200
    assert resp.json()["utterances"][0]["english_text"] == "Hi there."
    assert resp.json()["utterances"][0]["start_s"] == 0.2


def test_export_returns_mp4(tmp_path, monkeypatch):
    monkeypatch.setattr("app.api.audio.has_audio", lambda p: True)
    monkeypatch.setattr("app.api.audio.extract_audio", lambda v, o: True)
    monkeypatch.setattr("app.api.audio.probe_duration", lambda p: 4.0)
    client, _ = build_demo_client(
        tmp_path, keys_payload={"gladia": "g", "openai": "o", "elevenlabs": "e"})
    created = client.post(
        "/api/v1/demos",
        files={"file": ("screen.webm", io.BytesIO(b"x"), "video/webm")},
    ).json()
    resp = client.post(f"/api/v1/demos/{created['id']}/export")
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("video/")
    assert resp.content.startswith(b"ftyp") or b"ftyp" in resp.content
    got = client.get(f"/api/v1/demos/{created['id']}/export")
    assert got.status_code == 200


def test_get_export_404_before_build(tmp_path, monkeypatch):
    monkeypatch.setattr("app.api.audio.has_audio", lambda p: True)
    monkeypatch.setattr("app.api.audio.extract_audio", lambda v, o: True)
    monkeypatch.setattr("app.api.audio.probe_duration", lambda p: 4.0)
    client, _ = build_demo_client(
        tmp_path, keys_payload={"gladia": "g", "openai": "o"})
    created = client.post(
        "/api/v1/demos",
        files={"file": ("screen.webm", io.BytesIO(b"x"), "video/webm")},
    ).json()
    assert client.get(f"/api/v1/demos/{created['id']}/export").status_code == 404


def test_refine_failure_is_warning_not_500(tmp_path, monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("llm down")
    monkeypatch.setattr("app.api.audio.has_audio", lambda p: True)
    monkeypatch.setattr("app.api.audio.extract_audio", lambda v, o: True)
    monkeypatch.setattr("app.api.audio.probe_duration", lambda p: 4.0)
    settings = Settings(data_dir=str(tmp_path))
    store = Store(settings.db_path)
    store.init_db()
    keyfile = tmp_path / "keys.json"
    keyfile.write_text(json.dumps({"gladia": "g", "openai": "o"}))
    app = create_app(
        settings=settings, store=store, keys=KeyStore(str(keyfile)),
        vsr=_fake_vsr(), refine_fn=boom, synthesize_fn=fake_synthesize,
        transcribe_demo_fn=fake_transcribe_demo, export_demo_fn=fake_export_demo,
        make_llm_client=lambda key: object(),
    )
    client = TestClient(app)
    resp = client.post(
        "/api/v1/demos",
        files={"file": ("screen.webm", io.BytesIO(b"x"), "video/webm")},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["utterances"][0]["english_text"] == "Hello"
    assert resp.json()["warning"]
