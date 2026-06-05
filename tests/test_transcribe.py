import pytest

from app.transcribe import transcribe, Transcript


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeClient:
    """Scripts a sequence of responses for post/get calls."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, **kwargs):
        self.calls.append(("POST", url))
        return self._responses.pop(0)

    async def get(self, url, **kwargs):
        self.calls.append(("GET", url))
        return self._responses.pop(0)


@pytest.mark.asyncio
async def test_transcribe_happy_path(tmp_path, monkeypatch):
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"RIFFblah")

    responses = [
        FakeResponse({"audio_url": "https://x/audio"}),                  # upload
        FakeResponse({"result_url": "https://x/result/1"}),              # pre-recorded
        FakeResponse({"status": "done", "result": {"transcription": {
            "full_transcript": "你好",
            "languages": ["zh"],
            "utterances": [{"confidence": 0.88}],
        }}}),                                                            # poll
    ]
    fake = FakeClient(responses)
    monkeypatch.setattr("app.transcribe.httpx.AsyncClient", lambda **k: fake)

    result = await transcribe(str(audio), key="gla_test", base_url="https://api.gladia.io",
                              poll_interval=0)
    assert isinstance(result, Transcript)
    assert result.text == "你好"
    assert result.language == "zh"
    assert abs(result.confidence - 0.88) < 1e-6


@pytest.mark.asyncio
async def test_transcribe_polls_until_done(tmp_path, monkeypatch):
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"RIFFblah")
    responses = [
        FakeResponse({"audio_url": "u"}),
        FakeResponse({"result_url": "r"}),
        FakeResponse({"status": "queued"}),
        FakeResponse({"status": "done", "result": {"transcription": {
            "full_transcript": "hello", "languages": ["en"], "utterances": []}}}),
    ]
    fake = FakeClient(responses)
    monkeypatch.setattr("app.transcribe.httpx.AsyncClient", lambda **k: fake)
    result = await transcribe(str(audio), key="k", base_url="https://api.gladia.io",
                              poll_interval=0)
    assert result.text == "hello"
    assert result.language == "en"
    assert result.confidence is None  # no utterances
