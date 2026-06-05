# Chaplin Language Trainer — Design Spec

**Date:** 2026-06-05
**Status:** Approved for planning
**Author:** brainstormed with user

## 1. Purpose

Turn Chaplin (a local English visual-speech-recognition / lip-reading tool) into a
**daily spoken-language trainer** for English-as-a-second-language users (built for the
user and their cousin, both Chinese speakers).

In one recording the user speaks (and/or silently mouths) to the camera. The app returns:

1. **What they said** (verbatim original, Chinese or English).
2. **A better version** — a natural English translation (if they spoke Chinese) or an
   AI-optimized, more-native rewrite (if they spoke English).
3. **A lip-read transcript** (minor line), which also lets them practice with no voice at all.

It plays the better version aloud (TTS), highlights the upgraded vocabulary, copies the
better version to the clipboard, and quietly saves each recording as a clean training pair
for **future** model fine-tuning.

## 2. Goals & non-goals

### Goals
- One-button capture of `video + audio`.
- Chinese → natural English translation; English → AI-optimized native rewrite.
- Always-visible lip-reading (VSR), usable as a voice-optional input fallback.
- ElevenLabs TTS playback of the refined line.
- Vocabulary memory + review tab with highlighted word upgrades.
- Auto-copy refined text to clipboard.
- Capture every session as a fine-tune pair `(lip video, ground-truth transcript, VSR output)`.
- **API-first** architecture so a future iOS client reuses the same backend unchanged.
- Visible, reusable, changeable API keys; local now, deploy-ready later.

### Non-goals (YAGNI)
- Actual model fine-tuning / training code (data capture only for now).
- The iOS app itself (backend is made API-ready; no Swift in this build).
- Auth / multi-user.
- Real-time streaming transcription (record-then-process is sufficient).
- Speaker diarization (single-speaker practice).

## 3. Tech stack (decided)

| Concern | Choice | Why |
|---|---|---|
| Backend | **FastAPI + Uvicorn** | API-first, async, Pydantic-native, auto OpenAPI → future Swift codegen |
| Schemas | **Pydantic v2** | Already used (`LanguageTrainerOutput`, `VocabHighlight`) |
| Frontend | **Vanilla HTML/CSS/JS** static files | Thin client, zero build, nothing wasted when iOS becomes primary |
| Storage | **stdlib `sqlite3`** + files | Structured, queryable, no extra dep |
| HTTP client | **httpx** (async) | Gladia + ElevenLabs calls |
| STT | **Gladia API** | Good Chinese + English, auto language detection |
| LLM | **OpenAI** (async, structured output); Ollama kept as optional switch | Reliable structured trainer output |
| Audio extract | **ffmpeg** (CLI), PyAV fallback | Robust; user already relies on ffmpeg |
| TTS | **ElevenLabs API** | User's choice |
| Config/secrets | **pydantic-settings** | env → local file → UI; deploy-ready |

## 4. Architecture & data flow

One **Record** button captures `video + audio` → uploads one clip → server fans out:

```
clip.webm ─┬─► extract audio (ffmpeg) ─► Gladia ─► transcript + detected language   (real input / ground truth)
           └─► VSR lip-reading ──────────────────► raw English text                 (prediction; always shown)

   input text  = transcript if voice present, else VSR text   (voice-optional fallback)
   input text ─► Trainer LLM ─► { source_lang, original, refined, highlights[] }

   save clip.webm + audio.wav + row to SQLite     (fine-tune pair, saved BEFORE external calls fail)
   return SessionResult JSON
```

Browser then renders three lines, highlights upgrades, **auto-copies** the refined line,
and calls `POST /api/v1/tts` to **play** the refined line.

### Future iOS client
Records on-device, uploads to `POST /api/v1/sessions`, renders the same `SessionResult`.
No backend change. The heavy VSR/Gladia/ElevenLabs work stays server-side.

## 5. Trainer behavior (core feature)

### Display — three lines
1. **Original** (verbatim, Chinese or English).
2. **Refined**:
   - Chinese input → natural, authentic **English translation**.
   - English input → **AI-optimized native version**, with upgraded words highlighted.
3. **Lip-read (minor)** — VSR output, always shown small.

### Input precedence (voice-optional)
- **User speaks** → Line 1 = Gladia transcript (real input); Line 3 = VSR lip-read shown
  small as comparison, and captured as the fine-tune pair.
- **User silent / only mouthing** → no usable audio, Gladia skipped → Line 1 falls back to
  the VSR lip-read, and the trainer refines *that*. Always practicable with no voice.
- Silent mode is **English-only** (lip-reading cannot produce Chinese), so the
  Chinese→English path triggers only when the user actually speaks.

### Mode selection
Source language comes from Gladia's detected language (voice) or is assumed English (silent).
- `source_lang == target_lang` (English→English) → "optimize/native rewrite" prompt.
- `source_lang != target_lang` (e.g. Chinese→English) → "natural translation" prompt.

Both produce a `LanguageTrainerOutput` (reuse existing model):
`source_language, original_text, refined_text, highlights: list[VocabHighlight], explanation`.
`VocabHighlight = { original_phrase, refined_phrase, reason }`.

Default `target_lang = en`, user-selectable in the UI.

## 6. Data model (SQLite)

