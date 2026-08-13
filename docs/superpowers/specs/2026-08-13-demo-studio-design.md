# Chaplin Demo Studio — Design Spec

**Date:** 2026-08-13
**Status:** Approved for planning
**Author:** brainstormed with user

## 1. Purpose

Add a **Demo** page to the existing Chaplin web app so someone who does not have
perfect English can record a product demo or social clip and publish it with a
polished English voice.

The user records the **screen** once, talking in Chinese or imperfect English.
After they stop:

1. Gladia transcribes each sentence and translates it, keeping per-sentence timestamps.
2. The trainer LLM polishes each English line (natural translation, or a more-native rewrite).
3. The user reviews and edits those English lines.
4. ElevenLabs speaks each edited line.
5. The export is an MP4: screen video + English AI voice + burned-in English captions.

The Language Trainer (`/`, `/review`) is unchanged. Demo Studio is a new surface
on the same FastAPI process, same API keys, same visual language.

## 2. Goals & non-goals

### Goals
- One-take screen recording with the user's mic as the source of truth.
- Webcam on during the take as a **preview only** — never in the exported file.
- Chinese → natural English; imperfect English → polished native English.
- Gladia utterance timestamps are the spine: edit, TTS, and captions all share them.
- Review-and-edit the English lines before any voice is generated.
- Exported file has **only** the English AI voice (original mic is dropped).
- English captions burned into the export (social / mute-safe).
- Recording is saved to disk before any external API call.
- Reuse existing Gladia, OpenAI, and ElevenLabs keys (`~/.chaplin/keys.json` / env).

### Non-goals (YAGNI)
- Live / phrase-by-phrase TTS during the take.
- Live captions during the take.
- Webcam picture-in-picture or side-by-side in the export.
- Lip-reading / VSR on this path.
- ElevenLabs Dubbing API.
- Voice cloning, multi-speaker, or voice picker beyond the existing configured voice.
- A second no-caption download.
- Separate sibling app.
- Auth / multi-user / iOS client.

## 3. Tech stack (decided)

Same stack as the Language Trainer, plus ffmpeg for the export mux.

| Concern | Choice | Why |
|---|---|---|
| Backend | Existing **FastAPI + Uvicorn** | One process, shared keys and settings |
| Schemas | **Pydantic v2** | Matches `app/schemas.py` |
| Frontend | Vanilla HTML/CSS/JS | Same as Practice / Review; no build step |
| Storage | **sqlite3** + files under `data/demos/<id>/` | Queryable drafts; large media on disk |
| STT + live-quality translation (async) | **Gladia pre-recorded** with `translation` | Already in the app; utterance timestamps + translated utterances |
| Polish | Existing **trainer LLM** (`app/trainer.py`) | Same zh→en / en-polish behavior |
| TTS | **ElevenLabs** (`app/tts.py`) | Already configured; one voice from settings |
| Capture | Browser `getDisplayMedia` + `getUserMedia` | Screen+mic recorded; camera preview only |
| Export | **ffmpeg** | Replace audio, burn ASS captions, write MP4 |
| Config/secrets | Existing `KeyStore` | No new secret store |

Gladia **Live** WebSocket is not used in v1. The take is processed after Stop
with the pre-recorded API so we get stable utterances, `enhanced` translation,
and `match_original_utterances` timestamps.

## 4. Architecture & data flow

```
Browser
  getDisplayMedia(screen)          → recorded
  getUserMedia(audio)              → recorded (source of truth)
  getUserMedia(video: camera)      → preview only, not muxed

  MediaRecorder(screen + mic) ──► POST /api/v1/demos
                                    multipart: file=screen.webm, source_lang, target_lang

Server
  save data/demos/<id>/screen.webm     (before any API)
  extract data/demos/<id>/audio.wav
  Gladia /v2/pre-recorded
      translation: true
      translation_config:
        target_languages: [target_lang]
        model: enhanced
        match_original_utterances: true
  → utterances[] { start, end, original_text, english_text }
  trainer.refine() on each english_text (always; timestamps stay put)
  persist demo + utterances
  return DemoDraft JSON

Browser review
  table: start–end | original | editable english
  PATCH /api/v1/demos/{id}/utterances   (save edits)

  Generate voice & export
  POST /api/v1/demos/{id}/export
      per utterance: ElevenLabs TTS
      align each clip to utterance.start
      if TTS longer than (next.start - start) or (end - start):
          slight atempo speed-up; if still long, bleed into the following pause
      write captions.ass from edited english + timestamps
      ffmpeg: screen.webm + mixed voice → export.mp4 with ASS burned in
  return export.mp4
```

### Gladia timestamp contract

The Gladia translation result is the only clock the rest of the system trusts.

- Each source utterance has `start` and `end` in seconds.
- `match_original_utterances: true` means the translated utterance uses the
  same `start` / `end` as the source sentence.
- The review row, the TTS clip, and the ASS `Dialogue` line for that sentence
  all use those two numbers.
