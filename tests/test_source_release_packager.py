"""Regression tests for scripts/package_source_release.py.

The tests use temporary project trees and never package the user's real files.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGER_PATH = PROJECT_ROOT / "scripts" / "package_source_release.py"
_spec = importlib.util.spec_from_file_location("package_source_release", PACKAGER_PATH)
if _spec is None or _spec.loader is None:  # pragma: no cover - import guard
    raise RuntimeError(f"Cannot load source packager from {PACKAGER_PATH}")
packager = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(packager)


class SourceReleasePackagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="offline-reader-packager-")
        self.base = Path(self.temp.name)
        self.root = self.base / "sample_project"
        self.root.mkdir()
        self._write("README.md", "sample readme\n")
        self._write("requirements.txt", "requests\n")
        self._write("run_reader.py", "print('reader')\n")
        self._write("app/main.py", "print('app')\n")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _write(self, relative: str, content: str = "fixture") -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def _run_packager(self, *args: str) -> tuple[int, str, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        argv = [str(PACKAGER_PATH), "--root", str(self.root), *args]
        with patch.object(sys, "argv", argv), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result = packager.main()
        return result, stdout.getvalue(), stderr.getvalue()

    def test_excludes_private_models_caches_and_build_outputs(self) -> None:
        self._write(".env", "TOKEN=secret")
        self._write("config/.env.production", "TOKEN=secret")
        self._write(".Rhistory", "private interactive commands")
        self._write("private/credentials.json", '{"token":"secret"}')
        self._write("models/local.gguf", "fake model")
        self._write("weights/model.safetensors", "fake weights")
        self._write("cache/__pycache__/module.pyc", "bytecode")
        self._write("venv/lib/python/site.py", "venv")
        self._write("dist/old-release.zip", "old archive")
        self._write("archive/prototypes/app/old_ui.py", "print('legacy')")
        self._write("data/diary.db", "private diary")
        self._write("logs/app.log", "local log")

        output = self.base / "release.zip"
        result, stdout, stderr = self._run_packager("--output", str(output))
        self.assertEqual((result, stderr), (0, ""), stdout + stderr)
        with zipfile.ZipFile(output) as archive:
            names = set(archive.namelist())
        joined = "\n".join(names)
        for forbidden in (".env", ".Rhistory", "credentials.json", ".gguf", ".safetensors", ".pyc", "/venv/", "/dist/", "/archive/", "diary.db", ".log"):
            self.assertNotIn(forbidden, joined)
        self.assertIn("sample_project/README.md", names)
        self.assertIn("sample_project/requirements.txt", names)
        self.assertIn("sample_project/run_reader.py", names)
        self.assertIn("Excluded files", stdout)

    def test_preserves_required_and_normal_source_files(self) -> None:
        output = self.base / "normal-source.zip"
        result, stdout, stderr = self._run_packager("--output", str(output))
        self.assertEqual(result, 0, stderr + stdout)
        with zipfile.ZipFile(output) as archive:
            names = set(archive.namelist())
            self.assertEqual(archive.read("sample_project/README.md"), b"sample readme\n")
            self.assertEqual(archive.read("sample_project/app/main.py"), b"print('app')\n")
        self.assertIn("Created:", stdout)

    def test_symlinks_are_not_followed_or_archived(self) -> None:
        outside = self.base / "outside-secret.txt"
        outside.write_text("must not leak", encoding="utf-8")
        link = self.root / "linked-secret.txt"
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"Symlinks unavailable in this environment: {exc}")
        included, _excluded = packager.collect_files(self.root, self.base / "unused.zip")
        relative_names = {path.relative_to(self.root).as_posix() for path in included}
        self.assertNotIn("linked-secret.txt", relative_names)
        output = self.base / "symlink-test.zip"
        result, stdout, stderr = self._run_packager("--output", str(output))
        self.assertEqual(result, 0, stderr + stdout)
        with zipfile.ZipFile(output) as archive:
            self.assertNotIn("sample_project/linked-secret.txt", archive.namelist())
            self.assertNotIn(b"must not leak", b"".join(archive.read(n) for n in archive.namelist()))

    def test_output_inside_project_is_not_added_to_its_own_archive(self) -> None:
        output = self.root / "release" / "source.zip"
        result, stdout, stderr = self._run_packager("--output", str(output))
        self.assertEqual(result, 0, stderr + stdout)
        with zipfile.ZipFile(output) as archive:
            names = archive.namelist()
        self.assertNotIn("sample_project/release/source.zip", names)
        self.assertIn("sample_project/README.md", names)

    def test_missing_required_file_fails_with_clear_error(self) -> None:
        (self.root / "requirements.txt").unlink()
        result, stdout, stderr = self._run_packager("--dry-run")
        self.assertEqual(result, 2)
        self.assertIn("requirements.txt", stderr)
        self.assertIn("required release files missing", stderr)

    def test_dry_run_does_not_create_archive(self) -> None:
        output = self.base / "dry-run.zip"
        result, stdout, stderr = self._run_packager("--output", str(output), "--dry-run")
        self.assertEqual(result, 0, stderr + stdout)
        self.assertFalse(output.exists())
        self.assertIn("Dry run only", stdout)

    def test_created_archive_passes_integrity_check(self) -> None:
        self._write("nested/notes.txt", "archive integrity fixture")
        output = self.base / "integrity.zip"
        result, stdout, stderr = self._run_packager("--output", str(output))
        self.assertEqual(result, 0, stderr + stdout)
        with zipfile.ZipFile(output) as archive:
            self.assertIsNone(archive.testzip())


if __name__ == "__main__":
    unittest.main()