**sessions**
| column | type | notes |
|---|---|---|
| id | TEXT (uuid) | pk |
| created_at | TEXT (ISO) | |
| source_lang | TEXT | detected or assumed |
| target_lang | TEXT | default `en` |
| original_text | TEXT | Line 1 (transcript or VSR fallback) |
| refined_text | TEXT | Line 2 |
| vsr_raw_text | TEXT | Line 3 (lip-read) |
| video_path | TEXT | `data/recordings/<id>/clip.webm` |
| audio_path | TEXT | `data/recordings/<id>/audio.wav` (null if silent) |
| duration_s | REAL | |
| confidence | REAL | Gladia confidence (null if silent) |
| input_source | TEXT | `audio` or `lip` |

**vocab**
| column | type | notes |
|---|---|---|
| id | INTEGER | pk autoincrement |
| session_id | TEXT | fk → sessions.id |
| original_phrase | TEXT | |
| refined_phrase | TEXT | |
| reason | TEXT | |
| source_lang | TEXT | |
| starred | INTEGER | 0/1 |
| created_at | TEXT | |

Recordings live at `data/recordings/<session_id>/` (`clip.webm`, `audio.wav`).
The `(clip.webm → original_text)` pair is the future fine-tune dataset, exportable later.
`data/` is gitignored.

## 7. API contract (v1)

| Method | Route | Request | Response |
|---|---|---|---|
| GET | `/` | — | `index.html` (practice) |
| GET | `/review` | — | `review.html` |
| POST | `/api/v1/sessions` | multipart: `file=clip.webm`, `target_lang` | `SessionResult` |
| GET | `/api/v1/sessions?limit=` | — | `[SessionResult]` (history) |
| GET | `/api/v1/vocab?starred=&q=` | — | `[VocabItem]` |
| PATCH | `/api/v1/vocab/{id}` | `{ starred }` | `VocabItem` |
| POST | `/api/v1/tts` | `{ text, lang?, voice? }` | `audio/mpeg` |
| GET | `/api/v1/config` | — | `{ keys_set, default_lang, voices }` |
| POST | `/api/v1/keys` | `{ gladia?, elevenlabs?, openai? }` | `{ keys_set }` |
| GET | `/healthz` | — | `{ status }` |

**SessionResult** (Pydantic): `id, created_at, source_lang, target_lang, original_text,
refined_text, vsr_raw_text, input_source, highlights: [VocabHighlight], confidence`.

The VSR model loads once at startup; inference runs in a threadpool (`run_in_executor`)
so it never blocks the event loop.

## 8. Secrets / keys

Resolution order per key (Gladia, ElevenLabs, OpenAI):
1. Environment variable (for future deploy).
2. Local gitignored file `~/.chaplin/keys.json`.
3. Entered in the UI Settings panel → persisted to that file.

**Settings panel**: lists all three keys **masked** (e.g. `gla_••••3f2a`) each with a
**Change** button. Keys persist so they're reused automatically; user can rotate any key
anytime. Nothing secret enters git.

## 9. Frontend (web, vanilla)

### Practice view (`index.html`)
- Always-on webcam preview (lips always visible).
- One **Record** button → `getUserMedia({video, audio:true})` → MediaRecorder → upload.
- Target-language selector (default English).
- Result: **Line 1 original**, **Line 2 refined** with `<mark>` on upgraded phrases (from
  `highlights`), **Line 3 lip-read** (small).
- **Play / replay** button (auto-plays TTS of Line 2 on result).
- **"Copied ✓"** indicator (auto `navigator.clipboard.writeText(refined_text)`; localhost is
  a secure context).

### Review view (`review.html`)
- **Vocab tab**: original → refined upgrades with reasons; star; search. The growing
  vocabulary list, AI suggestions highlighted.
- **History tab**: past sessions (original / refined / lip-read), replay TTS.

Shared `app.js` (API client) and `style.css` (reuse existing dark aesthetic).

## 10. Error handling

- **Recording + DB row saved before any external call** → a Gladia/LLM/TTS failure never
  loses the fine-tune pair; partial `SessionResult` is still returned.
- Missing key → `400` naming the missing key → UI opens Settings for it.
- Short/silent-and-no-lip clip (below min duration / empty result) → skipped with a message.
- External API errors surface as structured JSON `{ error, stage }`; UI shows a message.

## 11. Testing

- Trainer mode selection (zh → translate, en → optimize) with mocked LLM.
- SQLite CRUD (`store.py`): save session, save/list/star vocab.
- Key resolution order (env > file > absent).
- Audio extraction smoke test.
- API routes via FastAPI `TestClient` with VSR/Gladia/TTS mocked.
- Manual smoke checklist for the browser flow.

## 12. File structure

```
chaplin/
  app/
    __init__.py
    api.py            # FastAPI app + routes
    schemas.py        # Pydantic request/response models (+ reuse LanguageTrainerOutput, VocabHighlight)
    settings.py       # pydantic-settings config
    keys.py           # key resolution + persistence
    vsr.py            # lip-reading wrapper (loads InferencePipeline once)
    transcribe.py     # Gladia client
    trainer.py        # translate / optimize LLM
    tts.py            # ElevenLabs client
    store.py          # SQLite + recording files
    audio.py          # ffmpeg audio extraction
  static/
    index.html        # practice
    review.html       # review (vocab + history)
    app.js
    style.css
  data/               # gitignored: chaplin.db + recordings/<id>/
  tests/
  # untouched: chaplin.py (desktop), pipelines/, configs/, espnet/, hydra_configs/
  web_chaplin.py      # becomes a thin launcher for `uvicorn app.api:app`
  requirements.txt    # + fastapi, uvicorn, httpx, pydantic-settings
```

## 13. Deployment posture

Local run now (`uvicorn app.api:app`, 127.0.0.1). Config/secrets via env override so the
same code can deploy to a server (e.g. Render) later for remote iOS access — no code change,
just environment configuration.
