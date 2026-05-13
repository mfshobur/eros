import os
from pathlib import Path
import yaml

DEFAULT_CONFIG_PATH = Path(__file__).parent / "config.yaml"
USER_CONFIG_PATH = Path.home() / ".config" / "eros" / "config.yaml"


def load_config(path: Path | None = None) -> dict:
    config = {}

    for p in [DEFAULT_CONFIG_PATH, USER_CONFIG_PATH]:
        if p.exists():
            with open(p) as f:
                config.update(yaml.safe_load(f) or {})

    if path and Path(path).exists():
        with open(path) as f:
            config.update(yaml.safe_load(f) or {})

    api_keys = config.setdefault("api_keys", {})
    for key, env in [("openai", "OPENAI_API_KEY"), ("anthropic", "ANTHROPIC_API_KEY"), ("groq", "GROQ_API_KEY")]:
        if not api_keys.get(key) and os.environ.get(env):
            api_keys[key] = os.environ[env]

    return config


def save_default_model(model: str, path: Path | None = None) -> None:
    p = path or DEFAULT_CONFIG_PATH
    with open(p) as f:
        text = f.read()
    import re
    text = re.sub(r"^model:.*$", f"model: {model}", text, flags=re.MULTILINE)
    with open(p, "w") as f:
        f.write(text)
