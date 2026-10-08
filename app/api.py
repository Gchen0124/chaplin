import asyncio
import json
import os
import subprocess
import time
import uuid

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app import audio
from app import doubao_asr
from app import live as live_module
from app.export import export_demo as default_export_demo
from app.export import mux_original as default_mux_original
from app.keys import KeyStore
from app.schemas import (ConfigResponse, DemoDraft, DemoUtterance, KeysRequest,
                         SessionResult, UtterancesPatch, VocabItem)
from app.settings import Settings
from app.store import Store
from app.transcribe import transcribe as default_transcribe
from app.transcribe import transcribe_demo as default_transcribe_demo
from app.trainer import refine as default_refine, make_openai_client
from app.tts import synthesize as default_synthesize

MIN_DURATION_S = 1.0
STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")


class _NoCacheStaticFiles(StaticFiles):
    """StaticFiles that forbids caching.

    Without a Cache-Control header Chromium applies heuristic caching to the
    app's JS/CSS, so a rebuilt or patched app can keep running stale assets.
    These files are tiny and local, so revalidate every time instead.
    """

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-store"
        return response


def create_app(*, settings: Settings, store: Store, keys: KeyStore, vsr,
               transcribe_fn=default_transcribe, refine_fn=default_refine,
               synthesize_fn=default_synthesize, make_llm_client=make_openai_client,
               transcribe_demo_fn=default_transcribe_demo,
               export_demo_fn=default_export_demo,
               mux_original_fn=default_mux_original) -> FastAPI:
    app = FastAPI(title="Chaplin Language Trainer", version="1.0.0")
    app.state.settings = settings

    def _have_llm() -> bool:
        return bool(keys.get("opencode") or keys.get("openai"))

    def _llm_client():
        """OpenAI-compatible client. Prefers the opencode gateway when keyed."""
        oc = keys.get("opencode")
        if oc:
            from openai import AsyncOpenAI
            return AsyncOpenAI(
                api_key=oc,
                base_url=os.getenv("CHAPLIN_OPENCODE_BASE_URL", "https://opencode.ai/zen/go/v1"),
                default_headers={"x-opencode-session": str(uuid.uuid4())},
            )
        return make_llm_client(keys.get("openai"))

    def _llm_model() -> str:
        if keys.get("opencode"):
            return os.getenv("CHAPLIN_OPENCODE_MODEL", "deepseek-v4-flash")
        return settings.openai_model

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
        if not _have_llm():
            raise HTTPException(400, "Missing OpenAI API key.")

        llm_client = _llm_client()
        trainer_out = await refine_fn(original_text, src, target_lang,
                                      client=llm_client, model=_llm_model())
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
                                    lipread: str = Form("on"),
                                    live_text: str = Form("")):
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

            live = (live_text or "").strip()
            if live and _have_llm():
                # Fast path: the client already streamed a live transcript
                # (Doubao). Use it as the original so we skip the whole second
                # ASR pass and refine immediately.
                src = forced or "en"
                yield event({"type": "original", "text": live,
                             "source_lang": src, "from_lip": False,
                             "stage_ms": ms_since(t0)})
                r0[0] = time.monotonic()
                try:
                    out = await refine_fn(live, src, target_lang,
                                          client=_llm_client(),
                                          model=_llm_model())
                except Exception as exc:  # noqa: BLE001
                    yield event({"type": "error", "stage": "refined", "detail": str(exc)})
                    return
                sid, _ = _persist(store, src=src, target_lang=target_lang,
                                  original_text=live,
                                  trainer_out=out, vsr_text="", video_path=video_path,
                                  audio_path=None, duration=duration, confidence=None,
                                  input_source="live")
                yield event({"type": "refined", "text": out.refined_text,
                             "highlights": [h.model_dump() for h in out.highlights],
                             "explanation": out.explanation,
                             "stage_ms": ms_since(r0[0])})
                yield event({"type": "done", "id": sid, "source_lang": src,
                             "input_source": "live", "total_ms": ms_since(t0)})
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
                if not _have_llm():
                    return None
                r0[0] = time.monotonic()
                return asyncio.create_task(refine_fn(
                    text, src, target_lang,
                    client=_llm_client(),
                    model=_llm_model()))

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

    @app.get("/api/v1/sessions/{sid}/video")
    def session_video(sid: str):
        row = store.get_session(sid)
        if not row or not row.get("video_path") or not os.path.isfile(row["video_path"]):
            raise HTTPException(404, "Recording not found.")
        return FileResponse(row["video_path"], media_type="video/webm")

    @app.get("/api/v1/sessions/{sid}/thumb")
    def session_thumb(sid: str):
        """First-frame still used as the History card thumbnail (cached)."""
        row = store.get_session(sid)
        if not row or not row.get("video_path") or not os.path.isfile(row["video_path"]):
            raise HTTPException(404, "Recording not found.")
        src = row["video_path"]
        thumb = os.path.join(os.path.dirname(src), "thumb.jpg")
        if not os.path.isfile(thumb):
            subprocess.run(
                ["ffmpeg", "-y", "-ss", "0", "-i", src, "-frames:v", "1",
                 "-vf", "scale=480:-1", thumb],
                capture_output=True, text=True,
            )
        if not os.path.isfile(thumb):
            raise HTTPException(404, "No thumbnail.")
        return FileResponse(thumb, media_type="image/jpeg")

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

    def _demo_draft(row: dict) -> DemoDraft:
        return DemoDraft(
            id=row["id"], created_at=row["created_at"],
            source_lang=row["source_lang"], target_lang=row["target_lang"],
            duration_s=row["duration_s"] or 0.0, status=row["status"],
            warning=row.get("warning"),
            utterances=[DemoUtterance(
                id=u["id"], idx=u["idx"], start_s=u["start_s"], end_s=u["end_s"],
                original_text=u["original_text"], english_text=u["english_text"],
                source_lang=u["source_lang"],
            ) for u in row["utterances"]],
        )

    @app.get("/demo")
    def demo_page():
        return FileResponse(os.path.join(STATIC_DIR, "demo.html"))

    @app.post("/api/v1/demos", response_model=DemoDraft)
    async def create_demo_route(file: UploadFile = File(...),
                                camera: UploadFile | None = File(None),
                                target_lang: str = Form(None),
                                source_lang: str = Form("auto")):
        target_lang = target_lang or settings.default_target_lang
        forced = None if source_lang in (None, "", "auto") else source_lang
        did = _new_id()
        did_dir = os.path.join(settings.demos_dir, did)
        os.makedirs(did_dir, exist_ok=True)
        screen_path = os.path.join(did_dir, "screen.webm")
        with open(screen_path, "wb") as fh:
            fh.write(await file.read())
        cam_path = None
        if camera is not None:
            raw = await camera.read()
            if raw:
                cam_path = os.path.join(did_dir, "cam.webm")
                with open(cam_path, "wb") as fh:
                    fh.write(raw)
        duration = audio.probe_duration(screen_path)
        if duration < MIN_DURATION_S:
            raise HTTPException(422, detail={"error": "Clip too short.", "stage": "save"})
        store.create_demo(
            demo_id=did, source_lang=source_lang or "auto", target_lang=target_lang,
            duration_s=duration, screen_path=screen_path, cam_path=cam_path,
            status="saved",
        )
        if not keys.get("gladia"):
            raise HTTPException(400, "Missing Gladia API key.")
        if not _have_llm():
            raise HTTPException(400, "Missing OpenAI API key.")
        if not audio.has_audio(screen_path):
            store.update_demo(did, status="error", error="No audio track.")
            raise HTTPException(422, detail={"error": "No audio track.", "stage": "audio"})
        audio_path = os.path.join(did_dir, "audio.wav")
        if not audio.extract_audio(screen_path, audio_path):
            store.update_demo(did, status="error", error="Could not extract audio.")
            raise HTTPException(422, detail={"error": "Could not extract audio.", "stage": "audio"})
        store.update_demo(did, audio_path=audio_path)
        try:
            tr = await transcribe_demo_fn(
                audio_path, key=keys.get("gladia"),
                base_url=settings.gladia_base_url, target_lang=target_lang,
                language=forced,
            )
        except Exception as exc:
            store.update_demo(did, status="error", error=str(exc))
            raise HTTPException(502, detail={"error": str(exc), "stage": "gladia"}) from exc
        if not tr.utterances or not any(
                (u.original_text or u.english_text) for u in tr.utterances):
            raise HTTPException(422, detail={"error": "No speech detected.", "stage": "gladia"})

        warning = None
        llm_client = _llm_client()
        polished = []
        for i, u in enumerate(tr.utterances):
            src = u.source_lang or tr.language or "en"
            english = u.english_text
            try:
                same = (u.english_text or "").strip() == (u.original_text or "").strip()
                src_code = src.split("-")[0].lower()
                tgt_code = target_lang.split("-")[0].lower()
                if same and src_code != tgt_code:
                    out = await refine_fn(
                        u.original_text, src, target_lang,
                        client=llm_client, model=_llm_model())
                else:
                    out = await refine_fn(
                        u.english_text, target_lang, target_lang,
                        client=llm_client, model=_llm_model())
                english = out.refined_text
            except Exception:
                warning = f"polish failed on utterance {i}"
            polished.append({
                "start_s": u.start_s, "end_s": u.end_s,
                "original_text": u.original_text, "english_text": english,
                "source_lang": src,
            })
        store.replace_utterances(did, polished)
        store.update_demo(did, status="transcribed", source_lang=tr.language,
                          warning=warning)
        return _demo_draft(store.get_demo(did))

    @app.post("/api/v1/demos/audio", response_model=DemoDraft)
    async def create_demo_from_audio(file: UploadFile = File(...),
                                     target_lang: str = Form(None),
                                     source_lang: str = Form("auto"),
                                     polish: str = Form("off")):
        """Transcribe a mic sidecar. polish=off keeps Gladia translation on the original clock."""
        target_lang = target_lang or settings.default_target_lang
        forced = None if source_lang in (None, "", "auto") else source_lang
        do_polish = str(polish).lower() in ("1", "true", "on", "yes")
        did = _new_id()
        did_dir = os.path.join(settings.demos_dir, did)
        os.makedirs(did_dir, exist_ok=True)
        raw_name = file.filename or "audio.wav"
        ext = os.path.splitext(raw_name)[1].lower() or ".wav"
        uploaded = os.path.join(did_dir, f"upload{ext}")
        with open(uploaded, "wb") as fh:
            fh.write(await file.read())
        audio_path = os.path.join(did_dir, "audio.wav")
        if ext == ".wav":
            os.replace(uploaded, audio_path)
        elif not audio.extract_audio(uploaded, audio_path):
            raise HTTPException(422, detail={"error": "Could not read audio.", "stage": "audio"})
        duration = audio.probe_duration(audio_path)
        if duration < MIN_DURATION_S:
            raise HTTPException(422, detail={"error": "Clip too short.", "stage": "save"})
        store.create_demo(
            demo_id=did, source_lang=source_lang or "auto", target_lang=target_lang,
            duration_s=duration, screen_path=audio_path, audio_path=audio_path,
            status="saved",
        )
        if not keys.get("gladia"):
            raise HTTPException(400, "Missing Gladia API key.")
        if do_polish and not _have_llm():
            raise HTTPException(400, "Missing OpenAI API key.")
        try:
            tr = await transcribe_demo_fn(
                audio_path, key=keys.get("gladia"),
                base_url=settings.gladia_base_url, target_lang=target_lang,
                language=forced,
            )
        except Exception as exc:
            store.update_demo(did, status="error", error=str(exc))
            raise HTTPException(502, detail={"error": str(exc), "stage": "gladia"}) from exc
        if not tr.utterances or not any(
                (u.original_text or u.english_text) for u in tr.utterances):
            raise HTTPException(422, detail={"error": "No speech detected.", "stage": "gladia"})
        warning = None
        llm_client = _llm_client() if do_polish else None
        polished = []
        for i, u in enumerate(tr.utterances):
            src = u.source_lang or tr.language or "en"
            english = u.english_text
            if do_polish and llm_client is not None:
                try:
                    same = (u.english_text or "").strip() == (u.original_text or "").strip()
                    src_code = src.split("-")[0].lower()
                    tgt_code = target_lang.split("-")[0].lower()
                    if same and src_code != tgt_code:
                        out = await refine_fn(
                            u.original_text, src, target_lang,
                            client=llm_client, model=_llm_model())
                    else:
                        out = await refine_fn(
                            u.english_text, target_lang, target_lang,
                            client=llm_client, model=_llm_model())
                    english = out.refined_text
                except Exception:
                    warning = f"polish failed on utterance {i}"
            polished.append({
                "start_s": u.start_s, "end_s": u.end_s,
                "original_text": u.original_text, "english_text": english,
                "source_lang": src,
            })
        store.replace_utterances(did, polished)
        store.update_demo(did, status="transcribed", source_lang=tr.language,
                          warning=warning)
        return _demo_draft(store.get_demo(did))

    @app.get("/api/v1/demos/{demo_id}", response_model=DemoDraft)
    def get_demo_route(demo_id: str):
        row = store.get_demo(demo_id)
        if not row:
            raise HTTPException(404, "Demo not found.")
        return _demo_draft(row)

    @app.patch("/api/v1/demos/{demo_id}/utterances", response_model=DemoDraft)
    def patch_utterances(demo_id: str, body: UtterancesPatch):
        row = store.get_demo(demo_id)
        if not row:
            raise HTTPException(404, "Demo not found.")
        store.update_utterance_texts(
            demo_id, [(e.id, e.english_text) for e in body.utterances])
        return _demo_draft(store.get_demo(demo_id))

    @app.post("/api/v1/demos/{demo_id}/voice")
    async def export_voice_only(demo_id: str):
        """Return one continuous English MP3 (no video mux). For Recordly."""
        from app.export import join_english_script
        row = store.get_demo(demo_id)
        if not row:
            raise HTTPException(404, "Demo not found.")
        if not keys.get("elevenlabs"):
            raise HTTPException(400, "Missing ElevenLabs API key.")
        utterances = [u for u in row["utterances"] if (u.get("english_text") or "").strip()]
        if not utterances:
            raise HTTPException(422, detail={"error": "No English lines to speak.", "stage": "tts"})
        script = join_english_script(utterances)
        try:
            data = await synthesize_fn(
                script, settings.elevenlabs_voice_id,
                keys.get("elevenlabs"), settings.elevenlabs_model_id,
            )
        except Exception as exc:
            raise HTTPException(502, detail={"error": str(exc), "stage": "tts", "idx": 0}) from exc
        demo_dir = os.path.dirname(row["audio_path"] or row["screen_path"])
        voice_dir = os.path.join(demo_dir, "voice")
        os.makedirs(voice_dir, exist_ok=True)
        dest = os.path.join(voice_dir, "full.mp3")
        with open(dest, "wb") as fh:
            fh.write(data)
        return Response(content=data, media_type="audio/mpeg",
                        headers={"Content-Disposition": f'attachment; filename="english-{demo_id}.mp3"'})

    @app.post("/api/v1/demos/{demo_id}/voice-aligned")
    async def export_voice_aligned(demo_id: str):
        """Per-sentence TTS locked to Gladia times; file length = original take."""
        from app.export import export_aligned_voice
        row = store.get_demo(demo_id)
        if not row:
            raise HTTPException(404, "Demo not found.")
        if not keys.get("elevenlabs"):
            raise HTTPException(400, "Missing ElevenLabs API key.")
        utterances = [u for u in row["utterances"] if (u.get("english_text") or "").strip()]
        if not utterances:
            raise HTTPException(422, detail={"error": "No English lines to speak.", "stage": "tts"})
        demo_dir = os.path.dirname(row["audio_path"] or row["screen_path"])
        try:
            out_path = await export_aligned_voice(
                utterances=utterances,
                duration_s=row["duration_s"] or 0.0,
                demo_dir=demo_dir,
                synthesize_fn=synthesize_fn,
                voice_id=settings.elevenlabs_voice_id,
                tts_key=keys.get("elevenlabs"),
                model_id=settings.elevenlabs_model_id,
            )
        except RuntimeError as exc:
            msg = str(exc)
            raise HTTPException(502, detail={"error": msg, "stage": "tts"}) from exc
        return FileResponse(out_path, media_type="audio/mpeg",
                            filename=f"english-aligned-{demo_id}.mp3")

    @app.post("/api/v1/demos/{demo_id}/export")
    async def export_demo_route(demo_id: str):
        row = store.get_demo(demo_id)
        if not row:
            raise HTTPException(404, "Demo not found.")
        if not keys.get("elevenlabs"):
            raise HTTPException(400, "Missing ElevenLabs API key.")
        utterances = [u for u in row["utterances"] if (u.get("english_text") or "").strip()]
        if not utterances:
            raise HTTPException(422, detail={"error": "No English lines to speak.", "stage": "tts"})
        demo_dir = os.path.dirname(row["screen_path"])
        try:
            out_path = await export_demo_fn(
                utterances=utterances,
                video_duration=row["duration_s"] or 0.0,
                screen_path=row["screen_path"],
                demo_dir=demo_dir,
                synthesize_fn=synthesize_fn,
                voice_id=settings.elevenlabs_voice_id,
                tts_key=keys.get("elevenlabs"),
                model_id=settings.elevenlabs_model_id,
            )
        except RuntimeError as exc:
            msg = str(exc)
            if msg.startswith("tts:"):
                parts = msg.split(":")
                idx = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
                raise HTTPException(502, detail={"error": msg, "stage": "tts", "idx": idx}) from exc
            raise HTTPException(502, detail={"error": msg, "stage": "mux"}) from exc
        store.update_demo(demo_id, export_path=out_path, status="exported")
        return FileResponse(out_path, media_type="video/mp4",
                            filename=f"chaplin-demo-{demo_id}.mp4")

    @app.get("/api/v1/demos/{demo_id}/export")
    def get_export(demo_id: str):
        row = store.get_demo(demo_id)
        if not row or not row.get("export_path") or not os.path.isfile(row["export_path"]):
            raise HTTPException(404, "Export not found.")
        return FileResponse(row["export_path"], media_type="video/mp4",
                            filename=f"chaplin-demo-{demo_id}.mp4")

    @app.post("/api/v1/demos/{demo_id}/original")
    def export_original_route(demo_id: str):
        row = store.get_demo(demo_id)
        if not row:
            raise HTTPException(404, "Demo not found.")
        demo_dir = os.path.dirname(row["screen_path"])
        out_path = os.path.join(demo_dir, "original.mp4")
        try:
            mux_original_fn(
                screen_path=row["screen_path"],
                cam_path=row.get("cam_path"),
                out_path=out_path,
                video_duration=row["duration_s"] or 0.0,
            )
        except RuntimeError as exc:
            raise HTTPException(502, detail={"error": str(exc), "stage": "mux"}) from exc
        store.update_demo(demo_id, original_path=out_path)
        return FileResponse(out_path, media_type="video/mp4",
                            filename=f"chaplin-demo-{demo_id}-original.mp4")

    @app.get("/api/v1/demos/{demo_id}/original")
    def get_original(demo_id: str):
        row = store.get_demo(demo_id)
        if not row or not row.get("original_path") or not os.path.isfile(row["original_path"]):
            raise HTTPException(404, "Original export not found.")
        return FileResponse(row["original_path"], media_type="video/mp4",
                            filename=f"chaplin-demo-{demo_id}-original.mp4")

    # Live iFlytek transcription + next-sentence hints over WebSocket.
    # Registered before the catch-all static mount so /ws/live resolves.
    live_module.attach(app, keys)

    # Doubao (Volcengine) streaming ASR for the camera bubble captions.
    doubao_asr.attach(app, keys)

    if os.path.isdir(STATIC_DIR):
        app.mount("/", _NoCacheStaticFiles(directory=STATIC_DIR, html=True), name="static")

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
