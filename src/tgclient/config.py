from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv, set_key, unset_key
from platformdirs import user_config_dir, user_data_dir

APP_NAME = "tgclient"
# Reverse-DNS id for the macOS bundle, the Flatpak and the Linux .desktop file.
APP_ID = "io.github.tgclient.TgClient"

DEFAULT_SUMMARY_MODEL = "google/gemini-2.5-flash"
DEFAULT_TRANSCRIPTION_MODEL = "google/gemini-2.5-flash"
# ~220 MB, 384 dims, ~50 languages including ru/uk/cs/en. Runs locally.
DEFAULT_EMBEDDING_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


@dataclass(frozen=True)
class Settings:
    api_id: int
    api_hash: str
    data_dir: Path
    use_test_dc: bool
    td_log_level: int
    log_level: str
    openrouter_api_key: str = ""
    summary_model: str = DEFAULT_SUMMARY_MODEL
    transcription_model: str = DEFAULT_TRANSCRIPTION_MODEL
    embedding_model: str = DEFAULT_EMBEDDING_MODEL
    openrouter_key_source: str = ""  # "environment" (shell or ./.env) | "settings" | ""

    @property
    def database_dir(self) -> Path:
        return self.data_dir / ("test" if self.use_test_dc else "prod") / "td_db"

    @property
    def files_dir(self) -> Path:
        return self.data_dir / ("test" if self.use_test_dc else "prod") / "td_files"

    @property
    def ai_db_path(self) -> Path:
        """Our own SQLite: per-chat AI switches, transcripts, summaries (not messages)."""
        return self.data_dir / ("test" if self.use_test_dc else "prod") / "ai.sqlite3"

    @property
    def search_db_path(self) -> Path:
        """Search index: tokens and vectors only, no message text. Safe to delete."""
        return self.data_dir / ("test" if self.use_test_dc else "prod") / "search.sqlite3"

    @property
    def models_dir(self) -> Path:
        return self.data_dir / "models"


def config_env_path() -> Path:
    """Per-user .env of an installed app (e.g. for OPENROUTER_API_KEY)."""
    return Path(user_config_dir(APP_NAME, appauthor=False)) / ".env"


def save_user_setting(name: str, value: str) -> None:
    """Persist a value (e.g. the OpenRouter key from Settings) in the per-user .env.
    The file is readable by its owner only."""
    path = config_env_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(mode=0o600, exist_ok=True)
    path.chmod(0o600)
    set_key(str(path), name, value, quote_mode="never")


def remove_user_setting(name: str) -> None:
    path = config_env_path()
    if path.exists():
        unset_key(str(path), name, quote_mode="never")


def build_info() -> dict[str, str]:
    """Values baked in by the packaging scripts (tgclient/_build.py, not in git)."""
    try:
        from . import _build  # type: ignore[attr-defined]
    except ImportError:
        return {}
    return {k: str(v) for k, v in vars(_build).items() if k.isupper()}


def load_settings() -> Settings:
    load_dotenv()  # ./.env (development)
    from_environment = bool(os.environ.get("OPENROUTER_API_KEY", "").strip())
    load_dotenv(config_env_path())  # never overrides what is already set
    baked = build_info()
    api_id = os.environ.get("TG_API_ID", "").strip() or baked.get("TG_API_ID", "")
    api_hash = os.environ.get("TG_API_HASH", "").strip() or baked.get("TG_API_HASH", "")
    if not api_id or not api_hash:
        raise SystemExit("Set TG_API_ID and TG_API_HASH (see .env.example, my.telegram.org)")

    data_dir = os.environ.get("TG_DATA_DIR") or user_data_dir(APP_NAME, appauthor=False)
    return Settings(
        api_id=int(api_id),
        api_hash=api_hash,
        data_dir=Path(data_dir),
        use_test_dc=os.environ.get("TG_USE_TEST_DC", "0") == "1",
        td_log_level=int(os.environ.get("TD_LOG_LEVEL", "1")),
        log_level=os.environ.get("LOG_LEVEL", "WARNING").upper(),
        openrouter_api_key=os.environ.get("OPENROUTER_API_KEY", "").strip(),
        openrouter_key_source=(
            "environment" if from_environment
            else "settings" if os.environ.get("OPENROUTER_API_KEY", "").strip() else ""),
        summary_model=os.environ.get("TGC_SUMMARY_MODEL", "").strip() or DEFAULT_SUMMARY_MODEL,
        transcription_model=(os.environ.get("TGC_TRANSCRIPTION_MODEL", "").strip()
                             or DEFAULT_TRANSCRIPTION_MODEL),
        embedding_model=(os.environ.get("TGC_EMBEDDING_MODEL", "").strip()
                         or DEFAULT_EMBEDDING_MODEL),
    )
