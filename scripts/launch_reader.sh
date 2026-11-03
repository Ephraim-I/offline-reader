#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ ! -x .venv/bin/python ]]; then
  echo "Offline Reader environment not found. Run: ./scripts/install_linux_mint.sh" >&2
  exit 1
fi

export OFFLINE_READER_MODEL="${OFFLINE_READER_MODEL:-$ROOT/models/tinyllama.gguf}"
export OFFLINE_READER_LLAMA_SERVER="${OFFLINE_READER_LLAMA_SERVER:-$ROOT/llama.cpp/build/bin/llama-server}"

if [[ ! -f "$OFFLINE_READER_MODEL" ]]; then
  echo "Model file not found: $OFFLINE_READER_MODEL" >&2
  echo "Set OFFLINE_READER_MODEL to the absolute path of a compatible local GGUF model." >&2
  echo "See INSTALL_LINUX_MINT.md for setup instructions." >&2
  exit 2
fi
if [[ ! -x "$OFFLINE_READER_LLAMA_SERVER" ]]; then
  echo "llama-server is missing or not executable: $OFFLINE_READER_LLAMA_SERVER" >&2
  echo "Set OFFLINE_READER_LLAMA_SERVER to the executable built from llama.cpp." >&2
  echo "See INSTALL_LINUX_MINT.md for setup instructions." >&2
  exit 2
fi

exec .venv/bin/python "$ROOT/run_reader.py"
