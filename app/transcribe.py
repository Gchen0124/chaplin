import asyncio

import httpx
from pydantic import BaseModel


class Transcript(BaseModel):
    text: str
    language: str
    confidence: float | None = None


async def transcribe(audio_path: str, key: str, base_url: str,
                     poll_interval: float = 1.0, max_polls: int = 60,
                     language: str | None = None) -> Transcript:
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

        # 2. request transcription. If a language is forced, disable auto-detect
        #    (Gladia's detector sometimes confuses zh with ko on short clips).
        config = {"audio_url": audio_url, "diarization": False}
        if language:
            config["detect_language"] = False
            config["language"] = language
        else:
            config["detect_language"] = True
        req = await client.post(
            f"{base_url}/v2/pre-recorded",
            headers=headers,
            json=config,
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
