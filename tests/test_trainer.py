import json
import pytest

from app.trainer import refine, build_messages, is_translation
from app.schemas import LanguageTrainerOutput


def test_is_translation_true_for_chinese_to_english():
    assert is_translation("zh", "en") is True


def test_is_translation_false_for_english_to_english():
    assert is_translation("en", "en") is False
    assert is_translation("en-US", "en") is False


def test_build_messages_translation_mentions_translate():
    msgs = build_messages("你好", "zh", "en")
    assert any("translat" in m["content"].lower() for m in msgs)


def test_build_messages_optimize_mentions_native():
    msgs = build_messages("i want go store", "en", "en")
    assert any("native" in m["content"].lower() for m in msgs)


class FakeMessage:
    def __init__(self, content):
        self.content = content


class FakeChoice:
    def __init__(self, content):
        self.message = FakeMessage(content)


class FakeCompletion:
    def __init__(self, content):
        self.choices = [FakeChoice(content)]


class FakeOpenAI:
    def __init__(self, content):
        self._content = content
        self.chat = self
        self.completions = self

    async def create(self, **kwargs):
        return FakeCompletion(self._content)


@pytest.mark.asyncio
async def test_refine_returns_trainer_output():
    payload = json.dumps({
        "source_language": "en",
        "original_text": "i want go store",
        "refined_text": "I want to go to the store.",
        "highlights": [{"original_phrase": "go store",
                        "refined_phrase": "go to the store",
                        "reason": "needs preposition and article"}],
        "explanation": "Added grammar.",
    })
    client = FakeOpenAI(payload)
    out = await refine("i want go store", "en", "en", client=client, model="gpt-x")
    assert isinstance(out, LanguageTrainerOutput)
    assert out.refined_text == "I want to go to the store."
    assert out.highlights[0].refined_phrase == "go to the store"
