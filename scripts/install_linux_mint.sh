#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if ! command -v python3 >/dev/null 2>&1; then
  echo "Error: python3 is not installed. Install it with: sudo apt install python3 python3-venv python3-pip python3-tk" >&2
  exit 1
fi

PYTHON_VERSION="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
if ! python3 -c 'import sys; raise SystemExit(sys.version_info < (3, 10))'; then
  echo "Error: Python 3.10 or newer is required; found ${PYTHON_VERSION}." >&2
  exit 1
fi

if ! python3 -c 'import tkinter' >/dev/null 2>&1; then
  echo "Warning: tkinter is not available for Python ${PYTHON_VERSION}. On Linux Mint, try: sudo apt install python3-tk" >&2
fi

if [[ ! -f requirements.txt ]]; then
  echo "Error: requirements.txt was not found. Run this script from a valid Offline Reader project copy." >&2
  exit 1
fi

if [[ ! -d .venv ]]; then
  python3 -m venv .venv || {
    echo "Could not create .venv. On Linux Mint, install python3-venv and retry." >&2
    exit 1
  }
fi

.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt

.venv/bin/python - <<'PY'
import importlib
import sys

modules = {"flask": "Flask", "fitz": "PyMuPDF", "requests": "requests"}
failed = []
for module, package in modules.items():
    try:
        importlib.import_module(module)
    except Exception as exc:
        failed.append(f"{package}: {exc}")
if failed:
    print("Dependency verification failed:", file=sys.stderr)
    print("\n".join(f"  - {item}" for item in failed), file=sys.stderr)
    raise SystemExit(1)
print("Python dependencies verified.")
PY

cat <<'MSG'

Installation environment is ready.

Next steps:
  1. Configure OFFLINE_READER_MODEL and OFFLINE_READER_LLAMA_SERVER if you
     do not use the default paths (see INSTALL_LINUX_MINT.md).
  2. Run: ./scripts/launch_reader.sh

This script does not download model weights or build llama.cpp. Keep model
weights outside release archives and use a model whose license permits your use.
MSG
