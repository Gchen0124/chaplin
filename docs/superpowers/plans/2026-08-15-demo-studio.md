# Demo Studio Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `/demo` page to Chaplin that records the screen once, turns timestamped Gladia utterances into editable English lines, and exports an MP4 with an ElevenLabs voice track and burned-in captions.

**Architecture:** Same FastAPI process as the Language Trainer. New `demos` / `demo_utterances` tables and `data/demos/<id>/` files. After Stop, `transcribe_demo()` calls Gladia pre-recorded with `translation` + `match_original_utterances`. The trainer polishes each English line. `export.py` aligns per-utterance TTS to Gladia timestamps and ffmpeg-muxes screen + voice + ASS captions. Trainer routes and VSR are untouched.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, sqlite3, httpx, ffmpeg, Gladia pre-recorded, existing trainer LLM, ElevenLabs TTS, vanilla HTML/CSS/JS.

**Spec:** `docs/superpowers/specs/2026-08-13-demo-studio-design.md`

## Global Constraints

- Do not alter `sessions` / `vocab` tables or Language Trainer routes.
- Do not call VSR on the demo path.
- Existing `transcribe()` return type stays `Transcript` (text, language, confidence).
- Gladia timestamps (`start_s`, `end_s`) are immutable after insert. Edits change only `english_text`.
- Persist `screen.webm` + a `demos` row (`status=saved`) before any Gladia / LLM / TTS call.
- Original mic is dropped on export. MP4 audio is English TTS only.
- Webcam is preview-only; never added to `MediaRecorder`.
- One ElevenLabs voice: `settings.elevenlabs_voice_id`.
- Missing key → HTTP 400 naming the key. Empty transcript → 422 `stage=gladia`.
- Trainer failure is non-fatal: keep Gladia English, set `DemoDraft.warning`.
- Demo routes register **before** `app.mount("/", StaticFiles(...))`.
- No new dependencies. No new env vars.

---

## File Structure

```
chaplin/
  app/
    schemas.py          # + DemoUtterance, DemoDraft, UtteranceEdit, UtterancesPatch
    settings.py         # + demos_dir property
    store.py            # + demos / demo_utterances CRUD
    transcribe.py       # + RawUtterance, DemoTranscript, map_gladia_result, transcribe_demo
    trainer.py          # reused, unchanged
    tts.py              # reused, unchanged
    export.py           # NEW: plan_alignment, wrap_caption, write_ass, mux_demo
    api.py              # + /demo and /api/v1/demos* ; trainer routes untouched
  static/
    demo.html           # NEW
    demo.js             # NEW
    index.html          # nav + Demo
    review.html         # nav + Demo
    style.css           # demo table / un-mirrored screen preview
  tests/
    test_schemas.py     # + demo models
    test_store.py       # + demo CRUD
    test_transcribe.py  # + mapper + transcribe_demo HTTP
    test_demo_align.py  # NEW
    test_demo_api.py    # NEW
  docs/superpowers/specs/2026-08-13-demo-studio-design.md
  docs/superpowers/plans/2026-08-15-demo-studio.md
```

Untouched: `chaplin.py`, `pipelines/`, `configs/`, `espnet/`, `hydra_configs/`, `app/vsr.py`, `app/trainer.py`, `app/tts.py`.

---

### Task 1: Schemas, settings, store

**Files:**
- Modify: `app/schemas.py`
- Modify: `app/settings.py`
- Modify: `app/store.py`
- Test: `tests/test_schemas.py`
- Test: `tests/test_store.py`

**Interfaces:**
- Consumes: existing `Store.init_db()`, `Store._conn()`, `_now()`
- Produces:
  - `DemoUtterance(id: str, idx: int, start_s: float, end_s: float, original_text: str, english_text: str, source_lang: str)`
  - `DemoDraft(id: str, created_at: str, source_lang: str, target_lang: str, duration_s: float, status: str, warning: str | None = None, utterances: list[DemoUtterance])`
  - `UtteranceEdit(id: str, english_text: str)`
  - `UtterancesPatch(utterances: list[UtteranceEdit])`
  - `Settings.demos_dir -> str` (`os.path.join(self.data_dir, "demos")`)
  - `Store.create_demo(*, source_lang, target_lang, duration_s, screen_path, audio_path=None, status="saved", error=None) -> str`
  - `Store.get_demo(demo_id: str) -> dict | None` (includes `utterances: list[dict]`)
  - `Store.update_demo(demo_id: str, **fields) -> None`
  - `Store.replace_utterances(demo_id: str, utterances: list[dict]) -> None` — each dict has `start_s`, `end_s`, `original_text`, `english_text`, `source_lang`; assigns `id` + `idx`
  - `Store.update_utterance_texts(demo_id: str, edits: list[tuple[str, str]]) -> None` — `(utterance_id, english_text)` only; ignore unknown ids; never touch timestamps

- [ ] **Step 1: Write the failing schema tests**

Append to `tests/test_schemas.py`:

```python
from app.schemas import DemoDraft, DemoUtterance, UtteranceEdit, UtterancesPatch


def test_demo_utterance_and_draft():
    u = DemoUtterance(
        id="u1", idx=0, start_s=1.0, end_s=2.5,
        original_text="打开设置", english_text="Open Settings.", source_lang="zh",
    )
    draft = DemoDraft(
        id="d1", created_at="2026-08-15T00:00:00+00:00",
        source_lang="zh", target_lang="en", duration_s=8.0,
        status="transcribed", warning=None, utterances=[u],
    )
    assert draft.utterances[0].english_text == "Open Settings."
    assert draft.warning is None


def test_utterances_patch_only_allows_english_text():
    patch = UtterancesPatch(utterances=[UtteranceEdit(id="u1", english_text="Open settings.")])
    assert patch.utterances[0].id == "u1"
    dumped = UtteranceEdit(id="u1", english_text="x").model_dump()
    assert set(dumped) == {"id", "english_text"}
```

