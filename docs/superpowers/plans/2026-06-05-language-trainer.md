# Chaplin Language Trainer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn Chaplin into a daily spoken-language trainer: one recording → original line + refined/translated line + lip-read line, with TTS playback, vocabulary memory, clipboard copy, and fine-tune-pair capture, behind an API-first backend reusable by a future iOS client.

**Architecture:** FastAPI backend exposing a versioned JSON API. One `POST /api/v1/sessions` multipart upload runs the pipeline: extract audio → Gladia STT (ground truth) in parallel with VSR lip-reading (prediction) → Trainer LLM (translate if non-English, optimize if English) → persist to SQLite + keep recording files. A thin vanilla HTML/JS frontend (practice + review views) is one client; a future SwiftUI iOS app is another.

**Tech Stack:** Python 3.11, FastAPI, Uvicorn, Pydantic v2, pydantic-settings, httpx, stdlib sqlite3, ffmpeg (CLI), OpenAI SDK (trainer), Gladia API (STT), ElevenLabs API (TTS), existing `InferencePipeline` (VSR).

**Spec:** `docs/superpowers/specs/2026-06-05-language-trainer-design.md`

---

## File Structure

```
chaplin/
  app/
    __init__.py        # empty package marker
    schemas.py         # Pydantic request/response models
    settings.py        # pydantic-settings config (get_settings)
    keys.py            # KeyStore: env -> ~/.chaplin/keys.json resolution + masking
    store.py           # Store: SQLite sessions + vocab CRUD, recording paths
    audio.py           # ffmpeg: has_audio, extract_audio, probe_duration
    transcribe.py      # Gladia async client -> Transcript
    trainer.py         # async refine() -> LanguageTrainerOutput
    tts.py             # ElevenLabs async synthesize() -> mp3 bytes
    vsr.py             # VSR wrapper around InferencePipeline (load once)
    api.py             # FastAPI app factory (create_app) + routes + dependency wiring
    main.py            # production wiring: builds real Store/KeyStore/VSR -> app
  static/
    index.html         # practice view
    review.html        # review view (vocab + history tabs)
    app.js             # shared API client + practice logic
    review.js          # review view logic
    style.css          # shared styles
  tests/
    __init__.py
    conftest.py        # shared fixtures (tmp store, fake keys)
    test_schemas.py
    test_keys.py
    test_store.py
    test_audio.py
    test_transcribe.py
    test_trainer.py
    test_tts.py
    test_api.py
  data/                # gitignored: chaplin.db + recordings/<id>/
  web_chaplin.py       # replaced: thin launcher for uvicorn app.api:app
  requirements.txt     # + fastapi, uvicorn[standard], httpx, pydantic-settings, pytest, pytest-asyncio
```

Untouched: `chaplin.py` (desktop), `pipelines/`, `configs/`, `espnet/`, `hydra_configs/`, `main.py`.

---

## Task 0: Scaffolding, dependencies, gitignore

**Files:**
- Create: `app/__init__.py`, `tests/__init__.py`
- Modify: `requirements.txt`, `.gitignore`

- [ ] **Step 1: Add dependencies to `requirements.txt`**

Append these lines to `requirements.txt`:

```
fastapi
uvicorn[standard]
httpx
pydantic-settings
pytest
pytest-asyncio
```

- [ ] **Step 2: Add data dir to `.gitignore`**

Append to `.gitignore`:

```
data/
.chaplin/
```

- [ ] **Step 3: Create empty package markers**

Create `app/__init__.py`:

```python
```

Create `tests/__init__.py`:

```python
```

- [ ] **Step 4: Install dependencies**

Run: `pip install fastapi "uvicorn[standard]" httpx pydantic-settings pytest pytest-asyncio`
Expected: installs succeed.

- [ ] **Step 5: Configure pytest-asyncio**

Create `pytest.ini`:

```ini
[pytest]
asyncio_mode = auto
testpaths = tests
```

- [ ] **Step 6: Commit**

```bash
git add requirements.txt .gitignore app/__init__.py tests/__init__.py pytest.ini
git commit -m "chore: scaffold app package and deps for language trainer"
```

---

## Task 1: Schemas (`app/schemas.py`)

**Files:**
- Create: `app/schemas.py`
- Test: `tests/test_schemas.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_schemas.py`:

```python
from app.schemas import (
    VocabHighlight,
    LanguageTrainerOutput,
    SessionResult,
    VocabItem,
    ConfigResponse,
    KeysRequest,
)


def test_language_trainer_output_roundtrip():
    out = LanguageTrainerOutput(
        source_language="zh",
        original_text="你好世界",
        refined_text="Hello, world.",
        highlights=[VocabHighlight(original_phrase="世界", refined_phrase="world", reason="noun")],
        explanation="Direct greeting.",
    )
    data = out.model_dump()
    assert data["source_language"] == "zh"
    assert data["highlights"][0]["refined_phrase"] == "world"


def test_session_result_allows_null_confidence():
    res = SessionResult(
        id="abc",
        created_at="2026-06-05T00:00:00",
        source_lang="en",
        target_lang="en",
        original_text="i want go store",
        refined_text="I want to go to the store.",
        vsr_raw_text="I WANT GO STORE",
        input_source="lip",
        highlights=[],
        confidence=None,
    )
    assert res.confidence is None
    assert res.input_source == "lip"


def test_keys_request_all_optional():
    req = KeysRequest()
    assert req.gladia is None and req.elevenlabs is None and req.openai is None


def test_vocab_item_fields():
    item = VocabItem(
        id=1, session_id="s1", original_phrase="big", refined_phrase="substantial",
        reason="more precise", source_lang="en", starred=False, created_at="2026-06-05T00:00:00",
    )
    assert item.starred is False


def test_config_response():
    cfg = ConfigResponse(keys_set={"gladia": True, "elevenlabs": False, "openai": True},
                         default_lang="en", voices=["Rachel"])
    assert cfg.keys_set["gladia"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_schemas.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.schemas'`

- [ ] **Step 3: Write minimal implementation**

Create `app/schemas.py`:

```python
from pydantic import BaseModel, ConfigDict


class VocabHighlight(BaseModel):
    model_config = ConfigDict(extra="forbid")
    original_phrase: str
    refined_phrase: str
    reason: str


class LanguageTrainerOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_language: str
    original_text: str
    refined_text: str
    highlights: list[VocabHighlight]
    explanation: str


class SessionResult(BaseModel):
    id: str
    created_at: str
    source_lang: str
    target_lang: str
    original_text: str
    refined_text: str
    vsr_raw_text: str
    input_source: str  # "audio" | "lip"
    highlights: list[VocabHighlight]
    confidence: float | None = None


class VocabItem(BaseModel):
    id: int
    session_id: str
    original_phrase: str
    refined_phrase: str
    reason: str
    source_lang: str
    starred: bool
    created_at: str


class ConfigResponse(BaseModel):
    keys_set: dict[str, bool]
    default_lang: str
    voices: list[str]


class KeysRequest(BaseModel):
    gladia: str | None = None
    elevenlabs: str | None = None
    openai: str | None = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_schemas.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add app/schemas.py tests/test_schemas.py
git commit -m "feat: add pydantic schemas for language trainer"
```

