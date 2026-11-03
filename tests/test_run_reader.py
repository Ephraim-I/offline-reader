import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import run_reader


class FakeProcess:
    def __init__(self):
        self.terminated = False

    def poll(self):
        return None

    def wait(self, timeout=None):
        return 0

    def terminate(self):
        self.terminated = True


class RunReaderTests(unittest.TestCase):
    def test_configured_embedding_model_starts_and_stops_separate_server(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            completion_model = root / "completion.gguf"
            embedding_model = root / "embedding.gguf"
            llama_server = root / "llama-server"
            for path in (completion_model, embedding_model, llama_server):
                path.touch()

            processes = []

            def fake_popen(args, cwd):
                process = FakeProcess()
                processes.append((args, process))
                return process

            with (
                patch.object(run_reader, "MODEL_PATH", completion_model),
                patch.object(run_reader, "EMBEDDING_MODEL_PATH", embedding_model),
                patch.object(run_reader, "LLAMA_SERVER_PATH", llama_server),
                patch.object(run_reader, "EMBEDDING_HOST", "127.0.0.1"),
                patch.object(run_reader, "EMBEDDING_PORT", 8081),
                patch.object(run_reader, "EMBEDDING_POOLING", "mean"),
                patch.object(run_reader, "is_available", return_value=False),
                patch.object(run_reader, "wait_for"),
                patch("run_reader.subprocess.Popen", side_effect=fake_popen),
            ):
                self.assertEqual(run_reader.main(), 0)

            embedding_args, embedding_process = processes[1]
            self.assertIn("--embedding", embedding_args)
            self.assertIn("--pooling", embedding_args)
            self.assertIn("mean", embedding_args)
            self.assertIn(str(embedding_model), embedding_args)
            self.assertTrue(embedding_process.terminated)
            self.assertEqual(len(processes), 4)


if __name__ == "__main__":
    unittest.main()