- If Gladia returns a translation `results[]` entry for the target language,
  `english_text` comes from the matching translated utterance. If translation
  is missing (source already is the target, or an empty result), `english_text`
  starts as the source transcript and the trainer still polishes it.
- Trainer polish **must not** change `start` / `end`. It only rewrites text.
- User edits **must not** change `start` / `end`. They only rewrite `english_text`.

### TTS alignment

For utterance `i` with `[start_i, end_i)` and next start `start_{i+1}`
(or video duration if last):

1. Synthesize `english_text` to a mono clip.
2. Allowed window = `start_{i+1} - start_i` (prefer the pause after the
   sentence, not a hard cut at `end_i`).
3. If clip duration ≤ window, pad with silence and place at `start_i`.
4. If clip duration > window, apply `atempo` up to **1.15×**. If still long,
   place at `start_i` and let it occupy the following pause; never overlap
   the next utterance's start.

Original mic audio is discarded on export. The MP4 audio track is the
aligned English clips only (silence elsewhere).

### Webcam

- **Start screen** requests display + mic (required) and camera (optional).
- Camera preview is a small muted `<video>` in the right column. It is never
  added to `MediaRecorder`.
- If the user denies camera permission, recording still works.

## 5. Data model (SQLite + files)

New tables. Do not alter `sessions` / `vocab`.

**demos**

| column | type | notes |
|---|---|---|
| id | TEXT (uuid) | pk |
| created_at | TEXT (ISO) | |
| source_lang | TEXT | `auto` resolved to detected code after Gladia |
| target_lang | TEXT | default `en` |
| duration_s | REAL | from the screen file |
| screen_path | TEXT | `data/demos/<id>/screen.webm` |
| audio_path | TEXT | `data/demos/<id>/audio.wav` |
| export_path | TEXT | `data/demos/<id>/export.mp4` (null until export) |
| status | TEXT | `saved` \| `transcribed` \| `exported` \| `error` |
| error | TEXT | last structured error, if any |

**demo_utterances**

| column | type | notes |
|---|---|---|
| id | TEXT (uuid) | pk |
| demo_id | TEXT | fk → demos.id |
| idx | INTEGER | order in the take |
| start_s | REAL | Gladia start, immutable after insert |
| end_s | REAL | Gladia end, immutable after insert |
| original_text | TEXT | source-language sentence |
| english_text | TEXT | current English (polished, then user-edited) |
| source_lang | TEXT | per-utterance detected language |

Files under `data/demos/<id>/`:

```
screen.webm
audio.wav
voice/          # one mp3 (or pcm/wav) per utterance, named by idx
captions.ass
export.mp4
```

`data/` stays gitignored.

## 6. API contract

| Method | Route | Request | Response |
|---|---|---|---|
| GET | `/demo` | — | `demo.html` |
| POST | `/api/v1/demos` | multipart: `file`, `source_lang`, `target_lang` | `DemoDraft` |
| GET | `/api/v1/demos/{id}` | — | `DemoDraft` |
| PATCH | `/api/v1/demos/{id}/utterances` | `{ utterances: [{ id, english_text }] }` | `DemoDraft` |
| POST | `/api/v1/demos/{id}/export` | — | `video/mp4` (`export.mp4`) |
| GET | `/api/v1/demos/{id}/export` | — | `video/mp4` (already-built file) |

Existing trainer routes, `/review`, `/api/v1/tts`, `/api/v1/keys`, `/api/v1/config`
are unchanged. Demo TTS is server-side only (needed for mux); the browser does
not call `/api/v1/tts` for export.

**DemoDraft**

```
id, created_at, source_lang, target_lang, duration_s, status,
warning,   # optional; non-fatal trainer/polish error
utterances: [DemoUtterance]
```

`GET /api/v1/demos/{id}/export` returns `404` if `export_path` is still empty.

**DemoUtterance**

```
id, idx, start_s, end_s, original_text, english_text, source_lang
```

Errors are `{ error, stage }` where `stage` is one of
`save`, `audio`, `gladia`, `translate`, `refine`, `tts`, `mux`.

Missing Gladia / OpenAI / ElevenLabs key → `400` naming the key (same
pattern as the trainer). The Demo page reuses the existing Settings / key rows.

## 7. Gladia request shape

Add `transcribe_demo()` in `app/transcribe.py`. Keep the existing `transcribe()`
return type unchanged so the Language Trainer does not break. Same Gladia
client, same key.

Pre-recorded config for a demo:

```json
{
  "audio_url": "...",
  "detect_language": true,
  "translation": true,
  "translation_config": {
    "target_languages": ["en"],
    "model": "enhanced",
    "match_original_utterances": true,
    "lipsync": false,
    "context_adaptation": true,
    "context": "Product demo / software walkthrough narration"
  }
}
```

If the user forces a source language (not `auto`), set `detect_language: false`
and `language` as the trainer already does.

Parse:

- `result.transcription.utterances[]` → `original_text`, `start`, `end`, language
- `result.translation.results[target].utterances[]` → `english_text` with the
  same `start` / `end`

If a translated utterance cannot be matched by timestamp (tolerance 50 ms),
fall back to `original_text` and let the trainer produce English.