---

## Task 2: Settings (`app/settings.py`)

**Files:**
- Create: `app/settings.py`
- Test: `tests/test_settings.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_settings.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_settings.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.settings'`

- [ ] **Step 3: Write minimal implementation**

Create `app/settings.py`:

```python
import os
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CHAPLIN_", extra="ignore")

    data_dir: str = os.path.join(os.getcwd(), "data")
    default_target_lang: str = "en"

    # LLM (trainer)
    llm_provider: str = "openai"  # "openai" | "ollama"
    openai_model: str = "gpt-5.4-mini"
    ollama_model: str = "qwen3:4b"

    # Gladia
    gladia_base_url: str = "https://api.gladia.io"

    # ElevenLabs
    elevenlabs_voice_id: str = "21m00Tcm4TlvDq8ikWAM"  # "Rachel" default
    elevenlabs_model_id: str = "eleven_multilingual_v2"

    # VSR
    vsr_config: str = "./configs/LRS3_V_WER19.1.ini"

    # server
    host: str = "127.0.0.1"
    port: int = 8765

    @property
    def db_path(self) -> str:
        return os.path.join(self.data_dir, "chaplin.db")

    @property
    def recordings_dir(self) -> str:
        return os.path.join(self.data_dir, "recordings")


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_settings.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add app/settings.py tests/test_settings.py
git commit -m "feat: add pydantic-settings config"
```

---

## Task 3: Key store (`app/keys.py`)

**Files:**
- Create: `app/keys.py`
- Test: `tests/test_keys.py`

Resolution order per key: environment variable → JSON file. Env var names:
`GLADIA_API_KEY`, `ELEVENLABS_API_KEY`, `OPENAI_API_KEY`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_keys.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_keys.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.keys'`

- [ ] **Step 3: Write minimal implementation**

Create `app/keys.py`:

```python
import json
import os

KEY_NAMES = ("gladia", "elevenlabs", "openai")
ENV_NAMES = {
    "gladia": "GLADIA_API_KEY",
    "elevenlabs": "ELEVENLABS_API_KEY",
    "openai": "OPENAI_API_KEY",
}


class KeyStore:
    def __init__(self, path: str):
        self.path = path

    def _load_file(self) -> dict:
        if not os.path.isfile(self.path):
            return {}
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (json.JSONDecodeError, OSError):
            return {}

    def get(self, name: str) -> str | None:
        env_val = os.getenv(ENV_NAMES.get(name, ""))
        if env_val:
            return env_val
        return self._load_file().get(name) or None

    def set(self, values: dict) -> None:
        data = self._load_file()
        for name in KEY_NAMES:
            v = values.get(name)
            if v:
                data[name] = v
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)

    def keys_set(self) -> dict:
        return {name: self.get(name) is not None for name in KEY_NAMES}

    def masked(self, name: str) -> str | None:
        val = self.get(name)
        if not val:
            return None
        tail = val[-4:]
        return f"{name[:3]}_••••{tail}"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_keys.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add app/keys.py tests/test_keys.py
git commit -m "feat: add key store with env-over-file resolution and masking"
```

---

## Task 4: Store (`app/store.py`)

**Files:**
- Create: `app/store.py`
- Test: `tests/test_store.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_store.py`:

```python
from app.store import Store
from app.schemas import VocabHighlight


def make_store(tmp_path):
    s = Store(str(tmp_path / "chaplin.db"))
    s.init_db()
    return s


def test_save_and_get_session(tmp_path):
    s = make_store(tmp_path)
    sid = s.save_session(
        source_lang="en", target_lang="en",
        original_text="i go store", refined_text="I went to the store.",
        vsr_raw_text="I GO STORE", video_path="/v.webm", audio_path="/a.wav",
        duration_s=3.2, confidence=0.91, input_source="audio",
    )
    assert isinstance(sid, str) and sid
    rows = s.list_sessions(limit=10)
    assert len(rows) == 1
    assert rows[0]["original_text"] == "i go store"
    assert rows[0]["id"] == sid


def test_sessions_ordered_newest_first(tmp_path):
    s = make_store(tmp_path)
    first = s.save_session(source_lang="en", target_lang="en", original_text="one",
                           refined_text="One.", vsr_raw_text="ONE", video_path="", audio_path=None,
                           duration_s=2.0, confidence=None, input_source="lip")
    second = s.save_session(source_lang="en", target_lang="en", original_text="two",
                            refined_text="Two.", vsr_raw_text="TWO", video_path="", audio_path=None,
                            duration_s=2.0, confidence=None, input_source="lip")
    rows = s.list_sessions(limit=10)
    assert rows[0]["id"] == second
    assert rows[1]["id"] == first


def test_save_and_list_vocab(tmp_path):
    s = make_store(tmp_path)
    sid = s.save_session(source_lang="en", target_lang="en", original_text="big house",
                         refined_text="substantial home", vsr_raw_text="", video_path="",
                         audio_path=None, duration_s=2.0, confidence=None, input_source="lip")
    s.save_vocab(sid, [
        VocabHighlight(original_phrase="big", refined_phrase="substantial", reason="precise"),
        VocabHighlight(original_phrase="house", refined_phrase="home", reason="warmer"),
    ], source_lang="en")
    vocab = s.list_vocab()
    assert len(vocab) == 2
    assert {v["refined_phrase"] for v in vocab} == {"substantial", "home"}


def test_star_vocab(tmp_path):
    s = make_store(tmp_path)
    sid = s.save_session(source_lang="en", target_lang="en", original_text="x", refined_text="X.",
                         vsr_raw_text="", video_path="", audio_path=None, duration_s=2.0,
                         confidence=None, input_source="lip")
    s.save_vocab(sid, [VocabHighlight(original_phrase="x", refined_phrase="ex", reason="r")],
                 source_lang="en")
    vid = s.list_vocab()[0]["id"]
    updated = s.set_vocab_starred(vid, True)
    assert updated["starred"] is True
    starred_only = s.list_vocab(starred=True)
    assert len(starred_only) == 1


def test_vocab_search(tmp_path):
    s = make_store(tmp_path)
    sid = s.save_session(source_lang="en", target_lang="en", original_text="x", refined_text="X.",
                         vsr_raw_text="", video_path="", audio_path=None, duration_s=2.0,
                         confidence=None, input_source="lip")
    s.save_vocab(sid, [
        VocabHighlight(original_phrase="happy", refined_phrase="elated", reason="r"),
        VocabHighlight(original_phrase="sad", refined_phrase="forlorn", reason="r"),
    ], source_lang="en")
    found = s.list_vocab(q="elat")
    assert len(found) == 1 and found[0]["refined_phrase"] == "elated"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_store.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.store'`

