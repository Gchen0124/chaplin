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
