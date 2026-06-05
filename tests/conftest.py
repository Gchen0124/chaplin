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