- [ ] **Step 2: Run schema tests to verify they fail**

Run: `pytest tests/test_schemas.py::test_demo_utterance_and_draft tests/test_schemas.py::test_utterances_patch_only_allows_english_text -v`

Expected: FAIL with `ImportError` / `cannot import name 'DemoDraft'`

- [ ] **Step 3: Add the models and `demos_dir`**

Append to `app/schemas.py`:

```python
class DemoUtterance(BaseModel):
    id: str
    idx: int
    start_s: float
    end_s: float
    original_text: str
    english_text: str
    source_lang: str


class DemoDraft(BaseModel):
    id: str
    created_at: str
    source_lang: str
    target_lang: str
    duration_s: float
    status: str
    warning: str | None = None
    utterances: list[DemoUtterance]


class UtteranceEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    english_text: str


class UtterancesPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    utterances: list[UtteranceEdit]
```

Add to `Settings` in `app/settings.py` after `recordings_dir`:

```python
    @property
    def demos_dir(self) -> str:
        return os.path.join(self.data_dir, "demos")
```

- [ ] **Step 4: Write the failing store tests**

Append to `tests/test_store.py`:

```python
def test_create_and_get_demo(tmp_path):
    s = make_store(tmp_path)
    did = s.create_demo(
        source_lang="auto", target_lang="en", duration_s=5.0,
        screen_path="/d/screen.webm", audio_path=None, status="saved",
    )
    row = s.get_demo(did)
    assert row["id"] == did
    assert row["status"] == "saved"
    assert row["utterances"] == []
    assert row["export_path"] is None


def test_replace_and_edit_utterances_ignores_timestamps(tmp_path):
    s = make_store(tmp_path)
    did = s.create_demo(
        source_lang="zh", target_lang="en", duration_s=5.0,
        screen_path="/d/screen.webm", status="saved",
    )
    s.replace_utterances(did, [
        {"start_s": 0.2, "end_s": 1.8, "original_text": "你好",
         "english_text": "Hello.", "source_lang": "zh"},
        {"start_s": 2.0, "end_s": 3.4, "original_text": "打开设置",
         "english_text": "Open Settings.", "source_lang": "zh"},
    ])
    row = s.get_demo(did)
    assert [u["idx"] for u in row["utterances"]] == [0, 1]
    uid = row["utterances"][0]["id"]
    s.update_utterance_texts(did, [(uid, "Hi there."), ("missing", "nope")])
    row = s.get_demo(did)
    assert row["utterances"][0]["english_text"] == "Hi there."
    assert row["utterances"][0]["start_s"] == 0.2
    assert row["utterances"][1]["english_text"] == "Open Settings."


def test_update_demo_status(tmp_path):
    s = make_store(tmp_path)
    did = s.create_demo(
        source_lang="en", target_lang="en", duration_s=2.0,
        screen_path="/d/screen.webm", status="saved",
    )
    s.update_demo(did, status="transcribed", source_lang="en", warning="polish skipped")
    row = s.get_demo(did)
    assert row["status"] == "transcribed"
    assert row["warning"] == "polish skipped"
```

- [ ] **Step 5: Run store tests to verify they fail**

Run: `pytest tests/test_store.py::test_create_and_get_demo -v`

Expected: FAIL with `AttributeError: 'Store' object has no attribute 'create_demo'`

- [ ] **Step 6: Implement store methods**

In `Store.init_db()`, append to the `executescript` string (do not change existing `sessions` / `vocab` DDL):

```sql
                CREATE TABLE IF NOT EXISTS demos (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    source_lang TEXT NOT NULL,
                    target_lang TEXT NOT NULL,
                    duration_s REAL,
                    screen_path TEXT NOT NULL,
                    audio_path TEXT,
                    export_path TEXT,
                    status TEXT NOT NULL,
                    error TEXT,
                    warning TEXT
                );
                CREATE TABLE IF NOT EXISTS demo_utterances (
                    id TEXT PRIMARY KEY,
                    demo_id TEXT NOT NULL,
                    idx INTEGER NOT NULL,
                    start_s REAL NOT NULL,
                    end_s REAL NOT NULL,
                    original_text TEXT NOT NULL,
                    english_text TEXT NOT NULL,
                    source_lang TEXT NOT NULL,
                    FOREIGN KEY (demo_id) REFERENCES demos(id)
                );
```

Add methods on `Store`:

