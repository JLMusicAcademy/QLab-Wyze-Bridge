"""Load and normalize bridge configuration from YAML + environment variables."""

import os
from dataclasses import dataclass, field

import yaml


@dataclass
class Config:
    credentials: dict = field(default_factory=dict)
    osc_host: str = "0.0.0.0"
    osc_port: int = 9000
    osc_prefix: str = "wyze"
    bulbs: dict = field(default_factory=dict)
    groups: dict = field(default_factory=dict)
    fade_min_interval: float = 0.25


# Credential keys mapped to the env var used when the YAML value is blank.
_CRED_ENV = {
    "email": "WYZE_EMAIL",
    "password": "WYZE_PASSWORD",
    "key_id": "WYZE_KEY_ID",
    "api_key": "WYZE_API_KEY",
    "totp_key": "WYZE_TOTP_KEY",
    "token": "WYZE_ACCESS_TOKEN",
}


def _resolve(value, env_name):
    """Resolve a config value, expanding ${VARS} and falling back to env."""
    if isinstance(value, str):
        expanded = os.path.expandvars(value)
        # If the value still contains an unresolved ${...}, treat as blank.
        if expanded and "${" not in expanded:
            return expanded
    elif value not in (None, ""):
        return value
    return os.environ.get(env_name)


def load_config(path):
    """Read the YAML config at *path* (if it exists) and merge with env vars."""
    # Load a .env file (if present) into the environment so credentials can
    # live there. Real shell exports take precedence over .env values.
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    raw = {}
    if path and os.path.exists(path):
        with open(path, "r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}

    wyze = raw.get("wyze", {}) or {}
    credentials = {}
    for key, env_name in _CRED_ENV.items():
        resolved = _resolve(wyze.get(key), env_name)
        if resolved:
            credentials[key] = resolved

    osc = raw.get("osc", {}) or {}
    fade = raw.get("fade", {}) or {}

    return Config(
        credentials=credentials,
        osc_host=osc.get("host", "0.0.0.0"),
        osc_port=int(osc.get("port", 9000)),
        osc_prefix=str(osc.get("prefix", "wyze")),
        bulbs=raw.get("bulbs", {}) or {},
        groups=raw.get("groups", {}) or {},
        fade_min_interval=float(fade.get("min_interval", 0.25)),
    )
