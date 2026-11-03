"""Runtime settings for the local Offline Reader stack."""

import os
from pathlib import Path


ROOT = Path(__file__).resolve().parent
API_HOST = os.getenv("OFFLINE_READER_API_HOST", "127.0.0.1")
API_PORT = int(os.getenv("OFFLINE_READER_API_PORT", "5000"))
LLM_HOST = os.getenv("OFFLINE_READER_LLM_HOST", "127.0.0.1")
LLM_PORT = int(os.getenv("OFFLINE_READER_LLM_PORT", "8080"))
API_REQUEST_TIMEOUT_SECONDS = float(os.getenv("OFFLINE_READER_API_TIMEOUT", "25"))
LLM_REQUEST_TIMEOUT_SECONDS = float(os.getenv("OFFLINE_READER_LLM_TIMEOUT", "20"))
MAX_COMPLETION_TOKENS = int(os.getenv("OFFLINE_READER_MAX_TOKENS", "256"))
EXPLANATION_CACHE_SIZE = int(os.getenv("OFFLINE_READER_CACHE_SIZE", "256"))
MODEL_PATH = Path(os.getenv("OFFLINE_READER_MODEL", ROOT / "models" / "tinyllama.gguf"))
LLAMA_SERVER_PATH = Path(os.getenv("OFFLINE_READER_LLAMA_SERVER", ROOT / "llama.cpp" / "build" / "bin" / "llama-server"))
_embedding_model = os.getenv("OFFLINE_READER_EMBEDDING_MODEL", "").strip()
EMBEDDING_MODEL_PATH = Path(_embedding_model) if _embedding_model else None
EMBEDDING_HOST = os.getenv("OFFLINE_READER_EMBEDDING_HOST", "127.0.0.1")
EMBEDDING_PORT = int(os.getenv("OFFLINE_READER_EMBEDDING_PORT", "8081"))
EMBEDDING_POOLING = os.getenv("OFFLINE_READER_EMBEDDING_POOLING", "mean")
OCR_ENABLED = os.getenv("OFFLINE_READER_OCR_ENABLED", "true").strip().casefold() not in {
    "0", "false", "no", "off",
}
OCR_LANGUAGE = os.getenv("OFFLINE_READER_OCR_LANGUAGE", "eng")
OCR_DPI = int(os.getenv("OFFLINE_READER_OCR_DPI", "180"))
API_BASE_URL = f"http://{API_HOST}:{API_PORT}"
LLM_COMPLETIONS_URL = f"http://{LLM_HOST}:{LLM_PORT}/v1/completions"
EMBEDDINGS_URL = f"http://{EMBEDDING_HOST}:{EMBEDDING_PORT}/v1/embeddings"
