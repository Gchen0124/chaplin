import json
import os

KEY_NAMES = ("gladia", "elevenlabs", "openai", "deepseek", "doubao",
             "xfyun_app_id", "xfyun_api_key", "xfyun_api_secret")
ENV_NAMES = {
    "gladia": "GLADIA_API_KEY",
    "elevenlabs": "ELEVENLABS_API_KEY",
    "openai": "OPENAI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "doubao": "DOUBAO_API_KEY",
    "xfyun_app_id": "XFYUN_APP_ID",
    "xfyun_api_key": "XFYUN_API_KEY",
    "xfyun_api_secret": "XFYUN_API_SECRET",
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
        data = self._load_file()
        if data.get(name):
            return data[name]
        # Tolerate the dotted names used by the RokidFocus Android project
        # (xfyun.app.id / xfyun.api.key / xfyun.api.secret) so existing
        # credentials can be reused without renaming.
        alt = {
            "doubao": ("doubao.api.key", "doubao"),
            "xfyun_app_id": ("xfyun.app.id", "xfyun_app_id"),
            "xfyun_api_key": ("xfyun.api.key", "xfyun_api_key"),
            "xfyun_api_secret": ("xfyun.api.secret", "xfyun_api_secret"),
        }.get(name)
        if alt:
            for candidate in alt:
                if data.get(candidate):
                    return data[candidate]
        return None

    def load_file(self) -> dict:
        """Raw keys file, including dotted aliases. Used by the live module."""
        data = dict(self._load_file())
        for name, aliases in {
            "xfyun_app_id": ("xfyun.app.id",),
            "xfyun_api_key": ("xfyun.api.key",),
            "xfyun_api_secret": ("xfyun.api.secret",),
        }.items():
            if not data.get(name):
                for alias in aliases:
                    if data.get(alias):
                        data[name] = data[alias]
                        break
        return data

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
