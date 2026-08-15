from app.schemas import (
    VocabHighlight,
    LanguageTrainerOutput,
    SessionResult,
    VocabItem,
    ConfigResponse,
    KeysRequest,
    DemoDraft,
    DemoUtterance,
    UtteranceEdit,
    UtterancesPatch,
)


def test_language_trainer_output_roundtrip():
    out = LanguageTrainerOutput(
        source_language="zh",
        original_text="你好世界",
        refined_text="Hello, world.",
        highlights=[VocabHighlight(original_phrase="世界", refined_phrase="world", reason="noun")],
        explanation="Direct greeting.",
    )
    data = out.model_dump()
    assert data["source_language"] == "zh"
    assert data["highlights"][0]["refined_phrase"] == "world"


def test_session_result_allows_null_confidence():
    res = SessionResult(
        id="abc",
        created_at="2026-06-05T00:00:00",
        source_lang="en",
        target_lang="en",
        original_text="i want go store",
        refined_text="I want to go to the store.",
        vsr_raw_text="I WANT GO STORE",
        input_source="lip",
        highlights=[],
        confidence=None,
    )
    assert res.confidence is None
    assert res.input_source == "lip"


def test_keys_request_all_optional():
    req = KeysRequest()
    assert req.gladia is None and req.elevenlabs is None and req.openai is None


def test_vocab_item_fields():
    item = VocabItem(
        id=1, session_id="s1", original_phrase="big", refined_phrase="substantial",
        reason="more precise", source_lang="en", starred=False, created_at="2026-06-05T00:00:00",
    )
    assert item.starred is False


def test_config_response():
    cfg = ConfigResponse(keys_set={"gladia": True, "elevenlabs": False, "openai": True},
                         default_lang="en", voices=["Rachel"])
    assert cfg.keys_set["gladia"] is True


def test_demo_utterance_and_draft():
    u = DemoUtterance(
        id="u1", idx=0, start_s=1.0, end_s=2.5,
        original_text="打开设置", english_text="Open Settings.", source_lang="zh",
    )
    draft = DemoDraft(
        id="d1", created_at="2026-08-15T00:00:00+00:00",
        source_lang="zh", target_lang="en", duration_s=8.0,
        status="transcribed", warning=None, utterances=[u],
    )
    assert draft.utterances[0].english_text == "Open Settings."
    assert draft.warning is None


def test_utterances_patch_only_allows_english_text():
    patch = UtterancesPatch(utterances=[UtteranceEdit(id="u1", english_text="Open settings.")])
    assert patch.utterances[0].id == "u1"
    dumped = UtteranceEdit(id="u1", english_text="x").model_dump()
    assert set(dumped) == {"id", "english_text"}