```python
    def create_demo(self, *, source_lang, target_lang, duration_s, screen_path,
                    audio_path=None, status="saved", error=None, warning=None) -> str:
        did = uuid.uuid4().hex
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO demos (id, created_at, source_lang, target_lang,
                   duration_s, screen_path, audio_path, export_path, status, error, warning)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (did, _now(), source_lang, target_lang, duration_s, screen_path,
                 audio_path, None, status, error, warning),
            )
        return did

    def get_demo(self, demo_id: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM demos WHERE id = ?", (demo_id,)).fetchone()
            if row is None:
                return None
            utts = conn.execute(
                "SELECT * FROM demo_utterances WHERE demo_id = ? ORDER BY idx ASC",
                (demo_id,),
            ).fetchall()
        d = dict(row)
        d["utterances"] = [dict(u) for u in utts]
        return d

    def update_demo(self, demo_id: str, **fields) -> None:
        allowed = {"source_lang", "target_lang", "duration_s", "screen_path",
                   "audio_path", "export_path", "status", "error", "warning"}
        sets, vals = [], []
        for k, v in fields.items():
            if k in allowed:
                sets.append(f"{k} = ?")
                vals.append(v)
        if not sets:
            return
        vals.append(demo_id)
        with self._conn() as conn:
            conn.execute(f"UPDATE demos SET {', '.join(sets)} WHERE id = ?", vals)

    def replace_utterances(self, demo_id: str, utterances: list[dict]) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM demo_utterances WHERE demo_id = ?", (demo_id,))
            for idx, u in enumerate(utterances):
                conn.execute(
                    """INSERT INTO demo_utterances
                       (id, demo_id, idx, start_s, end_s, original_text, english_text, source_lang)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (uuid.uuid4().hex, demo_id, idx, u["start_s"], u["end_s"],
                     u["original_text"], u["english_text"], u["source_lang"]),
                )

    def update_utterance_texts(self, demo_id: str, edits: list[tuple[str, str]]) -> None:
        with self._conn() as conn:
            for uid, text in edits:
                conn.execute(
                    """UPDATE demo_utterances SET english_text = ?
                       WHERE id = ? AND demo_id = ?""",
                    (text, uid, demo_id),
                )
```

- [ ] **Step 7: Run Task 1 tests**

Run: `pytest tests/test_schemas.py tests/test_store.py -v`

Expected: PASS (existing trainer tests included)

- [ ] **Step 8: Commit**

```bash
git add app/schemas.py app/settings.py app/store.py tests/test_schemas.py tests/test_store.py
git commit -m "feat(demo): add DemoDraft schemas and demo store tables"
```

---

### Task 2: Gladia utterance mapping and `transcribe_demo`

**Files:**
- Modify: `app/transcribe.py`
- Test: `tests/test_transcribe.py`

**Interfaces:**
- Consumes: existing Gladia upload / pre-recorded / poll loop, `FakeClient` in tests
- Produces:
  - `RawUtterance(start_s: float, end_s: float, original_text: str, english_text: str, source_lang: str)`
  - `DemoTranscript(language: str, utterances: list[RawUtterance])`
  - `MATCH_TOLERANCE_S = 0.05`
  - `map_gladia_result(body: dict, target_lang: str) -> DemoTranscript`
  - `async def transcribe_demo(audio_path: str, key: str, base_url: str, *, target_lang: str = "en", language: str | None = None, poll_interval: float = 1.0, max_polls: int = 60) -> DemoTranscript`

Mapping rules:
- Source rows come from `result.transcription.utterances[]`. Each has `text` (or `transcript`), `start`, `end`, optional `language`.
- If `utterances` is empty but `full_transcript` is non-empty, emit one utterance `start_s=0`, `end_s=0`, text = full transcript.
- Translated rows come from the `result.translation.results[]` entry whose `languages` contains `target_lang` (compare first two chars, case-insensitive). Use that entry's `utterances[]`.
- Match translated utterance to source when `abs(t.start - s.start) <= 0.05` and `abs(t.end - s.end) <= 0.05`. On match, `english_text = translated text`. On miss, `english_text = original_text`.
- `DemoTranscript.language` is `transcription.languages[0]` or `"en"`.
- Per-utterance `source_lang` is the utterance `language` if present, else the transcript language.
- `transcribe()` is unchanged. `transcribe_demo()` sends:

```python
config = {
    "audio_url": audio_url,
    "diarization": False,
    "translation": True,
    "translation_config": {
        "target_languages": [target_lang],
        "model": "enhanced",
        "match_original_utterances": True,
        "lipsync": False,
        "context_adaptation": True,
        "context": "Product demo / software walkthrough narration",
    },
}
```

plus the same forced-language / detect_language logic as `transcribe()`.

- [ ] **Step 1: Write the failing mapper tests**

Append to `tests/test_transcribe.py`:

```python
from app.transcribe import map_gladia_result, transcribe_demo


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
```

- [ ] **Step 2: Run mapper tests to verify they fail**

Run: `pytest tests/test_transcribe.py::test_map_gladia_result_matches_timestamps -v`

Expected: FAIL with `ImportError` / `cannot import name 'map_gladia_result'`

- [ ] **Step 3: Implement `map_gladia_result`**

Add to `app/transcribe.py` (keep `Transcript` and `transcribe()` exactly as they are):

```python
MATCH_TOLERANCE_S = 0.05


class RawUtterance(BaseModel):
    start_s: float
    end_s: float
    original_text: str
    english_text: str
    source_lang: str


class DemoTranscript(BaseModel):
    language: str
    utterances: list[RawUtterance]


def _utt_text(u: dict) -> str:
    return (u.get("text") or u.get("transcript") or "").strip()


def map_gladia_result(body: dict, target_lang: str) -> DemoTranscript:
    tr = (body.get("result") or {}).get("transcription") or {}
    langs = tr.get("languages") or ["en"]
    language = (langs[0] if langs else "en") or "en"
    src = tr.get("utterances") or []
    if not src and (tr.get("full_transcript") or "").strip():
        src = [{"text": tr["full_transcript"].strip(), "start": 0.0, "end": 0.0,
                "language": language}]

    tgt = target_lang.split("-")[0].lower()
    translated = []
    for block in ((body.get("result") or {}).get("translation") or {}).get("results") or []:
        block_langs = [str(x).split("-")[0].lower() for x in (block.get("languages") or [])]
        if tgt in block_langs:
            translated = block.get("utterances") or []
            break

    out = []
    for u in src:
        start = float(u.get("start") or 0.0)
        end = float(u.get("end") or 0.0)
        original = _utt_text(u)
        src_lang = u.get("language") or language
        english = original
        for t in translated:
            if (abs(float(t.get("start") or 0.0) - start) <= MATCH_TOLERANCE_S
                    and abs(float(t.get("end") or 0.0) - end) <= MATCH_TOLERANCE_S):
                text = _utt_text(t)
                if text:
                    english = text
                break
        out.append(RawUtterance(
            start_s=start, end_s=end, original_text=original,
            english_text=english, source_lang=src_lang,
        ))
    return DemoTranscript(language=language, utterances=out)
```

