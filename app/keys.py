import json
import os

KEY_NAMES = ("gladia", "elevenlabs", "openai")
ENV_NAMES = {
    "gladia": "GLADIA_API_KEY",
    "elevenlabs": "ELEVENLABS_API_KEY",
    "openai": "OPENAI_API_KEY",
}


class KeyStore:
    def __init__(self, path: str):
        self.path = path

    def _load_file(self) -> dict:
        if not os.path.isfile(self.path):
            return {}
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (json.JSONDecodeError, OSError):
            return {}

    def get(self, name: str) -> str | None:
        env_val = os.getenv(ENV_NAMES.get(name, ""))
        if env_val:
            return env_val
        return self._load_file().get(name) or None

    def set(self, values: dict) -> None:
        data = self._load_file()
        for name in KEY_NAMES:
            v = values.get(name)
            if v:
                data[name] = v
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)

    def keys_set(self) -> dict:
        return {name: self.get(name) is not None for name in KEY_NAMES}

    def masked(self, name: str) -> str | None:
        val = self.get(name)
        if not val:
            return None
        tail = val[-4:]
        return f"{name[:3]}_••••{tail}"