- [ ] **Step 3: Write minimal implementation**

Create `app/store.py`:

```python
import os
import sqlite3
import uuid
from datetime import datetime, timezone

from app.schemas import VocabHighlight


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, db_path: str):
        self.db_path = db_path

    def _conn(self) -> sqlite3.Connection:
        os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def init_db(self) -> None:
        with self._conn() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    source_lang TEXT NOT NULL,
                    target_lang TEXT NOT NULL,
                    original_text TEXT NOT NULL,
                    refined_text TEXT NOT NULL,
                    vsr_raw_text TEXT NOT NULL,
                    video_path TEXT NOT NULL,
                    audio_path TEXT,
                    duration_s REAL,
                    confidence REAL,
                    input_source TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS vocab (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    original_phrase TEXT NOT NULL,
                    refined_phrase TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    source_lang TEXT NOT NULL,
                    starred INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (session_id) REFERENCES sessions(id)
                );
                """
            )

    def save_session(self, *, source_lang, target_lang, original_text, refined_text,
                     vsr_raw_text, video_path, audio_path, duration_s, confidence,
                     input_source) -> str:
        sid = uuid.uuid4().hex
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO sessions (id, created_at, source_lang, target_lang,
                   original_text, refined_text, vsr_raw_text, video_path, audio_path,
                   duration_s, confidence, input_source)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (sid, _now(), source_lang, target_lang, original_text, refined_text,
                 vsr_raw_text, video_path, audio_path, duration_s, confidence, input_source),
            )
        return sid

    def list_sessions(self, limit: int = 50) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM sessions ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def save_vocab(self, session_id: str, highlights: list[VocabHighlight],
                   source_lang: str) -> None:
        with self._conn() as conn:
            conn.executemany(
                """INSERT INTO vocab (session_id, original_phrase, refined_phrase,
                   reason, source_lang, starred, created_at)
                   VALUES (?,?,?,?,?,0,?)""",
                [(session_id, h.original_phrase, h.refined_phrase, h.reason, source_lang, _now())
                 for h in highlights],
            )

    def list_vocab(self, starred: bool | None = None, q: str | None = None) -> list[dict]:
        sql = "SELECT * FROM vocab"
        clauses, params = [], []
        if starred:
            clauses.append("starred = 1")
        if q:
            clauses.append("(original_phrase LIKE ? OR refined_phrase LIKE ?)")
            params += [f"%{q}%", f"%{q}%"]
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at DESC, id DESC"
        with self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._vocab_row(r) for r in rows]

    def set_vocab_starred(self, vocab_id: int, starred: bool) -> dict:
        with self._conn() as conn:
            conn.execute("UPDATE vocab SET starred = ? WHERE id = ?",
                         (1 if starred else 0, vocab_id))
            row = conn.execute("SELECT * FROM vocab WHERE id = ?", (vocab_id,)).fetchone()
        return self._vocab_row(row)

    @staticmethod
    def _vocab_row(row: sqlite3.Row) -> dict:
        d = dict(row)
        d["starred"] = bool(d["starred"])
        return d
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_store.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add app/store.py tests/test_store.py
git commit -m "feat: add sqlite store for sessions and vocab"
```

---

## Task 5: Audio extraction (`app/audio.py`)

**Files:**
- Create: `app/audio.py`
- Test: `tests/test_audio.py`

Uses `ffmpeg`/`ffprobe` via subprocess. Tests mock subprocess so they run without ffmpeg.

- [ ] **Step 1: Write the failing test**

Create `tests/test_audio.py`:

```python
from unittest.mock import patch

from app import audio


def test_has_audio_true():
    with patch("app.audio.subprocess.run") as run:
        run.return_value.stdout = "audio\n"
        run.return_value.returncode = 0
        assert audio.has_audio("/clip.webm") is True


def test_has_audio_false_when_no_stream():
    with patch("app.audio.subprocess.run") as run:
        run.return_value.stdout = "\n"
        run.return_value.returncode = 0
        assert audio.has_audio("/clip.webm") is False


def test_probe_duration_parses_seconds():
    with patch("app.audio.subprocess.run") as run:
        run.return_value.stdout = "4.521000\n"
        run.return_value.returncode = 0
        assert abs(audio.probe_duration("/clip.webm") - 4.521) < 1e-3


def test_probe_duration_returns_zero_on_garbage():
    with patch("app.audio.subprocess.run") as run:
        run.return_value.stdout = "N/A\n"
        run.return_value.returncode = 0
        assert audio.probe_duration("/clip.webm") == 0.0


def test_extract_audio_invokes_ffmpeg(tmp_path):
    out = tmp_path / "audio.wav"
    with patch("app.audio.subprocess.run") as run:
        run.return_value.returncode = 0
        out.write_bytes(b"RIFF")  # simulate ffmpeg producing a file
        ok = audio.extract_audio("/clip.webm", str(out))
    assert ok is True
    args = run.call_args[0][0]
    assert args[0] == "ffmpeg" and str(out) in args
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_audio.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.audio'`

- [ ] **Step 3: Write minimal implementation**

Create `app/audio.py`:

```python
import os
import subprocess


def has_audio(video_path: str) -> bool:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a",
         "-show_entries", "stream=codec_type", "-of", "csv=p=0", video_path],
        capture_output=True, text=True,
    )
    return "audio" in (result.stdout or "")


def probe_duration(path: str) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", path],
        capture_output=True, text=True,
    )
    try:
        return float((result.stdout or "").strip())
    except ValueError:
        return 0.0


def extract_audio(video_path: str, out_path: str) -> bool:
    """Extract mono 16k wav. Returns True if the output file exists and is non-empty."""
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-i", video_path, "-vn", "-ac", "1", "-ar", "16000",
         out_path],
        capture_output=True, text=True,
    )
    return os.path.isfile(out_path) and os.path.getsize(out_path) > 0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_audio.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add app/audio.py tests/test_audio.py
git commit -m "feat: add ffmpeg audio probe and extraction helpers"
```

---

## Task 6: Gladia transcription (`app/transcribe.py`)

**Files:**
- Create: `app/transcribe.py`
- Test: `tests/test_transcribe.py`