- [ ] **Step 4: Write the failing `transcribe_demo` HTTP test**

Append to `tests/test_transcribe.py`:

```python
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
```

- [ ] **Step 5: Run the HTTP tests to verify they fail**

Run: `pytest tests/test_transcribe.py::test_transcribe_demo_sends_translation_config -v`

Expected: FAIL with `NameError` / `transcribe_demo` not defined

- [ ] **Step 6: Implement `transcribe_demo`**

```python
async def transcribe_demo(audio_path: str, key: str, base_url: str, *,
                          target_lang: str = "en", language: str | None = None,
                          poll_interval: float = 1.0, max_polls: int = 60) -> DemoTranscript:
    headers = {"x-gladia-key": key}
    async with httpx.AsyncClient(timeout=120.0) as client:
        with open(audio_path, "rb") as fh:
            up = await client.post(
                f"{base_url}/v2/upload",
                headers=headers,
                files={"audio": ("audio.wav", fh, "audio/wav")},
            )
        up.raise_for_status()
        audio_url = up.json()["audio_url"]
        config = {
            "audio_url": audio_url,
            "diarization": False,
            "translation": True,
            "translation_config": {
                "target_languages": [target_lang],
                "model": "enhanced",
                "match_original_utterances": True,
                "lipsync": False,
                "context_adaptation": True,
                "context": "Product demo / software walkthrough narration",
            },
        }
        if language:
            config["detect_language"] = False
            config["language"] = language
        else:
            config["detect_language"] = True
        req = await client.post(f"{base_url}/v2/pre-recorded", headers=headers, json=config)
        req.raise_for_status()
        result_url = req.json()["result_url"]
        for _ in range(max_polls):
            poll = await client.get(result_url, headers=headers)
            poll.raise_for_status()
            body = poll.json()
            status = body.get("status")
            if status == "done":
                return map_gladia_result(body, target_lang)
            if status == "error":
                raise RuntimeError(f"Gladia transcription failed: {body}")
            await asyncio.sleep(poll_interval)
        raise RuntimeError("Gladia transcription timed out")
```

- [ ] **Step 7: Run all transcribe tests**

Run: `pytest tests/test_transcribe.py -v`

Expected: PASS, including the original `test_transcribe_happy_path`

- [ ] **Step 8: Commit**

```bash
git add app/transcribe.py tests/test_transcribe.py
git commit -m "feat(demo): map Gladia translated utterances with timestamps"
```

---

### Task 3: Alignment, captions, ffmpeg mux

**Files:**
- Create: `app/export.py`
- Test: `tests/test_demo_align.py`

**Interfaces:**
- Consumes: utterance `start_s` list + clip durations + video duration; edited `english_text`
- Produces:
  - `class Alignment(BaseModel): idx: int; start_s: float; speed: float; duration_s: float`
  - `MAX_ATEMPO = 1.15`
  - `plan_alignment(*, starts: list[float], clip_durations: list[float], video_duration: float, max_atempo: float = 1.15) -> list[Alignment]`
  - `wrap_caption(text: str, width: int = 42) -> str` — break at spaces / CJK, join with `\\N`
  - `ass_timestamp(seconds: float) -> str` — `H:MM:SS.cs`
  - `write_ass(utterances: list[dict], path: str) -> None` — dicts have `start_s`, `end_s`, `english_text`
  - `mux_demo(*, screen_path: str, voice_clips: list[tuple[str, Alignment]], ass_path: str, out_path: str, video_duration: float) -> None`

Alignment rules (window = next start − this start, or `video_duration − start` for the last):
1. If `clip_duration <= window`, `speed = 1.0`, `duration_s = clip_duration`.
2. If `clip_duration > window` and `window > 0`, `speed = min(max_atempo, clip_duration / window)`.
3. After speed, if `clip_duration / speed > window`, hard-cut: `duration_s = window` (never overlap the next start).
4. Never place `start_s` before the utterance start.

ASS:
- PlayResX=1920, PlayResY=1080
- Style `Default`: Arial 48, primary `&H00FFFFFF`, back `&H33000000`, BorderStyle=4, Alignment=2, MarginL/R/V=40
- One `Dialogue` per utterance, times = Gladia `start_s`/`end_s` (not TTS duration)
- Text = `wrap_caption(english_text)`

`mux_demo` builds one ffmpeg command:
- `-i screen_path`
- `-i` each voice clip
- `filter_complex`: for each clip, `[{n}:a]atempo=SPEED,atrim=0:DURATION,adelay=START_MS|START_MS[aN]` (omit atempo when speed==1.0); `amix` all `[aN]` with `duration=longest:normalize=0`; `[0:v]ass=ASS_PATH[v]`
- `-map [v] -map [a] -c:v libx264 -preset medium -crf 20 -c:a aac -b:a 192k -t VIDEO_DURATION -movflags +faststart out_path`
- Raise `RuntimeError` if ffmpeg returncode != 0

