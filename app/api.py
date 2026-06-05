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
                                    source_lang: str = Form("auto"),
                                    lipread: str = Form("on")):
        target_lang = target_lang or settings.default_target_lang
        forced = None if source_lang in (None, "", "auto") else source_lang
        lip_on = str(lipread).lower() in ("1", "true", "on", "yes")
        sid_dir, video_path = await _save_upload(file)
        duration = audio.probe_duration(video_path)
        has_voice = audio.has_audio(video_path)

        async def gen():
            t0 = time.monotonic()
            r0 = [None]  # refine start time (mutable for closure)

            def ms_since(start):
                return round((time.monotonic() - start) * 1000)

            def event(payload):
                return json.dumps(payload, ensure_ascii=False) + "\n"

            if duration < MIN_DURATION_S:
                yield event({"type": "error", "stage": "refined",
                             "detail": "Clip too short — please record at least 1 second."})
                return

            loop = asyncio.get_event_loop()
            has_gladia = has_voice and bool(keys.get("gladia"))
            # Run lip-reading only when asked, or when it's the only possible input
            # (silent clip / no Gladia key). It never blocks the refine/TTS path.
            run_vsr = lip_on or not has_gladia
            if has_voice and not keys.get("gladia"):
                yield event({"type": "error", "stage": "original",
                             "detail": "No Gladia key — falling back to lip-read."})

            tasks = {}
            vsr_future = None
            if run_vsr:
                vsr_future = loop.run_in_executor(None, vsr.read_lips, video_path)
                tasks[vsr_future] = "lip"
            transcribe_task = None
            if has_gladia:
                transcribe_task = asyncio.create_task(
                    _transcribe_audio(sid_dir, video_path, forced))
                tasks[transcribe_task] = "original"

            state = {"vsr_text": "", "audio_path": None, "need_input": True,
                     "src": "en", "input_source": "lip", "confidence": None,
                     "sid": None, "refine_out": None, "use_lip_input": False}
            refine_task = None

            def begin_refine(text, src, conf, isrc):
                state.update(src=src, input_source=isrc, confidence=conf)
                if not keys.get("openai"):
                    return None
                r0[0] = time.monotonic()
                return asyncio.create_task(refine_fn(
                    text, src, target_lang,
                    client=make_llm_client(keys.get("openai")),
                    model=settings.openai_model))

            active = set(tasks)
            while active:
                done, _ = await asyncio.wait(active, return_when=asyncio.FIRST_COMPLETED)
                for d in done:
                    active.discard(d)
                    if d is refine_task:
                        try:
                            state["refine_out"] = d.result()
                        except Exception as exc:  # noqa: BLE001
                            yield event({"type": "error", "stage": "refined", "detail": str(exc)})
                            refine_task = None
                            continue
                        out = state["refine_out"]
                        sid, _ = _persist(store, src=state["src"], target_lang=target_lang,
                                          original_text=out.original_text, trainer_out=out,
                                          vsr_text=state["vsr_text"], video_path=video_path,
                                          audio_path=state["audio_path"], duration=duration,
                                          confidence=state["confidence"],
                                          input_source=state["input_source"])
                        state["sid"] = sid
                        yield event({"type": "refined", "text": out.refined_text,
                                     "highlights": [h.model_dump() for h in out.highlights],
                                     "explanation": out.explanation,
                                     "stage_ms": ms_since(r0[0])})
                        refine_task = None
                        continue

                    kind = tasks.get(d)
                    if kind == "lip":
                        try:
                            state["vsr_text"] = (d.result() or "").strip()
                        except Exception:  # noqa: BLE001
                            state["vsr_text"] = ""
                        yield event({"type": "lip", "text": state["vsr_text"],
                                     "stage_ms": ms_since(t0)})
                        if state["sid"]:
                            store.update_vsr_text(state["sid"], state["vsr_text"])
                        # Start refine from lip if lip is our input source.
                        if state["need_input"] and (transcribe_task is None or state["use_lip_input"]):
                            if state["vsr_text"]:
                                state["need_input"] = False
                                refine_task = begin_refine(state["vsr_text"], "en", None, "lip")
                                if refine_task is None:
                                    yield event({"type": "error", "stage": "refined",
                                                 "detail": "Missing OpenAI API key."})
                                else:
                                    active.add(refine_task)
                                    yield event({"type": "original", "text": state["vsr_text"],
                                                 "source_lang": "en", "from_lip": True,
                                                 "stage_ms": ms_since(t0)})
                    elif kind == "original":
                        try:
                            transcript, state["audio_path"] = d.result()
                        except Exception as exc:  # noqa: BLE001
                            transcript = None
                            yield event({"type": "error", "stage": "original", "detail": str(exc)})
                        if transcript and transcript.text:
                            yield event({"type": "original", "text": transcript.text,
                                         "source_lang": forced or transcript.language,
                                         "confidence": transcript.confidence,
                                         "stage_ms": ms_since(t0)})
                            if state["need_input"]:
                                state["need_input"] = False
                                refine_task = begin_refine(
                                    transcript.text, forced or transcript.language,
                                    transcript.confidence, "audio")
                                if refine_task is None:
                                    yield event({"type": "error", "stage": "refined",
                                                 "detail": "Missing OpenAI API key."})
                                else:
                                    active.add(refine_task)
                        else:
                            # Audio empty: fall back to lip for the input.
                            state["use_lip_input"] = True
                            if state["need_input"] and state["vsr_text"]:
                                state["need_input"] = False
                                refine_task = begin_refine(state["vsr_text"], "en", None, "lip")
                                if refine_task is not None:
                                    active.add(refine_task)
                                    yield event({"type": "original", "text": state["vsr_text"],
                                                 "source_lang": "en", "from_lip": True,
                                                 "stage_ms": ms_since(t0)})

            if state["refine_out"] is None:
                yield event({"type": "error", "stage": "refined",
                             "detail": "No usable speech or lip movement detected."})
                return
            yield event({"type": "done", "id": state["sid"], "source_lang": state["src"],
                         "input_source": state["input_source"], "total_ms": ms_since(t0)})

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
