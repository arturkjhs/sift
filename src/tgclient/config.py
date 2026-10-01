from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv
from platformdirs import user_data_dir

APP_NAME = "tgclient"


@dataclass(frozen=True)
class Settings:
    api_id: int
    api_hash: str
    data_dir: Path
    use_test_dc: bool
    td_log_level: int
    log_level: str

    @property
    def database_dir(self) -> Path:
        return self.data_dir / ("test" if self.use_test_dc else "prod") / "td_db"

    @property
    def files_dir(self) -> Path:
        return self.data_dir / ("test" if self.use_test_dc else "prod") / "td_files"


def load_settings() -> Settings:
    load_dotenv()
    api_id = os.environ.get("TG_API_ID", "").strip()
    api_hash = os.environ.get("TG_API_HASH", "").strip()
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
    )
