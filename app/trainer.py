import json

from app.schemas import LanguageTrainerOutput

try:
    from openai import AsyncOpenAI
except ImportError:  # pragma: no cover
    AsyncOpenAI = None


def is_translation(source_lang: str, target_lang: str) -> bool:
    return source_lang.split("-")[0].lower() != target_lang.split("-")[0].lower()


def build_messages(text: str, source_lang: str, target_lang: str) -> list[dict]:
    if is_translation(source_lang, target_lang):
        system = (
            "You are a language tutor. The user spoke in their native language. "
            f"Translate their input into natural, authentic, native-sounding {target_lang}. "
            "Do not translate word-for-word; produce what a fluent native speaker would "
            "actually say. In 'highlights', list 2-5 key vocabulary words or expressions "
            "from your translation that the learner should study, each with the original "
            "phrase (in the source language) it maps to and a short reason. "
            "Set 'source_language' to the detected language, 'original_text' to the user's "
            "input verbatim, 'refined_text' to your translation, and 'explanation' to one "
            "short sentence. Return JSON matching the schema."
        )
    else:
        system = (
            "You are a language tutor for an English learner. Rewrite the user's input as "
            "a more native, concise, and sophisticated version while keeping their meaning. "
            "In 'highlights', list the specific words or phrases you upgraded: 'original_phrase' "
            "is what the user said, 'refined_phrase' is your improved wording, 'reason' explains "
            "why it sounds more native. Set 'source_language' to the detected language, "
            "'original_text' to the user's input verbatim, 'refined_text' to your rewrite, and "
            "'explanation' to one short sentence. Return JSON matching the schema."
        )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": f"Input:\n\n{text}"},
    ]


async def refine(text: str, source_lang: str, target_lang: str, *, client, model: str
                 ) -> LanguageTrainerOutput:
    response = await client.chat.completions.create(
        model=model,
        messages=build_messages(text, source_lang, target_lang),
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "language_trainer_output",
                "schema": LanguageTrainerOutput.model_json_schema(),
                "strict": True,
            },
        },
    )
    content = response.choices[0].message.content
    if isinstance(content, list):
        content = "".join(
            item.get("text", "") if isinstance(item, dict) else str(item)
            for item in content
        )
    return LanguageTrainerOutput.model_validate_json(json.dumps(json.loads(content)))


def make_openai_client(api_key: str):
    if AsyncOpenAI is None:
        raise RuntimeError("openai package not installed")
    return AsyncOpenAI(api_key=api_key)
