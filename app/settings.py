import os
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CHAPLIN_", extra="ignore")

    data_dir: str = os.path.join(os.getcwd(), "data")
    default_target_lang: str = "en"

    # LLM (trainer). openai_base_url lets you point the OpenAI client at any
    # OpenAI-compatible endpoint, e.g. Gemini:
    #   CHAPLIN_OPENAI_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/
    #   CHAPLIN_OPENAI_MODEL=gemini-2.0-flash   (put the Gemini key in the OpenAI key field)
    llm_provider: str = "openai"  # "openai" | "ollama"
    openai_model: str = "gpt-4.1-mini"
    openai_base_url: str | None = None
    ollama_model: str = "qwen3:4b"

    # Gladia
    gladia_base_url: str = "https://api.gladia.io"

    # ElevenLabs
    elevenlabs_voice_id: str = "21m00Tcm4TlvDq8ikWAM"  # "Rachel" default
    elevenlabs_model_id: str = "eleven_multilingual_v2"

    # VSR
    vsr_config: str = "./configs/LRS3_V_WER19.1.ini"

    # server
    host: str = "127.0.0.1"
    port: int = 8765

    @property
    def db_path(self) -> str:
        return os.path.join(self.data_dir, "chaplin.db")

    @property
    def recordings_dir(self) -> str:
        return os.path.join(self.data_dir, "recordings")


@lru_cache
def get_settings() -> Settings:
    return Settings()
