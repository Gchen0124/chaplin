import pytest

from app.tts import synthesize


class FakeResponse:
    def __init__(self, content):
        self.content = content
        self.status_code = 200

    def raise_for_status(self):
        pass


class FakeClient:
    def __init__(self, content):
        self._content = content
        self.last_url = None
        self.last_json = None
        self.last_headers = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, headers=None, json=None):
        self.last_url = url
        self.last_json = json
        self.last_headers = headers
        return FakeResponse(self._content)


@pytest.mark.asyncio
async def test_synthesize_returns_audio_bytes(monkeypatch):
    fake = FakeClient(b"ID3audio-bytes")
    monkeypatch.setattr("app.tts.httpx.AsyncClient", lambda **k: fake)
    data = await synthesize("Hello there.", voice_id="voice123", key="el_key",
                            model_id="eleven_multilingual_v2")
    assert data == b"ID3audio-bytes"
    assert "voice123" in fake.last_url
    assert fake.last_json["text"] == "Hello there."
    assert fake.last_headers["xi-api-key"] == "el_key"
