"""Start the local model, API, and reader together; stop child services on exit."""

import subprocess
import sys
import time
import urllib.error
import urllib.request

from app_config import (
    API_BASE_URL,
    EMBEDDING_MODEL_PATH,
    EMBEDDING_POOLING,
    EMBEDDING_PORT,
    EMBEDDING_HOST,
    LLAMA_SERVER_PATH,
    LLM_HOST,
    LLM_PORT,
    MODEL_PATH,
    ROOT,
)


def is_available(url):
    try:
        with urllib.request.urlopen(url, timeout=1):
            return True
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def wait_for(url, label, timeout=45, process=None):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if is_available(url):
            return
        if process is not None and process.poll() is not None:
            raise RuntimeError(f"{label} exited with code {process.returncode}.")
        time.sleep(0.5)
    raise RuntimeError(f"{label} did not start within {timeout} seconds.")


def terminate(process):
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def main():
    if not MODEL_PATH.is_file():
        raise SystemExit(f"Model file not found: {MODEL_PATH}")
    if not LLAMA_SERVER_PATH.is_file():
        raise SystemExit(f"llama-server not found: {LLAMA_SERVER_PATH}")

    model_process = None
    embedding_process = None
    api_process = None
    reader_process = None
    try:
        if not is_available(f"http://{LLM_HOST}:{LLM_PORT}/health"):
            model_process = subprocess.Popen(
                [str(LLAMA_SERVER_PATH), "-m", str(MODEL_PATH), "--host", LLM_HOST, "--port", str(LLM_PORT)],
                cwd=ROOT,
            )
            wait_for(f"http://{LLM_HOST}:{LLM_PORT}/health", "llama.cpp model server", process=model_process)

        if EMBEDDING_MODEL_PATH is not None:
            if not EMBEDDING_MODEL_PATH.is_file():
                raise RuntimeError(f"Embedding model file not found: {EMBEDDING_MODEL_PATH}")
            if not is_available(f"http://{EMBEDDING_HOST}:{EMBEDDING_PORT}/health"):
                embedding_process = subprocess.Popen(
                    [
                        str(LLAMA_SERVER_PATH), "-m", str(EMBEDDING_MODEL_PATH),
                        "--embedding", "--pooling", EMBEDDING_POOLING,
                        "--host", EMBEDDING_HOST, "--port", str(EMBEDDING_PORT),
                    ],
                    cwd=ROOT,
                )
                wait_for(
                    f"http://{EMBEDDING_HOST}:{EMBEDDING_PORT}/health",
                    "llama.cpp embeddings server",
                    process=embedding_process,
                )

        if not is_available(f"{API_BASE_URL}/health"):
            api_process = subprocess.Popen(
                [sys.executable, "-m", "services.llm_service"], cwd=ROOT
            )
            wait_for(f"{API_BASE_URL}/health", "reader API", process=api_process)

        reader_process = subprocess.Popen([sys.executable, "-m", "app.main"], cwd=ROOT)
        return reader_process.wait()
    except (KeyboardInterrupt, RuntimeError) as error:
        if isinstance(error, RuntimeError):
            print(f"Startup failed: {error}", file=sys.stderr)
            return 1
        return 130
    finally:
        terminate(reader_process)
        terminate(api_process)
        terminate(embedding_process)
        terminate(model_process)


if __name__ == "__main__":
    raise SystemExit(main())
