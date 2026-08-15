import pytest

from app.transcribe import transcribe, Transcript, map_gladia_result, transcribe_demo


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


def _done_body():
    return {
        "status": "done",
        "result": {
            "transcription": {
                "full_transcript": "你好 打开设置",
                "languages": ["zh"],
                "utterances": [
                    {"text": "你好", "start": 0.20, "end": 1.10, "language": "zh"},
                    {"text": "打开设置", "start": 1.40, "end": 2.80, "language": "zh"},
                ],
            },
            "translation": {
                "results": [{
                    "languages": ["en"],
                    "utterances": [
                        {"text": "Hello", "start": 0.20, "end": 1.10},
                        {"text": "Open Settings", "start": 1.40, "end": 2.80},
                    ],
                }],
            },
        },
    }


def test_map_gladia_result_matches_timestamps():
    dt = map_gladia_result(_done_body(), target_lang="en")
    assert dt.language == "zh"
    assert len(dt.utterances) == 2
    assert dt.utterances[0].original_text == "你好"
    assert dt.utterances[0].english_text == "Hello"
    assert dt.utterances[0].start_s == 0.20
    assert dt.utterances[1].english_text == "Open Settings"


def test_map_gladia_result_unmatched_falls_back_to_original():
    body = _done_body()
    body["result"]["translation"]["results"][0]["utterances"][1] = {
        "text": "???", "start": 9.0, "end": 9.5,
    }
    dt = map_gladia_result(body, target_lang="en")
    assert dt.utterances[1].english_text == "打开设置"


def test_map_gladia_result_missing_translation_uses_original():
    body = _done_body()
    del body["result"]["translation"]
    dt = map_gladia_result(body, target_lang="en")
    assert dt.utterances[0].english_text == "你好"


def test_map_gladia_result_full_transcript_fallback():
    body = {
        "status": "done",
        "result": {
            "transcription": {
                "full_transcript": "hello there",
                "languages": ["en"],
                "utterances": [],
            },
        },
    }
    dt = map_gladia_result(body, target_lang="en")
    assert len(dt.utterances) == 1
    assert dt.utterances[0].original_text == "hello there"
    assert dt.utterances[0].start_s == 0.0


@pytest.mark.asyncio
async def test_transcribe_demo_sends_translation_config(tmp_path, monkeypatch):
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"RIFFblah")
    done = _done_body()
    captured = {}

    class CapturingClient(FakeClient):
        async def post(self, url, **kwargs):
            self.calls.append(("POST", url, kwargs.get("json")))
            if "json" in kwargs:
                captured["config"] = kwargs["json"]
            return self._responses.pop(0)

    responses = [
        FakeResponse({"audio_url": "https://x/audio"}),
        FakeResponse({"result_url": "https://x/result/1"}),
        FakeResponse(done),
    ]
    fake = CapturingClient(responses)
    monkeypatch.setattr("app.transcribe.httpx.AsyncClient", lambda **k: fake)
    result = await transcribe_demo(
        str(audio), key="gla_test", base_url="https://api.gladia.io",
        target_lang="en", poll_interval=0,
    )
    assert captured["config"]["translation"] is True
    assert captured["config"]["translation_config"]["model"] == "enhanced"
    assert captured["config"]["translation_config"]["match_original_utterances"] is True
    assert captured["config"]["detect_language"] is True
    assert result.utterances[0].english_text == "Hello"


@pytest.mark.asyncio
async def test_transcribe_demo_forced_language_disables_detect(tmp_path, monkeypatch):
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"RIFFblah")
    captured = {}

    class CapturingClient(FakeClient):
        async def post(self, url, **kwargs):
            if "json" in kwargs:
                captured["config"] = kwargs["json"]
            return self._responses.pop(0)

    responses = [
        FakeResponse({"audio_url": "u"}),
        FakeResponse({"result_url": "r"}),
        FakeResponse(_done_body()),
    ]
    fake = CapturingClient(responses)
    monkeypatch.setattr("app.transcribe.httpx.AsyncClient", lambda **k: fake)
    await transcribe_demo(str(audio), key="k", base_url="https://api.gladia.io",
                          target_lang="en", language="zh", poll_interval=0)
    assert captured["config"]["detect_language"] is False
    assert captured["config"]["language"] == "zh"