Gladia v2 flow: `POST /v2/upload` (multipart) → `audio_url`; `POST /v2/pre-recorded`
(`{audio_url, detect_language: true}`) → `result_url`; poll `GET result_url` until
`status == "done"`. Tests mock httpx so no real network/key is needed.

- [ ] **Step 1: Write the failing test**

Create `tests/test_transcribe.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_transcribe.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.transcribe'`

- [ ] **Step 3: Write minimal implementation**

Create `app/transcribe.py`:

```python
import asyncio

import httpx
from pydantic import BaseModel


class Transcript(BaseModel):
    text: str
    language: str
    confidence: float | None = None


async def transcribe(audio_path: str, key: str, base_url: str,
                     poll_interval: float = 1.0, max_polls: int = 60) -> Transcript:
    headers = {"x-gladia-key": key}
    async with httpx.AsyncClient(timeout=120.0) as client:
        # 1. upload
        with open(audio_path, "rb") as fh:
            up = await client.post(
                f"{base_url}/v2/upload",
                headers=headers,
                files={"audio": ("audio.wav", fh, "audio/wav")},
            )
        up.raise_for_status()
        audio_url = up.json()["audio_url"]

        # 2. request transcription
        req = await client.post(
            f"{base_url}/v2/pre-recorded",
            headers=headers,
            json={"audio_url": audio_url, "detect_language": True, "diarization": False},
        )
        req.raise_for_status()
        result_url = req.json()["result_url"]

        # 3. poll
        for _ in range(max_polls):
            poll = await client.get(result_url, headers=headers)
            poll.raise_for_status()
            body = poll.json()
            status = body.get("status")
            if status == "done":
                tr = body["result"]["transcription"]
                langs = tr.get("languages") or ["en"]
                utterances = tr.get("utterances") or []
                conf = utterances[0].get("confidence") if utterances else None
                return Transcript(
                    text=tr.get("full_transcript", "").strip(),
                    language=langs[0],
                    confidence=conf,
                )
            if status == "error":
                raise RuntimeError(f"Gladia transcription failed: {body}")
            await asyncio.sleep(poll_interval)
        raise RuntimeError("Gladia transcription timed out")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_transcribe.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add app/transcribe.py tests/test_transcribe.py
git commit -m "feat: add gladia async transcription client"
```

---

## Task 7: Trainer LLM (`app/trainer.py`)

**Files:**
- Create: `app/trainer.py`
- Test: `tests/test_trainer.py`

Picks prompt by comparing normalized source vs target language. Uses OpenAI structured
output. Tests inject a fake client so no key/network is needed.

- [ ] **Step 1: Write the failing test**

Create `tests/test_trainer.py`:

```python
import json
import pytest

from app.trainer import refine, build_messages, is_translation
from app.schemas import LanguageTrainerOutput


def test_is_translation_true_for_chinese_to_english():
    assert is_translation("zh", "en") is True


def test_is_translation_false_for_english_to_english():
    assert is_translation("en", "en") is False
    assert is_translation("en-US", "en") is False


def test_build_messages_translation_mentions_translate():
    msgs = build_messages("你好", "zh", "en")
    assert any("translat" in m["content"].lower() for m in msgs)


def test_build_messages_optimize_mentions_native():
    msgs = build_messages("i want go store", "en", "en")
    assert any("native" in m["content"].lower() for m in msgs)


class FakeMessage:
    def __init__(self, content):
        self.content = content


class FakeChoice:
    def __init__(self, content):
        self.message = FakeMessage(content)


class FakeCompletion:
    def __init__(self, content):
        self.choices = [FakeChoice(content)]


class FakeOpenAI:
    def __init__(self, content):
        self._content = content
        self.chat = self
        self.completions = self

    async def create(self, **kwargs):
        return FakeCompletion(self._content)


@pytest.mark.asyncio
async def test_refine_returns_trainer_output():
    payload = json.dumps({
        "source_language": "en",
        "original_text": "i want go store",
        "refined_text": "I want to go to the store.",
        "highlights": [{"original_phrase": "go store",
                        "refined_phrase": "go to the store",
                        "reason": "needs preposition and article"}],
        "explanation": "Added grammar.",
    })
    client = FakeOpenAI(payload)
    out = await refine("i want go store", "en", "en", client=client, model="gpt-x")
    assert isinstance(out, LanguageTrainerOutput)
    assert out.refined_text == "I want to go to the store."
    assert out.highlights[0].refined_phrase == "go to the store"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_trainer.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.trainer'`

- [ ] **Step 3: Write minimal implementation**

Create `app/trainer.py`:

```python
import json

from app.schemas import LanguageTrainerOutput

try:
    from openai import AsyncOpenAI
except ImportError:  # pragma: no cover
    AsyncOpenAI = None


def is_translation(source_lang: str, target_lang: str) -> bool:
    return source_lang.split("-")[0].lower() != target_lang.split("-")[0].lower()


def build_messages(text: str, source_lang: str, target_lang: str) -> list[dict]:
    if is_translation(source_lang, target_lang):
        system = (
            "You are a language tutor. The user spoke in their native language. "
            f"Translate their input into natural, authentic, native-sounding {target_lang}. "
            "Do not translate word-for-word; produce what a fluent native speaker would "
            "actually say. In 'highlights', list 2-5 key vocabulary words or expressions "
            "from your translation that the learner should study, each with the original "
            "phrase (in the source language) it maps to and a short reason. "
            "Set 'source_language' to the detected language, 'original_text' to the user's "
            "input verbatim, 'refined_text' to your translation, and 'explanation' to one "
            "short sentence. Return JSON matching the schema."
        )
    else:
        system = (
            "You are a language tutor for an English learner. Rewrite the user's input as "
            "a more native, concise, and sophisticated version while keeping their meaning. "
            "In 'highlights', list the specific words or phrases you upgraded: 'original_phrase' "
            "is what the user said, 'refined_phrase' is your improved wording, 'reason' explains "
            "why it sounds more native. Set 'source_language' to the detected language, "
            "'original_text' to the user's input verbatim, 'refined_text' to your rewrite, and "
            "'explanation' to one short sentence. Return JSON matching the schema."
        )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": f"Input:\n\n{text}"},
    ]


async def refine(text: str, source_lang: str, target_lang: str, *, client, model: str
                 ) -> LanguageTrainerOutput:
    response = await client.chat.completions.create(
        model=model,
        messages=build_messages(text, source_lang, target_lang),
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "language_trainer_output",
                "schema": LanguageTrainerOutput.model_json_schema(),
                "strict": True,
            },
        },
    )
    content = response.choices[0].message.content
    if isinstance(content, list):
        content = "".join(
            item.get("text", "") if isinstance(item, dict) else str(item)
            for item in content
        )
    return LanguageTrainerOutput.model_validate_json(json.dumps(json.loads(content)))


def make_openai_client(api_key: str):
    if AsyncOpenAI is None:
        raise RuntimeError("openai package not installed")
    return AsyncOpenAI(api_key=api_key)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_trainer.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add app/trainer.py tests/test_trainer.py
git commit -m "feat: add trainer LLM with translate/optimize prompts"
```

