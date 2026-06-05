import asyncio
import json
import os
import time

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import Response, StreamingResponse
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

    def _config_response() -> ConfigResponse:
        return ConfigResponse(
            keys_set=keys.keys_set(),
            default_lang=settings.default_target_lang,
            voices=[settings.elevenlabs_voice_id],
        )

    async def _save_upload(file: UploadFile) -> tuple[str, str]:
        """Persist the upload, return (session_dir, video_path)."""
        os.makedirs(settings.recordings_dir, exist_ok=True)
        sid_dir = os.path.join(settings.recordings_dir, _new_id())
        os.makedirs(sid_dir, exist_ok=True)
        video_path = os.path.join(sid_dir, "clip.webm")
        with open(video_path, "wb") as fh:
            fh.write(await file.read())
        return sid_dir, video_path

    async def _transcribe_audio(sid_dir: str, video_path: str, language: str | None):
        """Extract audio and transcribe. Returns (transcript, audio_path)."""
        audio_path = os.path.join(sid_dir, "audio.wav")
        if not audio.extract_audio(video_path, audio_path):
            return None, None
        tr = await transcribe_fn(audio_path, key=keys.get("gladia"),
                                 base_url=settings.gladia_base_url, language=language)
        return tr, audio_path

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    @app.get("/api/v1/config", response_model=ConfigResponse)
    def get_config():
        return _config_response()

    @app.post("/api/v1/keys", response_model=ConfigResponse)
    def set_keys(req: KeysRequest):
        keys.set(req.model_dump())
        return _config_response()

    @app.post("/api/v1/sessions", response_model=SessionResult)
    async def create_session(file: UploadFile = File(...),
                             target_lang: str = Form(None),
                             source_lang: str = Form("auto")):
        target_lang = target_lang or settings.default_target_lang
        forced = None if source_lang in (None, "", "auto") else source_lang
        sid_dir, video_path = await _save_upload(file)
        duration = audio.probe_duration(video_path)
        has_voice = audio.has_audio(video_path)

        vsr_text = vsr.read_lips(video_path)

        original_text, src, confidence, audio_path, input_source = "", target_lang, None, None, "lip"
        if has_voice:
            if not keys.get("gladia"):
                raise HTTPException(400, "Missing Gladia API key.")
            tr, audio_path = await _transcribe_audio(sid_dir, video_path, forced)
            if tr and tr.text:
                original_text = tr.text
                src = forced or tr.language
                confidence = tr.confidence
                input_source = "audio"

        if not original_text:
            original_text = vsr_text
            src = "en"  # lip-reading model is English-only
            input_source = "lip"

        if not original_text or duration < MIN_DURATION_S:
            raise HTTPException(422, "No usable speech or lip movement detected.")
        if not keys.get("openai"):
            raise HTTPException(400, "Missing OpenAI API key.")

        llm_client = make_llm_client(keys.get("openai"))
        trainer_out = await refine_fn(original_text, src, target_lang,
                                      client=llm_client, model=settings.openai_model)
        sid, created_at = _persist(store, src=src, target_lang=target_lang,
                                   original_text=original_text, trainer_out=trainer_out,
                                   vsr_text=vsr_text, video_path=video_path,
                                   audio_path=audio_path, duration=duration,
                                   confidence=confidence, input_source=input_source)
        return SessionResult(
            id=sid, created_at=created_at, source_lang=src, target_lang=target_lang,
            original_text=original_text, refined_text=trainer_out.refined_text,
            vsr_raw_text=vsr_text, input_source=input_source,
            highlights=trainer_out.highlights, confidence=confidence,
        )

    @app.post("/api/v1/sessions/stream")
    async def create_session_stream(file: UploadFile = File(...),
                                    target_lang: str = Form(None),
                                    source_lang: str = Form("auto")):
        target_lang = target_lang or settings.default_target_lang
        forced = None if source_lang in (None, "", "auto") else source_lang
        sid_dir, video_path = await _save_upload(file)
        duration = audio.probe_duration(video_path)
        has_voice = audio.has_audio(video_path)

        async def gen():
            t0 = time.monotonic()

            def ms_since(start):
                return round((time.monotonic() - start) * 1000)

            def event(payload):
                return json.dumps(payload, ensure_ascii=False) + "\n"

            loop = asyncio.get_event_loop()
            vsr_future = loop.run_in_executor(None, vsr.read_lips, video_path)
            tasks = {vsr_future: "lip"}

            transcribe_task = None
            audio_path = None
            if has_voice and keys.get("gladia"):
                transcribe_task = asyncio.create_task(
                    _transcribe_audio(sid_dir, video_path, forced))
                tasks[transcribe_task] = "original"
            elif has_voice and not keys.get("gladia"):
                yield event({"type": "error", "stage": "original",
                             "detail": "Missing Gladia API key — using lip-read instead."})

            vsr_text = ""
            transcript = None
            pending = set(tasks)
            while pending:
                done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                for d in done:
                    kind = tasks[d]
                    try:
                        result = d.result()
                    except Exception as exc:  # noqa: BLE001
                        yield event({"type": "error", "stage": kind, "detail": str(exc)})
                        continue
                    if kind == "lip":
                        vsr_text = (result or "").strip()
                        yield event({"type": "lip", "text": vsr_text, "stage_ms": ms_since(t0)})
                    elif kind == "original":
                        transcript, audio_path = result
                        if transcript and transcript.text:
                            yield event({"type": "original", "text": transcript.text,
                                         "source_lang": forced or transcript.language,
                                         "confidence": transcript.confidence,
                                         "stage_ms": ms_since(t0)})

            # Decide the refine input: prefer audio transcript, else lip-read.
            if transcript and transcript.text:
                original_text = transcript.text
                src = forced or transcript.language
                confidence = transcript.confidence
                input_source = "audio"
            else:
                original_text = vsr_text
                src = "en"
                confidence = None
                input_source = "lip"
                yield event({"type": "original", "text": original_text,
                             "source_lang": src, "confidence": None,
                             "stage_ms": ms_since(t0), "from_lip": True})

            if not original_text or duration < MIN_DURATION_S:
                yield event({"type": "error", "stage": "refined",
                             "detail": "No usable speech or lip movement detected."})
                return
            if not keys.get("openai"):
                yield event({"type": "error", "stage": "refined",
                             "detail": "Missing OpenAI API key."})
                return

            r0 = time.monotonic()
            try:
                llm_client = make_llm_client(keys.get("openai"))
                trainer_out = await refine_fn(original_text, src, target_lang,
                                              client=llm_client, model=settings.openai_model)
            except Exception as exc:  # noqa: BLE001
                yield event({"type": "error", "stage": "refined", "detail": str(exc)})
                return

            sid, created_at = _persist(store, src=src, target_lang=target_lang,
                                       original_text=original_text, trainer_out=trainer_out,
                                       vsr_text=vsr_text, video_path=video_path,
                                       audio_path=audio_path, duration=duration,
                                       confidence=confidence, input_source=input_source)
            yield event({"type": "refined", "text": trainer_out.refined_text,
                         "highlights": [h.model_dump() for h in trainer_out.highlights],
                         "explanation": trainer_out.explanation,
                         "stage_ms": ms_since(r0)})
            yield event({"type": "done", "id": sid, "source_lang": src,
                         "input_source": input_source, "total_ms": ms_since(t0)})

        return StreamingResponse(gen(), media_type="application/x-ndjson")

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


def _persist(store: Store, *, src, target_lang, original_text, trainer_out, vsr_text,
             video_path, audio_path, duration, confidence, input_source):
    sid = store.save_session(
        source_lang=src, target_lang=target_lang, original_text=original_text,
        refined_text=trainer_out.refined_text, vsr_raw_text=vsr_text,
        video_path=video_path, audio_path=audio_path, duration_s=duration,
        confidence=confidence, input_source=input_source,
    )
    store.save_vocab(sid, trainer_out.highlights, source_lang=src)
    rows = store.list_sessions(limit=1)
    created_at = rows[0]["created_at"] if rows else ""
    return sid, created_at


def _new_id() -> str:
    import uuid
    return uuid.uuid4().hex