## 8. Frontend (web, vanilla)

New files: `static/demo.html`, `static/demo.js`. Reuse `style.css` and the
key-row markup pattern from `index.html`. Add **Demo** to the top nav on
Practice, Review, and Demo.

### Layout
- **Left card:** screen `<video>` preview (the display surface being recorded).
  Controls: **Start screen**, **Record**, **Stop**, status text.
- **Right card:** small muted webcam preview; source / target language selects
  (defaults: source `auto`, target `en`); the same API-key rows as Practice.
- **After Stop, full-width under the two cards:** utterance table
  (`start–end` \| original \| editable English), then
  **Generate voice & export**, then an `<video>` player + download link
  for `export.mp4`.
- Status line names the current stage: saving → transcribing → translating
  → polishing → ready to edit → generating voice → muxing → done.

### Capture rules
- **Start screen** calls `getDisplayMedia({ video: true, audio: false })` and
  `getUserMedia({ audio: true })`, then mixes mic into the recorded stream
  with a `MediaStreamAudioSourceNode` / `AudioContext` (tab audio is not
  captured). Camera is a separate `getUserMedia({ video: true })` that is
  never added to the recorder.
- `MediaRecorder` mime type: `video/webm;codecs=vp8,opus` (same family as
  the trainer).
- Minimum duration: 1.0 s (same as trainer).

### Review rules
- Editing a cell updates local state; blur or an explicit Save calls PATCH.
- **Generate voice & export** PATCHes any dirty cells first, then POSTs export.
- The download filename is `chaplin-demo-<id>.mp4`.

## 9. Captions

ASS (not SRT), burned in with ffmpeg `ass=` so social platforms keep them.

- One `Dialogue` line per utterance.
- Text = the **edited** `english_text`.
- Times = Gladia `start_s` / `end_s` (use `end_s`, not the possibly longer
  TTS clip, so captions do not collide).
- Style: readable on a product-demo screen — bottom-center, outlined white
  on a semi-transparent box, Playfair/system-safe fallback is not required;
  use **Arial** (ffmpeg-safe) at a size that stays inside 16:9 and 9:16
  captures. Margins L=40, R=40, V=40. Alignment=2. Wrap at ~42 characters.
- `BorderStyle=4`, opacity ~80% box, no karaoke.

## 10. Error handling

- Persist `screen.webm` and a `demos` row with `status=saved` **before**
  Gladia / LLM / TTS. A failed Gladia call leaves the take downloadable as
  the raw webm from disk (v1 does not need a raw-download button; the file
  is there for retry).
- Retry: `POST /api/v1/demos` is for new takes. Re-running Gladia on an
  existing id is out of scope; the user records again.
- Empty transcript → `422` with `stage=gladia`.
- Trainer failure → return Gladia English (or original) without polish;
  do not fail the draft. Surface a warning in `DemoDraft` via `status`
  staying `transcribed` and a non-fatal `error` field.
- TTS failure on one utterance → `502` `{ error, stage: "tts", idx }`;
  already-written voice files stay on disk so a retry of export can skip
  successful idxes.
- ffmpeg failure → `502` `{ error, stage: "mux" }`.
- Missing key → `400` naming it; UI opens the key rows.

## 11. Testing

- Gladia mapper: source + translated utterances with matching timestamps
  produce the expected `DemoUtterance` list; unmatched translation falls
  back; forced language disables detect.
- PATCH persists only `english_text`; attempts to send new timestamps are
  ignored.
- TTS alignment helper: clip shorter than window is padded; clip up to
  1.15× window is sped; longer clip does not start before `start_i` or
  overlap `start_{i+1}`.
- Export route with mocked Gladia / trainer / TTS / ffmpeg via TestClient.
- Existing trainer tests stay green; Demo routes do not load VSR.

## 12. File structure

```
chaplin/
  app/
    api.py              # add demo routes; trainer routes untouched
    schemas.py          # + DemoDraft, DemoUtterance, UtterancesPatch
    transcribe.py       # + transcribe_demo(); existing transcribe() unchanged
    trainer.py          # reused per utterance
    tts.py              # reused
    store.py            # + demos / demo_utterances
    audio.py            # reused extract; + mux/align helpers (or app/export.py)
    export.py           # NEW: align clips, write ASS, ffmpeg mux
  static/
    demo.html           # NEW
    demo.js             # NEW
    index.html          # nav link only
    review.html         # nav link only
    style.css           # demo table / preview tweaks
  data/demos/           # gitignored
  tests/
    test_demo_api.py
    test_demo_align.py
    test_transcribe.py  # extend for translation mapping
  docs/superpowers/specs/2026-08-13-demo-studio-design.md
```

Desktop `chaplin.py` and the VSR pipelines stay untouched.

## 13. Deployment posture

Same as the trainer: `web_chaplin.py` / `uvicorn` on 127.0.0.1. No new env
vars. `GLADIA_API_KEY`, `OPENAI_API_KEY`, `ELEVENLABS_API_KEY` continue to
override `~/.chaplin/keys.json`.