---

## Task 8: ElevenLabs TTS (`app/tts.py`)

**Files:**
- Create: `app/tts.py`
- Test: `tests/test_tts.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_tts.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_tts.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.tts'`

- [ ] **Step 3: Write minimal implementation**

Create `app/tts.py`:

```python
import httpx

ELEVENLABS_BASE = "https://api.elevenlabs.io/v1/text-to-speech"


async def synthesize(text: str, voice_id: str, key: str,
                     model_id: str = "eleven_multilingual_v2") -> bytes:
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            f"{ELEVENLABS_BASE}/{voice_id}",
            headers={"xi-api-key": key, "accept": "audio/mpeg"},
            json={"text": text, "model_id": model_id},
        )
        resp.raise_for_status()
        return resp.content
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_tts.py -v`
Expected: PASS (1 passed)

- [ ] **Step 5: Commit**

```bash
git add app/tts.py tests/test_tts.py
git commit -m "feat: add elevenlabs tts client"
```

---

## Task 9: VSR wrapper (`app/vsr.py`)

**Files:**
- Create: `app/vsr.py`

No unit test (loads a heavy model + needs downloaded weights). Verified via the API smoke
test and manual run. Keep the wrapper trivial so there is nothing to unit test.

- [ ] **Step 1: Write the implementation**

Create `app/vsr.py`:

```python
import torch

from pipelines.pipeline import InferencePipeline


class VSR:
    """Loads the lip-reading model once and reads video files to text."""

    def __init__(self, config_filename: str, device: str = "cpu"):
        self.model = InferencePipeline(
            config_filename,
            device=torch.device(device),
            detector="mediapipe",
            face_track=True,
        )

    def read_lips(self, video_path: str) -> str:
        try:
            return (self.model(video_path) or "").strip()
        except Exception:
            return ""
```

- [ ] **Step 2: Verify it imports**

Run: `python -c "from app.vsr import VSR; print('ok')"`
Expected: prints `ok` (model not instantiated, just imported).

- [ ] **Step 3: Commit**

```bash
git add app/vsr.py
git commit -m "feat: add vsr lip-reading wrapper"
```

---

## Task 10: API (`app/api.py`)

**Files:**
- Create: `app/api.py`
- Test: `tests/test_api.py`, `tests/conftest.py`

The app uses a small dependency container so tests can swap VSR/Gladia/trainer/TTS with
fakes. The container is built in `create_app(...)` and stored on `app.state`.

- [ ] **Step 1: Write the conftest fixtures**

Create `tests/conftest.py`:

```python
import pytest


@pytest.fixture
def fake_services():
    """Returns a dict of fakes for injection into create_app."""

    async def fake_transcribe(audio_path, key, base_url, **kw):
        from app.transcribe import Transcript
        return Transcript(text="你好世界", language="zh", confidence=0.9)

    async def fake_refine(text, source_lang, target_lang, *, client, model):
        from app.schemas import LanguageTrainerOutput, VocabHighlight
        return LanguageTrainerOutput(
            source_language=source_lang,
            original_text=text,
            refined_text="Hello, world.",
            highlights=[VocabHighlight(original_phrase="世界", refined_phrase="world", reason="noun")],
            explanation="Greeting.",
        )

    async def fake_synthesize(text, voice_id, key, model_id="m"):
        return b"ID3fake-audio"

    class FakeVSR:
        def read_lips(self, video_path):
            return "HELLO WORLD"

    return {
        "transcribe": fake_transcribe,
        "refine": fake_refine,
        "synthesize": fake_synthesize,
        "vsr": FakeVSR(),
    }
```

- [ ] **Step 2: Write the failing API test**

Create `tests/test_api.py`:

```python
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
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_api.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.api'`

- [ ] **Step 4: Write minimal implementation**

Create `app/api.py`:

```python
import os

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles

from app import audio
from app.keys import KeyStore
from app.schemas import (ConfigResponse, KeysRequest, SessionResult, VocabItem)
from app.settings import Settings
from app.store import Store
from app.transcribe import transcribe as default_transcribe
from app.trainer import refine as default_refine, make_openai_client
from app.tts import synthesize as default_synthesize

MIN_DURATION_S = 1.0
STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")


def create_app(*, settings: Settings, store: Store, keys: KeyStore, vsr,
               transcribe_fn=default_transcribe, refine_fn=default_refine,
               synthesize_fn=default_synthesize, make_llm_client=make_openai_client) -> FastAPI:
    app = FastAPI(title="Chaplin Language Trainer", version="1.0.0")
    app.state.settings = settings
    app.state.store = store
    app.state.keys = keys
    app.state.vsr = vsr

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    @app.get("/api/v1/config", response_model=ConfigResponse)
    def get_config():
        return ConfigResponse(
            keys_set=keys.keys_set(),
            default_lang=settings.default_target_lang,
            voices=[settings.elevenlabs_voice_id],
        )

    @app.post("/api/v1/keys", response_model=ConfigResponse)
    def set_keys(req: KeysRequest):
        keys.set(req.model_dump())
        return ConfigResponse(
            keys_set=keys.keys_set(),
            default_lang=settings.default_target_lang,
            voices=[settings.elevenlabs_voice_id],
        )

    @app.post("/api/v1/sessions", response_model=SessionResult)
    async def create_session(file: UploadFile = File(...),
                             target_lang: str = Form(None)):
        target_lang = target_lang or settings.default_target_lang
        os.makedirs(settings.recordings_dir, exist_ok=True)
        sid_dir = os.path.join(settings.recordings_dir, _new_id())
        os.makedirs(sid_dir, exist_ok=True)
        video_path = os.path.join(sid_dir, "clip.webm")
        with open(video_path, "wb") as fh:
            fh.write(await file.read())

        duration = audio.probe_duration(video_path)
        has_voice = audio.has_audio(video_path)

        # VSR (lip-reading) always runs — prediction + fallback input
        vsr_text = vsr.read_lips(video_path)

        original_text = ""
        source_lang = target_lang
        confidence = None
        audio_path = None
        input_source = "lip"

        if has_voice:
            if not keys.get("gladia"):
                raise HTTPException(400, "Missing Gladia API key.")
            audio_path = os.path.join(sid_dir, "audio.wav")
            if audio.extract_audio(video_path, audio_path):
                tr = await transcribe_fn(audio_path, key=keys.get("gladia"),
                                         base_url=settings.gladia_base_url)
                original_text = tr.text
                source_lang = tr.language
                confidence = tr.confidence
                input_source = "audio"

        if not original_text:
            original_text = vsr_text
            source_lang = target_lang
            input_source = "lip"

        if not original_text or duration < MIN_DURATION_S:
            raise HTTPException(422, "No usable speech or lip movement detected.")

        if not keys.get("openai"):
            raise HTTPException(400, "Missing OpenAI API key.")
        llm_client = make_llm_client(keys.get("openai"))
        trainer_out = await refine_fn(original_text, source_lang, target_lang,
                                      client=llm_client, model=settings.openai_model)

        sid = store.save_session(
            source_lang=source_lang, target_lang=target_lang,
            original_text=original_text, refined_text=trainer_out.refined_text,
            vsr_raw_text=vsr_text, video_path=video_path, audio_path=audio_path,
            duration_s=duration, confidence=confidence, input_source=input_source,
        )
        store.save_vocab(sid, trainer_out.highlights, source_lang=source_lang)

        rows = store.list_sessions(limit=1)
        created_at = rows[0]["created_at"] if rows else ""
        return SessionResult(
            id=sid, created_at=created_at, source_lang=source_lang, target_lang=target_lang,
            original_text=original_text, refined_text=trainer_out.refined_text,
            vsr_raw_text=vsr_text, input_source=input_source,
            highlights=trainer_out.highlights, confidence=confidence,
        )

    @app.get("/api/v1/sessions")
    def list_sessions(limit: int = 50):
        return store.list_sessions(limit=limit)

    @app.get("/api/v1/vocab", response_model=list[VocabItem])
    def list_vocab(starred: bool | None = None, q: str | None = None):
        return store.list_vocab(starred=starred, q=q)

    @app.patch("/api/v1/vocab/{vocab_id}", response_model=VocabItem)
    def star_vocab(vocab_id: int, body: dict):
        return store.set_vocab_starred(vocab_id, bool(body.get("starred")))

    @app.post("/api/v1/tts")
    async def tts(body: dict):
        text = (body or {}).get("text", "").strip()
        if not text:
            raise HTTPException(422, "No text provided.")
        if not keys.get("elevenlabs"):
            raise HTTPException(400, "Missing ElevenLabs API key.")
        data = await synthesize_fn(text, voice_id=settings.elevenlabs_voice_id,
                                   key=keys.get("elevenlabs"),
                                   model_id=settings.elevenlabs_model_id)
        return Response(content=data, media_type="audio/mpeg")

    if os.path.isdir(STATIC_DIR):
        app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")

    return app


def _new_id() -> str:
    import uuid
    return uuid.uuid4().hex
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_api.py -v`
Expected: PASS (8 passed)

- [ ] **Step 6: Run the full suite**

Run: `pytest -v`
Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add app/api.py tests/test_api.py tests/conftest.py
git commit -m "feat: add fastapi app wiring the trainer pipeline"
```

---

## Task 11: App entrypoint (`app/main.py`) + launcher

**Files:**
- Create: `app/main.py`
- Modify: `web_chaplin.py` (replace contents with a launcher)

- [ ] **Step 1: Write the production app factory**

Create `app/main.py`:

```python
import os

from app.api import create_app
from app.keys import KeyStore
from app.settings import get_settings
from app.store import Store
from app.vsr import VSR


def build() -> "FastAPI":
    settings = get_settings()
    store = Store(settings.db_path)
    store.init_db()
    keyfile = os.path.join(os.path.expanduser("~"), ".chaplin", "keys.json")
    keys = KeyStore(keyfile)
    print("Loading Chaplin VSR model...")
    vsr = VSR(settings.vsr_config, device="cpu")
    print("Chaplin VSR model loaded.")
    return create_app(settings=settings, store=store, keys=keys, vsr=vsr)


app = build()
```

- [ ] **Step 2: Replace `web_chaplin.py` with a launcher**

Replace the entire contents of `web_chaplin.py`:

```python
"""Launcher for the Chaplin Language Trainer web app.

Run directly (python web_chaplin.py) or via uvicorn:
    uvicorn app.main:app --host 127.0.0.1 --port 8765
"""
import uvicorn

from app.settings import get_settings


def main():
    settings = get_settings()
    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=False)


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Verify the app builds without crashing on import (model load may take time)**

Run: `python -c "import app.main; print('built')"`
Expected: prints model-loading lines then `built`. (Requires the VSR weights from `setup.sh`.)
If weights are not present, this step is deferred to manual verification after `./setup.sh`.

- [ ] **Step 4: Commit**

```bash
git add app/main.py web_chaplin.py
git commit -m "feat: add app entrypoint and web launcher"
```

---

## Task 12: Frontend — styles and practice view

**Files:**
- Create: `static/style.css`, `static/index.html`, `static/app.js`

- [ ] **Step 1: Create `static/style.css`**

```css
:root {
  color-scheme: dark;
  font-family: Inter, ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
  background: #121212;
  color: #f4f2ec;
}
* { box-sizing: border-box; }
body { margin: 0; min-height: 100vh; background:
  radial-gradient(circle at 18% 8%, rgba(48,118,115,0.28), transparent 34%),
  linear-gradient(135deg, #151515, #1f211e 52%, #101110); }
.topbar { display: flex; justify-content: space-between; align-items: center;
  padding: 14px 22px; border-bottom: 1px solid rgba(255,255,255,0.1); }
.topbar a { color: #b9f3d0; text-decoration: none; margin-left: 16px; font-weight: 600; }
main { width: min(1120px, calc(100vw - 32px)); margin: 22px auto; display: grid;
  grid-template-columns: minmax(320px, 1fr) 360px; gap: 24px; }
.card { border: 1px solid rgba(255,255,255,0.14); background: rgba(20,21,20,0.78);
  box-shadow: 0 24px 80px rgba(0,0,0,0.34); border-radius: 8px; padding: 18px; }
video { width: 100%; border-radius: 6px; background: #050505; transform: scaleX(-1); }
button { border: 0; border-radius: 6px; padding: 10px 14px; color: #0b0d0c;
  background: #b9f3d0; font-weight: 700; cursor: pointer; }
button.secondary { color: #f4f2ec; background: rgba(255,255,255,0.12); }
button:disabled { opacity: 0.48; cursor: not-allowed; }
.controls { display: flex; gap: 12px; margin-top: 14px; align-items: center; }
.status { font-size: 13px; color: #b8c7c2; }
label { display: grid; gap: 7px; font-size: 12px; color: #aeb8b3; margin-bottom: 14px; }
input, select { width: 100%; border: 1px solid rgba(255,255,255,0.14);
  background: rgba(255,255,255,0.07); color: #f4f2ec; border-radius: 6px; padding: 10px; font: inherit; }
.line { margin: 10px 0; }
.line .tag { font-size: 11px; text-transform: uppercase; letter-spacing: 0.08em; color: #8aa; }
.line .text { font-size: 20px; line-height: 1.4; }
.line.minor .text { font-size: 14px; color: #9aa6a1; }
mark { background: #b9f3d0; color: #0b0d0c; border-radius: 3px; padding: 0 3px; }
.copied { color: #b9f3d0; font-size: 12px; margin-left: 8px; }
.key-row { display: flex; justify-content: space-between; align-items: center;
  font-size: 13px; padding: 6px 0; }
@media (max-width: 860px) { main { grid-template-columns: 1fr; } }
```

