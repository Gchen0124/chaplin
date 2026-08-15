from pydantic import BaseModel, ConfigDict


class VocabHighlight(BaseModel):
    model_config = ConfigDict(extra="forbid")
    original_phrase: str
    refined_phrase: str
    reason: str


class LanguageTrainerOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_language: str
    original_text: str
    refined_text: str
    highlights: list[VocabHighlight]
    explanation: str


class SessionResult(BaseModel):
    id: str
    created_at: str
    source_lang: str
    target_lang: str
    original_text: str
    refined_text: str
    vsr_raw_text: str
    input_source: str  # "audio" | "lip"
    highlights: list[VocabHighlight]
    confidence: float | None = None


class VocabItem(BaseModel):
    id: int
    session_id: str
    original_phrase: str
    refined_phrase: str
    reason: str
    source_lang: str
    starred: bool
    created_at: str


class ConfigResponse(BaseModel):
    keys_set: dict[str, bool]
    default_lang: str
    voices: list[str]


class KeysRequest(BaseModel):
    gladia: str | None = None
    elevenlabs: str | None = None
    openai: str | None = None


class DemoUtterance(BaseModel):
    id: str
    idx: int
    start_s: float
    end_s: float
    original_text: str
    english_text: str
    source_lang: str


class DemoDraft(BaseModel):
    id: str
    created_at: str
    source_lang: str
    target_lang: str
    duration_s: float
    status: str
    warning: str | None = None
    utterances: list[DemoUtterance]


class UtteranceEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    english_text: str


class UtterancesPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    utterances: list[UtteranceEdit]