- [ ] **Step 1: Write the failing alignment / caption tests**

Create `tests/test_demo_align.py`:

```python
from app.export import plan_alignment, wrap_caption, ass_timestamp, write_ass, MAX_ATEMPO


def test_plan_alignment_pads_short_clip():
    plans = plan_alignment(starts=[0.0, 3.0], clip_durations=[1.0, 0.8], video_duration=5.0)
    assert plans[0].start_s == 0.0
    assert plans[0].speed == 1.0
    assert plans[0].duration_s == 1.0
    assert plans[1].start_s == 3.0


def test_plan_alignment_speeds_up_to_max_atempo():
    # window = 2.0, clip = 2.2 → speed = 1.1
    plans = plan_alignment(starts=[0.0, 2.0], clip_durations=[2.2], video_duration=4.0)
    assert abs(plans[0].speed - 1.1) < 1e-6
    assert plans[0].duration_s <= 2.0 + 1e-6


def test_plan_alignment_hard_cuts_instead_of_overlapping_next():
    # window = 1.0, clip = 2.0 → speed capped at 1.15, still 1.739s → cut to 1.0
    plans = plan_alignment(starts=[0.0, 1.0], clip_durations=[2.0], video_duration=3.0)
    assert plans[0].speed == MAX_ATEMPO
    assert abs(plans[0].duration_s - 1.0) < 1e-6
    assert plans[0].start_s + plans[0].duration_s <= 1.0 + 1e-6


def test_wrap_caption_breaks_near_42():
    text = "This is a long product demo sentence that should wrap onto another line."
    wrapped = wrap_caption(text, width=42)
    assert "\\N" in wrapped
    assert "\\N\\N" not in wrapped
    for line in wrapped.split("\\N"):
        assert len(line) <= 48  # allow a leftover word


def test_ass_timestamp_format():
    assert ass_timestamp(0) == "0:00:00.00"
    assert ass_timestamp(65.2) == "0:01:05.20"


def test_write_ass_uses_gladia_end_not_tts(tmp_path):
    path = tmp_path / "c.ass"
    write_ass([
        {"start_s": 1.0, "end_s": 2.5, "english_text": "Open Settings."},
    ], str(path))
    body = path.read_text(encoding="utf-8")
    assert "Dialogue:" in body
    assert "0:00:01.00" in body
    assert "0:00:02.50" in body
    assert "Open Settings." in body
    assert "BorderStyle=4" in body or "BorderStyle,4" in body or ",4," in body
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_demo_align.py::test_plan_alignment_pads_short_clip -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'app.export'`

- [ ] **Step 3: Implement `app/export.py`**

```python
import os
import subprocess

from pydantic import BaseModel

MAX_ATEMPO = 1.15


class Alignment(BaseModel):
    idx: int
    start_s: float
    speed: float
    duration_s: float


def plan_alignment(*, starts: list[float], clip_durations: list[float],
                   video_duration: float, max_atempo: float = MAX_ATEMPO) -> list[Alignment]:
    n = len(clip_durations)
    out = []
    for i, dur in enumerate(clip_durations):
        start = starts[i] if i < len(starts) else 0.0
        nxt = starts[i + 1] if i + 1 < len(starts) else video_duration
        window = max(0.0, nxt - start)
        speed = 1.0
        placed = dur
        if window > 0 and dur > window:
            speed = min(max_atempo, dur / window)
            placed = dur / speed
            if placed > window:
                placed = window
        out.append(Alignment(idx=i, start_s=start, speed=speed, duration_s=placed))
    return out


def wrap_caption(text: str, width: int = 42) -> str:
    text = (text or "").replace("\n", " ").strip()
    if len(text) <= width:
        return text
    lines, buf = [], ""
    for ch in text:
        buf += ch
        if len(buf) >= width:
            cut = max(buf.rfind(" "), buf.rfind("，"), buf.rfind("。"), buf.rfind(","))
            if cut >= width // 2:
                lines.append(buf[:cut].strip())
                buf = buf[cut + 1:].lstrip()
            else:
                lines.append(buf.strip())
                buf = ""
    if buf.strip():
        lines.append(buf.strip())
    return "\\N".join(lines)


def ass_timestamp(seconds: float) -> str:
    if seconds < 0:
        seconds = 0.0
    cs = int(round(seconds * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


_ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,48,&H00FFFFFF,&H000000FF,&H00000000,&H33000000,0,0,0,0,100,100,0,0,4,0,0,2,40,40,40,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def write_ass(utterances: list[dict], path: str) -> None:
    lines = [_ASS_HEADER]
    for u in utterances:
        text = wrap_caption(u.get("english_text") or "").replace("{", "\\{")
        start = ass_timestamp(float(u["start_s"]))
        end = ass_timestamp(float(u["end_s"]))
        lines.append(f"Dialogue: 0,{start},{end},Default,,0,0,0,,{text}\n")
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.writelines(lines)


def mux_demo(*, screen_path: str, voice_clips: list[tuple[str, Alignment]],
             ass_path: str, out_path: str, video_duration: float) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    cmd = ["ffmpeg", "-y", "-i", screen_path]
    for path, _al in voice_clips:
        cmd += ["-i", path]
    filters = []
    mix_labels = []
    for i, (_path, al) in enumerate(voice_clips):
        delay_ms = max(0, int(round(al.start_s * 1000)))
        chain = f"[{i + 1}:a]"
        if abs(al.speed - 1.0) > 1e-3:
            chain += f"atempo={al.speed:.4f},"
        chain += f"atrim=0:{al.duration_s:.4f},adelay={delay_ms}|{delay_ms}[a{i}]"
        filters.append(chain)
        mix_labels.append(f"[a{i}]")
    if mix_labels:
        filters.append(
            f"{''.join(mix_labels)}amix=inputs={len(mix_labels)}:duration=longest:normalize=0[a]"
        )
        audio_map = "[a]"
    else:
        filters.append("anullsrc=r=44100:cl=stereo[a]")
        audio_map = "[a]"
    ass_escaped = ass_path.replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
    filters.append(f"[0:v]ass='{ass_escaped}'[v]")
    cmd += [
        "-filter_complex", ";".join(filters),
        "-map", "[v]", "-map", audio_map,
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-c:a", "aac", "-b:a", "192k",
        "-t", f"{video_duration:.3f}",
        "-movflags", "+faststart",
        out_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg mux failed: {result.stderr[-800:]}")
```