- [ ] **Step 2: Create `static/index.html`**

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Chaplin · Language Trainer</title>
  <link rel="stylesheet" href="/style.css" />
</head>
<body>
  <div class="topbar">
    <strong>Chaplin · Language Trainer</strong>
    <nav><a href="/index.html">Practice</a><a href="/review.html">Review</a></nav>
  </div>
  <main>
    <section class="card">
      <video id="preview" autoplay playsinline muted></video>
      <div class="controls">
        <button id="startCamera">Start Camera</button>
        <button id="record" disabled>● Record</button>
        <button id="stop" class="secondary" disabled>Stop</button>
        <span class="status" id="status">Camera not started</span>
      </div>
      <div id="results" style="margin-top:18px;">
        <div class="line"><div class="tag">Original <span id="srcLang"></span></div>
          <div class="text" id="original">—</div></div>
        <div class="line"><div class="tag">Refined
          <button id="play" class="secondary" style="padding:2px 8px;font-size:12px;">▶ Play</button>
          <span class="copied" id="copied"></span></div>
          <div class="text" id="refined">—</div></div>
        <div class="line minor"><div class="tag">Lip-read</div>
          <div class="text" id="vsr">—</div></div>
        <div class="status" id="explanation"></div>
      </div>
    </section>
    <aside class="card">
      <label>Target language
        <select id="targetLang"><option value="en">English</option>
          <option value="zh">Chinese</option><option value="es">Spanish</option></select>
      </label>
      <h4 style="margin:6px 0;">API keys</h4>
      <div id="keyRows"></div>
    </aside>
  </main>
  <audio id="ttsAudio"></audio>
  <script src="/app.js"></script>
</body>
</html>
```

- [ ] **Step 3: Create `static/app.js`**

```javascript
const $ = (id) => document.getElementById(id);
let stream, recorder, chunks = [], mimeType = "", lastRefined = "";

function chooseMimeType() {
  return ["video/webm;codecs=vp9,opus", "video/webm;codecs=vp8,opus",
    "video/webm", "video/mp4"].find(t => MediaRecorder.isTypeSupported(t)) || "";
}

async function loadConfig() {
  const cfg = await (await fetch("/api/v1/config")).json();
  $("targetLang").value = cfg.default_lang;
  renderKeys(cfg.keys_set);
}

function renderKeys(keysSet) {
  const names = { gladia: "Gladia (speech)", elevenlabs: "ElevenLabs (voice)", openai: "OpenAI (trainer)" };
  $("keyRows").innerHTML = "";
  Object.keys(names).forEach(name => {
    const row = document.createElement("div");
    row.className = "key-row";
    row.innerHTML = `<span>${names[name]}: ${keysSet[name] ? "set ✓" : "not set"}</span>`;
    const btn = document.createElement("button");
    btn.className = "secondary"; btn.textContent = "Change"; btn.style.padding = "4px 10px";
    btn.onclick = async () => {
      const val = prompt(`Enter ${names[name]} API key`);
      if (!val) return;
      const resp = await fetch("/api/v1/keys", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ [name]: val }) });
      renderKeys((await resp.json()).keys_set);
    };
    row.appendChild(btn);
    $("keyRows").appendChild(row);
  });
}

$("startCamera").onclick = async () => {
  stream = await navigator.mediaDevices.getUserMedia({
    video: { width: 640, height: 480, frameRate: 25 }, audio: true });
  $("preview").srcObject = stream;
  mimeType = chooseMimeType();
  $("record").disabled = false;
  $("status").textContent = "Camera ready";
};

$("record").onclick = () => {
  chunks = [];
  recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
  recorder.ondataavailable = e => { if (e.data.size > 0) chunks.push(e.data); };
  recorder.onstop = upload;
  recorder.start();
  $("record").disabled = true; $("stop").disabled = false;
  $("status").textContent = "Recording…";
};

$("stop").onclick = () => { $("stop").disabled = true; $("status").textContent = "Processing…"; recorder.stop(); };

function renderRefined(text, highlights) {
  let html = text;
  (highlights || []).forEach(h => {
    if (h.refined_phrase && html.includes(h.refined_phrase)) {
      html = html.replace(h.refined_phrase, `<mark title="${h.reason || ""}">${h.refined_phrase}</mark>`);
    }
  });
  return html;
}

async function upload() {
  const blob = new Blob(chunks, { type: mimeType || "video/webm" });
  const form = new FormData();
  form.append("file", blob, "clip.webm");
  form.append("target_lang", $("targetLang").value);
  const resp = await fetch("/api/v1/sessions", { method: "POST", body: form });
  $("record").disabled = false;
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({ detail: "Error" }));
    $("status").textContent = err.detail || "Error";
    return;
  }
  const data = await resp.json();
  $("original").textContent = data.original_text || "—";
  $("srcLang").textContent = data.source_lang ? `(${data.source_lang})` : "";
  $("refined").innerHTML = renderRefined(data.refined_text, data.highlights);
  $("vsr").textContent = data.vsr_raw_text || "—";
  lastRefined = data.refined_text || "";
  $("status").textContent = "Done";
  if (lastRefined) {
    try { await navigator.clipboard.writeText(lastRefined);
      $("copied").textContent = "copied ✓"; setTimeout(() => $("copied").textContent = "", 2000);
    } catch (_) {}
    playTTS(lastRefined);
  }
}

async function playTTS(text) {
  try {
    const resp = await fetch("/api/v1/tts", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }) });
    if (!resp.ok) return;
    const buf = await resp.blob();
    $("ttsAudio").src = URL.createObjectURL(buf);
    $("ttsAudio").play();
  } catch (_) {}
}

