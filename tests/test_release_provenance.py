"""Tests for source-release checksum and provenance generation."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "create_release_provenance.py"


class ReleaseProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.archive = self.root / "offline-reader-source-0.1.0.zip"
        self.archive.write_bytes(b"fixture source archive bytes\n")
        self.version_file = self.root / "VERSION"
        self.version_file.write_text("0.1.0\n", encoding="utf-8")
        self.output = self.root / "out"

    def tearDown(self):
        self.temp.cleanup()

    def run_script(self, *extra, expected=0):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--archive", str(self.archive),
             "--version-file", str(self.version_file), "--commit", "a" * 40,
             "--ref", "refs/tags/v0.1.0", "--output-dir", str(self.output), *extra],
            text=True, capture_output=True, check=False,
        )
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return result

    def test_writes_valid_sha256_sidecar(self):
        self.run_script()
        sidecar = self.output / f"{self.archive.name}.sha256"
        expected = hashlib.sha256(self.archive.read_bytes()).hexdigest()
        self.assertEqual(sidecar.read_text(encoding="utf-8"), f"{expected}  {self.archive.name}\n")

    def test_manifest_records_version_commit_ref_and_hash(self):
        self.run_script()
        manifest = json.loads((self.output / f"{self.archive.name}.provenance.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["version"], "0.1.0")
        self.assertEqual(manifest["commit"], "a" * 40)
        self.assertEqual(manifest["ref"], "refs/tags/v0.1.0")
        self.assertEqual(manifest["archive"], self.archive.name)
        self.assertEqual(manifest["sha256"], hashlib.sha256(self.archive.read_bytes()).hexdigest())
        self.assertTrue(manifest["created_at_utc"].endswith("+00:00"))

    def test_rejects_invalid_version(self):
        self.version_file.write_text("latest\n", encoding="utf-8")
        self.run_script(expected=2)

    def test_rejects_missing_archive(self):
        self.archive.unlink()
        self.run_script(expected=2)

    def test_rejects_empty_commit(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--archive", str(self.archive),
             "--version-file", str(self.version_file), "--commit", " ",
             "--ref", "main", "--output-dir", str(self.output)],
            text=True, capture_output=True, check=False,
        )
        self.assertEqual(result.returncode, 2)


if __name__ == "__main__":
    unittest.main()