Add a mux smoke test that only checks the command:

```python
from unittest.mock import patch
from app.export import mux_demo, Alignment


def test_mux_demo_invokes_ffmpeg(tmp_path):
    out = tmp_path / "export.mp4"
    clip = tmp_path / "v0.mp3"
    clip.write_bytes(b"ID3")
    ass = tmp_path / "c.ass"
    ass.write_text("[Script Info]\n")
    with patch("app.export.subprocess.run") as run:
        run.return_value.returncode = 0
        run.return_value.stderr = ""
        mux_demo(
            screen_path="/screen.webm",
            voice_clips=[(str(clip), Alignment(idx=0, start_s=1.0, speed=1.0, duration_s=0.8))],
            ass_path=str(ass),
            out_path=str(out),
            video_duration=5.0,
        )
    args = run.call_args[0][0]
    assert args[0] == "ffmpeg"
    assert "ass=" in " ".join(args)
    assert str(out) == args[-1]
```

- [ ] **Step 4: Run Task 3 tests**

Run: `pytest tests/test_demo_align.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/export.py tests/test_demo_align.py
git commit -m "feat(demo): align TTS clips and burn ASS captions"
```

---

### Task 4: Demo API routes

**Files:**
- Modify: `app/api.py`
- Test: `tests/test_demo_api.py`
- Modify: `tests/conftest.py` (optional extra fakes; prefer passing fns in the test helper)

**Interfaces:**
- Consumes: `Store.create_demo/get_demo/update_demo/replace_utterances/update_utterance_texts`, `transcribe_demo`, `refine`, `synthesize`, `plan_alignment`, `write_ass`, `mux_demo`, `audio.extract_audio`, `audio.probe_duration`, `audio.has_audio`
- Produces (all registered **before** the StaticFiles mount):
  - `GET /demo` → `FileResponse(static/demo.html)`
  - `POST /api/v1/demos` multipart `file`, `source_lang="auto"`, `target_lang=None` → `DemoDraft`
  - `GET /api/v1/demos/{id}` → `DemoDraft` or 404
  - `PATCH /api/v1/demos/{id}/utterances` body `UtterancesPatch` → `DemoDraft`
  - `POST /api/v1/demos/{id}/export` → `video/mp4`
  - `GET /api/v1/demos/{id}/export` → `video/mp4` or 404

`create_app` gains two optional kwargs with defaults so existing tests stay valid:

```python
from app.transcribe import transcribe_demo as default_transcribe_demo
from app.export import export_demo as default_export_demo
```

```python
def create_app(..., transcribe_demo_fn=default_transcribe_demo,
               export_demo_fn=default_export_demo)
```

Add `export_demo` in `app/export.py` (thin orchestrator used by the route):

```python
async def export_demo(*, utterances: list[dict], video_duration: float,
                      screen_path: str, demo_dir: str,
                      synthesize_fn, voice_id: str, tts_key: str, model_id: str) -> str:
    voice_dir = os.path.join(demo_dir, "voice")
    os.makedirs(voice_dir, exist_ok=True)
    clip_paths, durs, starts = [], [], []
    for u in utterances:
        idx = u["idx"]
        dest = os.path.join(voice_dir, f"{idx:03d}.mp3")
        if not (os.path.isfile(dest) and os.path.getsize(dest) > 0):
            data = await synthesize_fn(u["english_text"], voice_id, tts_key, model_id)
            with open(dest, "wb") as fh:
                fh.write(data)
        clip_paths.append(dest)
        starts.append(float(u["start_s"]))
        durs.append(_mp3_duration(dest))
    plans = plan_alignment(starts=starts, clip_durations=durs, video_duration=video_duration)
    ass_path = os.path.join(demo_dir, "captions.ass")
    write_ass(utterances, ass_path)
    out_path = os.path.join(demo_dir, "export.mp4")
    mux_demo(screen_path=screen_path, voice_clips=list(zip(clip_paths, plans)),
             ass_path=ass_path, out_path=out_path, video_duration=video_duration)
    return out_path


def _mp3_duration(path: str) -> float:
    from app.audio import probe_duration
    return probe_duration(path) or 0.5
```

