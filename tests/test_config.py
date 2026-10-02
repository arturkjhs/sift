"""Settings: env and .env first, then values baked into packaged builds."""

from __future__ import annotations

import os
import sys
import types
import unittest
from unittest import mock

from tgclient import config


class ConfigTest(unittest.TestCase):
    def setUp(self) -> None:
        patcher = mock.patch.dict(os.environ, {"TG_API_ID": "", "TG_API_HASH": ""})
        patcher.start()
        self.addCleanup(patcher.stop)
        dotenv = mock.patch.object(config, "load_dotenv", lambda *a, **k: False)  # not the real .env
        dotenv.start()
        self.addCleanup(dotenv.stop)

    def bake(self, **values: str) -> None:
        module = types.ModuleType("tgclient._build")
        module.__dict__.update(values)
        patcher = mock.patch.dict(sys.modules, {"tgclient._build": module})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_packaged_build_uses_baked_credentials(self) -> None:
        self.bake(VERSION="1.2.3", TG_API_ID="123", TG_API_HASH="abc", lower="ignored")
        self.assertEqual(config.build_info(),
                         {"VERSION": "1.2.3", "TG_API_ID": "123", "TG_API_HASH": "abc"})
        settings = config.load_settings()
        self.assertEqual((settings.api_id, settings.api_hash), (123, "abc"))

    def test_environment_wins_over_baked_values(self) -> None:
        self.bake(TG_API_ID="123", TG_API_HASH="abc")
        with mock.patch.dict(os.environ, {"TG_API_ID": "7", "TG_API_HASH": "env"}):
            settings = config.load_settings()
        self.assertEqual((settings.api_id, settings.api_hash), (7, "env"))

    def test_key_saved_in_settings_is_private_and_loaded_next_time(self) -> None:
        import stat
        import tempfile
        from pathlib import Path

        self.bake(TG_API_ID="1", TG_API_HASH="h")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "conf" / ".env"
            with mock.patch.object(config, "config_env_path", lambda: path), \
                    mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": ""}):
                config.save_user_setting("OPENROUTER_API_KEY", "sk-or-v1-secret")
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
                self.assertIn("OPENROUTER_API_KEY=sk-or-v1-secret", path.read_text())

                from dotenv import load_dotenv as real_load
                with mock.patch.object(config, "load_dotenv",
                                       lambda p=None, **k: p is not None and real_load(p)):
                    os.environ.pop("OPENROUTER_API_KEY")
                    settings = config.load_settings()
                self.assertEqual(settings.openrouter_api_key, "sk-or-v1-secret")
                self.assertEqual(settings.openrouter_key_source, "settings")

                config.remove_user_setting("OPENROUTER_API_KEY")
                self.assertNotIn("sk-or-v1-secret", path.read_text())

    def test_environment_key_is_reported_as_such(self) -> None:
        self.bake(TG_API_ID="1", TG_API_HASH="h")
        with mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-env"}):
            settings = config.load_settings()
        self.assertEqual(settings.openrouter_key_source, "environment")

    def test_missing_credentials_explain_what_to_do(self) -> None:
        self.bake(VERSION="1.0")
        with self.assertRaises(SystemExit) as raised:
            config.load_settings()
        self.assertIn("TG_API_ID", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
