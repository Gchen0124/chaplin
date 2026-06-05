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
