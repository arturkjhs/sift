"""The macOS development app bundle (devbundle.py)."""

from __future__ import annotations

import os
import plistlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tgclient.devbundle import DEV_BUNDLE_ID, ensure_bundle


@unittest.skipUnless(sys.platform == "darwin", "macOS only")
class DevBundleTest(unittest.TestCase):
    def test_bundle_runs_python_as_our_app(self) -> None:
        root = Path(tempfile.mkdtemp())
        executable = ensure_bundle(root)
        contents = executable.parent.parent
        info = plistlib.loads((contents / "Info.plist").read_bytes())
        self.assertEqual(info["CFBundleIdentifier"], DEV_BUNDLE_ID)
        self.assertTrue((contents / "Resources" / "tgclient.icns").stat().st_size > 1000)
        self.assertEqual(ensure_bundle(root), executable)  # cached by stamp
        code = ("import sys; from Foundation import NSBundle; import tgclient; "
                "print(NSBundle.mainBundle().bundleIdentifier(), sys.prefix)")
        out = subprocess.run(
            [str(executable), "-c", code], capture_output=True, text=True, check=True,
            env={**os.environ, "__PYVENV_LAUNCHER__": sys.executable}).stdout.split()
        self.assertEqual(out, [DEV_BUNDLE_ID, sys.prefix])  # our bundle, same venv


if __name__ == "__main__":
    unittest.main()