POST `/api/v1/demos` flow:
1. `os.makedirs(settings.demos_dir)`; `did_dir = join(demos_dir, new_id)`; write `screen.webm`.
2. `duration = audio.probe_duration(screen)`; if `< 1.0` → 422.
3. `store.create_demo(..., status="saved")` **before** Gladia.
4. If missing gladia key → 400 `"Missing Gladia API key."` (row already saved).
5. If no audio stream → 422 `{detail: {error, stage: "audio"}}`.
6. Extract `audio.wav`; `transcribe_demo_fn(..., target_lang=target, language=forced)`.
7. If no utterances or all texts empty → 422 `{detail: {error: "No speech detected.", stage: "gladia"}}`.
8. For each raw utterance, call `refine_fn` with:
   - if `english_text == original_text` and source lang ≠ target: `refine(original_text, source_lang, target_lang)`
   - else: `refine(english_text, target_lang, target_lang)`
   - on exception: keep current english_text, collect warning `"polish failed on utterance {idx}"`
9. `replace_utterances`, `update_demo(status="transcribed", source_lang=detected, warning=...)`.
10. Return `_demo_draft(store.get_demo(id))`.

PATCH: 404 if missing; `update_utterance_texts`; return draft.

POST export: 404 if missing; 400 if no elevenlabs key; skip empty `english_text` utterances; on TTS error raise 502 `{detail: {error, stage: "tts", idx}}`; on mux error 502 `{detail: {error, stage: "mux"}}`; `update_demo(export_path=..., status="exported")`; return `FileResponse`.

Helper:

```python
def _demo_draft(row: dict) -> DemoDraft:
    return DemoDraft(
        id=row["id"], created_at=row["created_at"],
        source_lang=row["source_lang"], target_lang=row["target_lang"],
        duration_s=row["duration_s"] or 0.0, status=row["status"],
        warning=row.get("warning"),
        utterances=[DemoUtterance(**{
            "id": u["id"], "idx": u["idx"], "start_s": u["start_s"],
            "end_s": u["end_s"], "original_text": u["original_text"],
            "english_text": u["english_text"], "source_lang": u["source_lang"],
        }) for u in row["utterances"]],
    )
```

- [ ] **Step 1: Write the failing API tests**

Create `tests/test_demo_api.py`:

```python
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
    demo_dir = kwargs["demo_dir"]
    out = __import__("os").path.join(demo_dir, "export.mp4")
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
    client, store = build_demo_client(tmp_path)  # no keys
    files = {"file": ("screen.webm", io.BytesIO(b"fakevideo"), "video/webm")}
    resp = client.post("/api/v1/demos", files=files)
    assert resp.status_code == 400
    assert "gladia" in resp.json()["detail"].lower()
    assert len(store._conn().execute("SELECT id FROM demos").fetchall()) == 1


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
```

- [ ] **Step 2: Run API tests to verify they fail**

Run: `pytest tests/test_demo_api.py::test_post_demo_happy_path -v`

Expected: FAIL (`create_app() got an unexpected keyword argument 'transcribe_demo_fn'` or 404)

- [ ] **Step 3: Implement routes and `export_demo`**

1. Add `export_demo` + `_mp3_duration` to `app/export.py` as specified above.
2. Update `create_app` signature and register routes **immediately before** the `if os.path.isdir(STATIC_DIR)` block. Import `FileResponse`, `DemoDraft`, `UtterancesPatch`.
3. Persist the screen file under `settings.demos_dir / <id> / screen.webm` (generate id first, then `create_demo` with that id — **change `create_demo` to accept optional `demo_id=`** so the folder name matches the row; if you already shipped Task 1 without `demo_id`, add the optional kwarg now and keep auto-uuid as default).

`create_demo` adjustment:

```python
    def create_demo(self, *, source_lang, target_lang, duration_s, screen_path,
                    audio_path=None, status="saved", error=None, warning=None,
                    demo_id=None) -> str:
        did = demo_id or uuid.uuid4().hex
```

POST handler sketch (put the real implementation in `api.py`; keep it linear, no VSR):

```python
    @app.get("/demo")
    def demo_page():
        return FileResponse(os.path.join(STATIC_DIR, "demo.html"))

    @app.post("/api/v1/demos", response_model=DemoDraft)
    async def create_demo_route(file: UploadFile = File(...),
                                target_lang: str = Form(None),
                                source_lang: str = Form("auto")):
        ...

    @app.get("/api/v1/demos/{demo_id}", response_model=DemoDraft)
    def get_demo_route(demo_id: str):
        row = store.get_demo(demo_id)
        if not row:
            raise HTTPException(404, "Demo not found.")
        return _demo_draft(row)

    @app.patch("/api/v1/demos/{demo_id}/utterances", response_model=DemoDraft)
    def patch_utterances(demo_id: str, body: UtterancesPatch):
        ...

    @app.post("/api/v1/demos/{demo_id}/export")
    async def export_demo_route(demo_id: str):
        ...

    @app.get("/api/v1/demos/{demo_id}/export")
    def get_export(demo_id: str):
        ...
```

For 400-after-save on missing Gladia key: write the file, `create_demo(status="saved")`, then check the key.

Use `HTTPException(422, detail={"error": "...", "stage": "gladia"})` so the client can read `stage`.

- [ ] **Step 4: Run demo + trainer API tests**

Run: `pytest tests/test_demo_api.py tests/test_api.py -v`

Expected: PASS. VSR dummy must not have been called.

- [ ] **Step 5: Commit**

```bash
git add app/api.py app/export.py app/store.py tests/test_demo_api.py
git commit -m "feat(demo): add /api/v1/demos create, edit, and export routes"
```

---

### Task 5: Demo page UI

**Files:**
- Create: `static/demo.html`
- Create: `static/demo.js`
- Modify: `static/index.html` (nav)
- Modify: `static/review.html` (nav)
- Modify: `static/style.css`
- Modify: `README.md` (Demo Studio section)

