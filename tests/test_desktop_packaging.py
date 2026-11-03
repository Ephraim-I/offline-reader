import importlib.util
import os
from pathlib import Path
import tempfile
import unittest

from scripts import desktop_launcher


ROOT = Path(__file__).resolve().parents[1]
BUILD_DEB_PATH = ROOT / "packaging" / "linux" / "build_deb.py"
SPEC = importlib.util.spec_from_file_location("offline_reader_build_deb", BUILD_DEB_PATH)
build_deb = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(build_deb)


class DesktopLauncherTests(unittest.TestCase):
    def test_runtime_config_round_trips_and_is_limited_to_runtime_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "runtime.json"
            desktop_launcher.save_runtime_config(
                {"model": "/models/local.gguf", "llama_server": "/bin/llama-server", "ignored": "value"},
                path,
            )
            self.assertEqual(
                desktop_launcher.load_runtime_config(path),
                {"model": "/models/local.gguf", "llama_server": "/bin/llama-server"},
            )

    def test_runtime_validation_needs_a_model_and_executable_server(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            model = root / "model.gguf"
            server = root / "llama-server"
            model.touch()
            server.touch()
            self.assertFalse(desktop_launcher.runtime_is_valid(model, server))
            server.chmod(0o755)
            self.assertTrue(desktop_launcher.runtime_is_valid(model, server))


class DebianPackagingTests(unittest.TestCase):
    def test_package_name_uses_an_architecture_independent_debian_suffix(self):
        self.assertEqual(build_deb.package_name("2.3.4"), "offline-reader_2.3.4_all.deb")

    def test_control_file_declares_runtime_dependencies_and_excludes_model_claims(self):
        control = build_deb.control_file("Reader Team <reader@example.com>", "2.3.4")
        self.assertIn("Architecture: all", control)
        self.assertIn("python3-pymupdf", control)
        self.assertIn("not included in this package", control)


if __name__ == "__main__":
    unittest.main()