$("play").onclick = () => { if (lastRefined) playTTS(lastRefined); };
loadConfig();
```

- [ ] **Step 4: Manual verification**

Run: `pytest tests/test_api.py -v` (confirms static mount does not break routes)
Expected: PASS. (Full browser check happens in Task 14.)

- [ ] **Step 5: Commit**

```bash
git add static/style.css static/index.html static/app.js
git commit -m "feat: add practice view frontend"
```

---

## Task 13: Frontend — review view

**Files:**
- Create: `static/review.html`, `static/review.js`

- [ ] **Step 1: Create `static/review.html`**

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Chaplin · Review</title>
  <link rel="stylesheet" href="/style.css" />
</head>
<body>
  <div class="topbar">
    <strong>Chaplin · Review</strong>
    <nav><a href="/index.html">Practice</a><a href="/review.html">Review</a></nav>
  </div>
  <main style="grid-template-columns: 1fr;">
    <section class="card">
      <div class="controls">
        <button id="tabVocab">Vocabulary</button>
        <button id="tabHistory" class="secondary">History</button>
        <input id="search" placeholder="Search vocabulary…" style="max-width:240px;" />
        <label style="margin:0;flex-direction:row;align-items:center;gap:6px;">
          <input type="checkbox" id="starredOnly" style="width:auto;" /> Starred only</label>
      </div>
      <div id="list" style="margin-top:16px;"></div>
    </section>
  </main>
  <script src="/review.js"></script>
</body>
</html>
```

- [ ] **Step 2: Create `static/review.js`**

```javascript
const $ = (id) => document.getElementById(id);
let mode = "vocab";

async function loadVocab() {
  const params = new URLSearchParams();
  if ($("search").value) params.set("q", $("search").value);
  if ($("starredOnly").checked) params.set("starred", "true");
  const items = await (await fetch("/api/v1/vocab?" + params.toString())).json();
  $("list").innerHTML = items.length ? "" : "<p class='status'>No vocabulary yet.</p>";
  items.forEach(v => {
    const row = document.createElement("div");
    row.className = "card"; row.style.marginBottom = "10px";
    row.innerHTML = `<div class="line"><div class="text">
      <span style="color:#9aa6a1;">${v.original_phrase}</span> →
      <mark>${v.refined_phrase}</mark></div>
      <div class="status">${v.reason} · ${v.source_lang}</div></div>`;
    const star = document.createElement("button");
    star.className = "secondary"; star.style.padding = "4px 10px";
    star.textContent = v.starred ? "★ Starred" : "☆ Star";
    star.onclick = async () => {
      await fetch(`/api/v1/vocab/${v.id}`, { method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ starred: !v.starred }) });
      loadVocab();
    };
    row.appendChild(star);
    $("list").appendChild(row);
  });
}

async function loadHistory() {
  const items = await (await fetch("/api/v1/sessions?limit=100")).json();
  $("list").innerHTML = items.length ? "" : "<p class='status'>No sessions yet.</p>";
  items.forEach(s => {
    const row = document.createElement("div");
    row.className = "card"; row.style.marginBottom = "10px";
    row.innerHTML = `<div class="line"><div class="tag">${s.created_at} · ${s.source_lang}→${s.target_lang} · ${s.input_source}</div>
      <div class="text">${s.original_text}</div>
      <div class="text" style="color:#b9f3d0;">${s.refined_text}</div>
      <div class="status">lip: ${s.vsr_raw_text || "—"}</div></div>`;
    $("list").appendChild(row);
  });
}

function refresh() { mode === "vocab" ? loadVocab() : loadHistory(); }
$("tabVocab").onclick = () => { mode = "vocab"; $("tabVocab").className = "";
  $("tabHistory").className = "secondary"; refresh(); };
$("tabHistory").onclick = () => { mode = "history"; $("tabHistory").className = "";
  $("tabVocab").className = "secondary"; refresh(); };
$("search").oninput = () => { if (mode === "vocab") loadVocab(); };
$("starredOnly").onchange = () => { if (mode === "vocab") loadVocab(); };
refresh();
```

- [ ] **Step 3: Commit**

```bash
git add static/review.html static/review.js
git commit -m "feat: add review view (vocab + history tabs)"
```

---

## Task 14: README + end-to-end manual verification

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add a "Language Trainer (web)" section to `README.md`**

Append to `README.md`:

```markdown
## Language Trainer (web app)

An API-first web app that turns Chaplin into a daily spoken-language trainer.

### Run
```sh
./setup.sh                       # one-time: downloads VSR model weights
pip install -r requirements.txt
python web_chaplin.py            # serves http://127.0.0.1:8765
```

### Use
1. Open http://127.0.0.1:8765, click **Start Camera**.
2. In the right panel, set your **Gladia**, **OpenAI**, and **ElevenLabs** keys (stored
   locally in `~/.chaplin/keys.json`; you can change them anytime). Env vars
   `GLADIA_API_KEY` / `OPENAI_API_KEY` / `ELEVENLABS_API_KEY` override.
3. Press **Record**, speak (or silently mouth) to the camera, press **Stop**.
4. You'll see the original, a refined/translated version (highlighted upgrades, copied to
   clipboard, spoken aloud), and the lip-read line.
5. Visit **Review** for your vocabulary upgrades and session history.

Recordings are saved under `data/recordings/<id>/` as future fine-tuning pairs.
```

- [ ] **Step 2: Run the full test suite**

Run: `pytest -v`
Expected: all tests pass.

- [ ] **Step 3: Manual smoke checklist** (requires real keys + VSR weights)

- [ ] `python web_chaplin.py` starts and logs "Chaplin VSR model loaded."
- [ ] Browser loads, camera starts.
- [ ] Speaking English → original ≈ what you said, refined = improved version, highlights show, lip line populated, audio plays, clipboard has refined text.
- [ ] Speaking Chinese → original in Chinese, refined = English translation.
- [ ] Silent mouthing (no voice) → `input_source` is lip, original = lip-read text.
- [ ] Review page shows vocabulary and history; starring works.

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: document language trainer web app"
```

---

## Self-Review Notes

- **Spec coverage:** Trainer 3-line display (Task 10 + 12), translate/optimize modes (Task 7),
  voice-optional lip fallback (Task 10 `test_sessions_silent_falls_back_to_lip`), Gladia STT
  (Task 6), ElevenLabs TTS (Task 8), SQLite sessions+vocab (Task 4), review tab + highlights
  (Task 13), clipboard auto-copy (Task 12), recording capture for fine-tune (Task 10 writes
  `clip.webm`), visible/changeable keys (Task 3 + 12), API-first contract (Task 10),
  deploy-ready settings/env override (Task 2 + 3).
- **Out of scope confirmed absent:** no fine-tuning code, no iOS code, no auth.
- **Type consistency:** `transcribe()`/`Transcript`, `refine(...client,model)`,
  `synthesize(text,voice_id,key,model_id)`, `VSR.read_lips`, `Store` method names, and
  `create_app(...)` injection points all match across tasks 6–13.
