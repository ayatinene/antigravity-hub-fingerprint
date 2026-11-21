"""Offline regression tests: extraction, release selection, and update safety."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import build_fingerprint as hub


class FingerprintTests(unittest.TestCase):
    def test_baseline_cl_is_preferred(self):
        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp) / "language_server.exe"
            binary.write_bytes(
                b"changelist 99999999 irrelevant "
                b"changelist 1023160026 with baseline testing\x00"
            )
            self.assertEqual(hub.extract_cl(binary), "1023160026")

    def test_cl_crosses_4mib_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp) / "language_server.exe"
            binary.write_bytes(
                b"\x00" * (4 * 1024 * 1024 - 12)
                + b"changelist 1023160026 with baseline test"
            )
            self.assertEqual(hub.extract_cl(binary), "1023160026")

    def test_ambiguous_cl_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp) / "language_server.exe"
            binary.write_bytes(
                b"changelist 1023160026 with baseline\n"
                b"changelist 1023160027 with baseline\n"
            )
            with self.assertRaisesRegex(ValueError, "Expected one"):
                hub.extract_cl(binary)

    def test_latest_release_is_numerically_sorted(self):
        class Response:
            def __init__(self, data):
                self.data = data
                self.offset = 0
            def read(self, size):
                chunk = self.data[self.offset:self.offset + size]
                self.offset += len(chunk)
                return chunk
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
        payload = json.dumps([
            {"version": "2.9.0", "execution_id": "123456789"},
            {"version": "2.10.0", "execution_id": "123456788"},
            {"version": "4.1.0", "execution_id": "123456787"},
        ]).encode()
        with patch.object(hub, "urlopen", return_value=Response(payload)):
            self.assertEqual(hub.get_latest_release(), ("2.10.0", "123456788"))

    def test_existing_version_skips_download(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "latest.json"
            output.write_text(json.dumps({
                "version": "2.33.0", "execution_id": "123456789", "cl": "1023160026"
            }))
            with patch.object(hub, "get_latest_release", return_value=("2.33.0", "123456789")), \
                 patch.object(hub, "download_installer", side_effect=AssertionError("download")):
                self.assertFalse(hub.run(output, Path(tmp) / "history"))

    def test_history_matches_published_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "latest.json"
            history = Path(tmp) / "history"
            with patch.object(hub, "get_latest_release", return_value=("2.33.0", "123456789")), \
                 patch.object(hub, "download_installer", return_value="0" * 64), \
                 patch.object(hub, "extract_language_server", return_value=Path("/tmp/mock.exe")), \
                 patch.object(hub, "extract_cl", return_value="1023160026"):
                self.assertTrue(hub.run(output, history))
            current = json.loads(output.read_text())
            self.assertEqual(current["version"], "2.33.0")
            self.assertEqual(current["cl"], "1023160026")
            self.assertEqual(current["os_type"], "windows")
            self.assertEqual(current["arch"], "amd64")
            self.assertEqual(json.loads((history / "2.33.0-123456789.json").read_text()), current)

    def test_downgrade_is_rejected_before_download(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "latest.json"
            output.write_text(json.dumps({"version": "2.34.0", "cl": "1030000000"}))
            with patch.object(hub, "get_latest_release", return_value=("2.33.0", "123456789")), \
                 patch.object(hub, "download_installer", side_effect=AssertionError("download")):
                with self.assertRaisesRegex(ValueError, "Refusing release downgrade"):
                    hub.run(output, Path(tmp) / "history")

    def test_archive_entry_disambiguation(self):
        self.assertEqual(
            hub.pick_entry(["$PLUGINSDIR/app-64.7z"], ("/app-64.7z", "app-64.7z")),
            "$PLUGINSDIR/app-64.7z",
        )
        with self.assertRaisesRegex(ValueError, "Ambiguous"):
            hub.pick_entry(
                ["resources/bin/language_server.exe", "other/bin/language_server.exe"],
                ("/bin/language_server.exe",),
            )


if __name__ == "__main__":
    unittest.main()
