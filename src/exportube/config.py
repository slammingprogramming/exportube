"""Configuration loading.

Precedence (highest wins): environment variables > config/config.yaml >
config/default_config.yaml built-in defaults.

Env var override format: EXPORTUBE_<PATH> where PATH is the dotted config
path with "__" separating nested keys, e.g.
EXPORTUBE_MATCHING__DURATION__STRONG_TOLERANCE_SECONDS=15
Also supports the flat convenience vars documented in .env.example for the
most commonly-touched secrets/paths (client secrets file, cookies, etc).
"""
from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "default_config.yaml"
USER_CONFIG_PATH = PROJECT_ROOT / "config" / "config.yaml"
DOTENV_PATH = PROJECT_ROOT / ".env"

ENV_PREFIX = "EXPORTUBE_"


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _deep_merge(base: dict, override: dict) -> dict:
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _apply_env_overrides(cfg: dict) -> dict:
    cfg = copy.deepcopy(cfg)
    for env_key, env_val in os.environ.items():
        if not env_key.startswith(ENV_PREFIX):
            continue
        path_part = env_key[len(ENV_PREFIX):]
        if "__" not in path_part:
            continue  # not a structured override; handled by flat lookups elsewhere
        parts = [p.lower() for p in path_part.split("__")]
        node = cfg
        for p in parts[:-1]:
            node = node.setdefault(p, {})
        leaf = parts[-1]
        node[leaf] = _coerce(env_val)
    return cfg


def _coerce(value: str) -> Any:
    lowered = value.lower()
    if lowered in ("true", "false"):
        return lowered == "true"
    try:
        if "." in value:
            return float(value)
        return int(value)
    except ValueError:
        return value


class Config:
    """Thin wrapper around the merged config dict with dotted-path access."""

    def __init__(self, data: dict, env: dict[str, str]):
        self._data = data
        self._env = env

    def get(self, dotted_path: str, default: Any = None) -> Any:
        node: Any = self._data
        for part in dotted_path.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def env(self, key: str, default: str | None = None) -> str | None:
        return self._env.get(ENV_PREFIX + key, default)

    def set(self, dotted_path: str, value: Any) -> None:
        """In-process override, e.g. a one-off CLI flag. Does not persist."""
        parts = dotted_path.split(".")
        node = self._data
        for p in parts[:-1]:
            node = node.setdefault(p, {})
        node[parts[-1]] = value

    @property
    def data_dir(self) -> Path:
        return Path(self.env("DATA_DIR", self.get("storage.data_dir", "./data")))

    @property
    def db_path(self) -> Path:
        return Path(self.env("DB_PATH", self.get("storage.db_path", "./data/exportube.sqlite3")))

    @property
    def output_dir(self) -> Path:
        return Path(self.get("export.output_dir", "./output"))

    def musicbrainz_user_agent(self) -> tuple[str, str, str]:
        app = self.env("MUSICBRAINZ_APP", "exportube")
        version = self.env("MUSICBRAINZ_VERSION", "0.1.0")
        contact = self.env("MUSICBRAINZ_CONTACT", "unset@example.com")
        return app, version, contact

    def as_dict(self) -> dict:
        return copy.deepcopy(self._data)


def load_config(config_path: Path | None = None) -> Config:
    _load_dotenv(DOTENV_PATH)

    default_cfg = yaml.safe_load(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8")) or {}

    user_path = config_path or USER_CONFIG_PATH
    user_cfg: dict = {}
    if user_path.exists():
        user_cfg = yaml.safe_load(user_path.read_text(encoding="utf-8")) or {}

    merged = _deep_merge(default_cfg, user_cfg)
    merged = _apply_env_overrides(merged)

    return Config(merged, dict(os.environ))