**Interfaces:**
- Consumes: `GET /api/v1/config`, `POST /api/v1/keys`, `POST /api/v1/demos`, `PATCH /api/v1/demos/{id}/utterances`, `POST /api/v1/demos/{id}/export`
- Produces: working `/demo` page

Nav on all three pages:

```html
<nav><a href="/index.html">Practice</a><a href="/demo">Demo</a><a href="/review.html">Review</a></nav>
```

CSS additions (do not change the global `video { transform: scaleX(-1) }` used by Practice):

```css
#screenPreview { transform: none; background: #050505; min-height: 240px; }
#camPreview { width: 160px; transform: scaleX(-1); }
.demo-table { width: 100%; border-collapse: collapse; font-size: 14px; }
.demo-table th, .demo-table td { border-bottom: 1px solid rgba(255,255,255,0.1); padding: 8px; vertical-align: top; }
.demo-table textarea { min-height: 56px; resize: vertical; }
.demo-editor { grid-column: 1 / -1; }
```

`demo.html` structure:
- topbar title `Chaplin · Demo Studio`
- `main`: left card `#screenPreview` video + Start screen / Record / Stop / `#status`
- right card: `#camPreview` video muted, source/target selects (copy options from `index.html`), `#keyRows`
- `#editor` card (class `demo-editor`, hidden until draft exists): table `#utterances`, button `#exportBtn` "Generate voice & export", `#exportPlayer` video, `#downloadLink`

`demo.js` behavior:
1. `loadConfig()` + `renderKeys()` copied from `app.js` (duplicate; do not import-bundle).
2. **Start screen**:
   - `displayStream = await getDisplayMedia({ video: true, audio: false })`
   - `micStream = await getUserMedia({ audio: true })`
   - Mix: `AudioContext` → `createMediaStreamSource(micStream)` → `createMediaStreamDestination()`; recorded stream = display video tracks + dest stream audio track
   - `screenPreview.srcObject = displayStream` (not mirrored)
   - Camera: `getUserMedia({ video: true })` into `camPreview`, catch and ignore denial
   - Enable Record
3. **Record / Stop**: `MediaRecorder` on the mixed stream, mime from `chooseMimeType()` (same list as `app.js`). On stop, `FormData` with `file`, `source_lang`, `target_lang`; POST `/api/v1/demos`. Status text: `Saving…` then wait for JSON. On 400 mentioning a key, focus key rows. On success, `renderDraft(body)`.
4. `renderDraft`: fill table rows `time | original | <textarea data-id>`. Show editor. Status `Ready to edit`.
5. textarea `blur` → if dirty, PATCH `{ utterances: [{ id, english_text }] }` and replace draft.
6. **Generate voice & export**: PATCH any dirty textareas first; POST `/api/v1/demos/{id}/export`; blob → object URL on `#exportPlayer`; `#downloadLink` `download="chaplin-demo-{id}.mp4"`. Status: `Generating voice…` / `Done`.

Minimum 1s of recording before upload (if blob tiny, set status and return).

- [ ] **Step 1: Add the CSS and nav links**

- [ ] **Step 2: Write `static/demo.html` and `static/demo.js` as specified**

- [ ] **Step 3: Add a README section after the Language Trainer “Use” list**

```markdown
## Demo Studio

Record a product-demo screen take, edit the English lines, and export an MP4
with an ElevenLabs voice track and burned-in captions.

1. Open http://127.0.0.1:8765/demo and click **Start screen**.
2. Grant screen + microphone (camera is optional preview only).
3. **Record**, narrate in Chinese or English, **Stop**.
4. Edit the English column. **Generate voice & export** downloads
   `chaplin-demo-<id>.mp4` (screen + English voice + captions).
```

- [ ] **Step 4: Add a tiny static-file test in `tests/test_demo_api.py`**

```python
def test_demo_page_served(tmp_path):
    client, _ = build_demo_client(tmp_path)
    resp = client.get("/demo")
    assert resp.status_code == 200
    assert b"Demo Studio" in resp.content
```

- [ ] **Step 5: Run the full suite**

Run: `pytest -v`

Expected: all tests PASS, including existing trainer tests.

- [ ] **Step 6: Commit**

```bash
git add static/demo.html static/demo.js static/index.html static/review.html static/style.css static/README.md README.md tests/test_demo_api.py
git commit -m "feat(demo): add Demo Studio screen-record and export UI"
```

---

## Self-review (plan vs spec)

| Spec section | Task |
|---|---|
| Screen + mic record, camera preview only | Task 5 |
| Gladia pre-recorded + translation + timestamps | Task 2 |
| Trainer polish per English line, non-fatal | Task 4 |
| Edit english_text only (PATCH) | Task 1 + 4 + 5 |
| Per-utterance TTS, align, atempo ≤ 1.15, no overlap | Task 3 |
| ASS captions on Gladia start/end, burned in | Task 3 + 4 |
| Save screen before APIs | Task 4 |
| Keys reused, 400 on missing | Task 4 + 5 |
| No VSR / no live TTS / no Dubbing | Task 4 (vsr dummy asserts) |
| `/demo` before StaticFiles | Task 4 |
| Existing trainer unchanged | Tasks 1–5 leave those routes alone |

No placeholders remain. Types are consistent: `RawUtterance` (pre-id) → store assigns `id`/`idx` → `DemoUtterance`. `export_demo(...)` is the route-facing orchestrator; `plan_alignment` / `write_ass` / `mux_demo` stay pure.
