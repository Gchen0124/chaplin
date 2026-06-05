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
