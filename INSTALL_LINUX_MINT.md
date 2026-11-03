# Install and run Offline Reader on Linux Mint

This guide installs the Python application environment and connects it to a local
`llama.cpp` server. It does **not** bundle or download model weights, and it does
not build `llama.cpp` for you. The application is offline-first once its required
files are installed, but the first dependency installation normally needs internet
access unless you already have a local package cache.

## 1. Install system packages

On Linux Mint, install Python, virtual-environment support, Tkinter, and build tools:

```bash
sudo apt update
sudo apt install python3 python3-venv python3-pip python3-tk build-essential cmake git
```

The helper requires Python 3.10 or newer. If your distribution's Python and
`python3-venv` packages do not match, install the matching package for your Python
version before continuing.

## 2. Create the Python environment

From the project root, run:

```bash
./scripts/install_linux_mint.sh
```

This creates `.venv/`, installs the pinned packages in `requirements.txt`, and
checks imports for Flask, PyMuPDF, and requests. It does not use `sudo pip` and does
not modify the system Python environment.

## 3. Build or locate `llama-server`

The default executable path expected by this project is:

```text
llama.cpp/build/bin/llama-server
```

If you have not built `llama.cpp`, follow the upstream project's current build
instructions in its official repository: <https://github.com/ggml-org/llama.cpp>.
The exact build options depend on your hardware and the upstream version. After
building, verify the executable exists and can run:

```bash
./llama.cpp/build/bin/llama-server --help
```

If your executable is elsewhere, set `OFFLINE_READER_LLAMA_SERVER` to its absolute
path when launching the app.

## 4. Place a compatible model outside release archives

Use a local GGUF model compatible with the version of `llama.cpp` you built. The
default model path is:

```text
models/tinyllama.gguf
```

You can keep your model elsewhere and set an absolute path instead. For example:

```bash
export OFFLINE_READER_MODEL="$HOME/llm-models/your-model.gguf"
export OFFLINE_READER_LLAMA_SERVER="$HOME/src/llama.cpp/build/bin/llama-server"
```

Choose a model small enough for your available RAM and CPU, and review its license
and use restrictions yourself. This project does not download model weights.

## 5. Launch the application

With the environment variables set in the same terminal:

```bash
./scripts/launch_reader.sh
```

The launcher starts the local model server and explanation API when they are not
already available, then opens the Tkinter reader. By default, the API binds to
`127.0.0.1:5000` and the model server to `127.0.0.1:8080`. Keep these services on
loopback unless you intentionally understand and configure the security implications
of exposing them to a network.

To use non-default ports, set `OFFLINE_READER_API_PORT` and
`OFFLINE_READER_LLM_PORT` before launching. Other runtime settings are documented
in `.env.example`; that file is a reference, not an automatically loaded `.env`
file. Export variables in your shell before running the launcher.

## 6. Verify and troubleshoot

- **Model file not found:** check `OFFLINE_READER_MODEL` and confirm it points to a
  readable GGUF file.
- **`llama-server` missing:** check `OFFLINE_READER_LLAMA_SERVER`, executable
  permissions, and whether the build completed successfully.
- **Virtual environment missing:** rerun `./scripts/install_linux_mint.sh`.
- **Tkinter import error:** install the system package `python3-tk` matching your
  Python version.
- **API reports that the model is unavailable:** try running the `llama-server`
  command directly and inspect its terminal output. Confirm the configured port is
  free and that `/health` responds on the configured host and port.
- **Slow or failed answers:** smaller quantized models generally use less memory;
  the chosen model's performance depends on available RAM, CPU, quantization, and
  context size.

The launcher only terminates child processes it started itself. If an API or model
server was already running, it is treated as externally managed and is not stopped
by the reader launcher.

## 7. Before sharing a release

Do not include `.venv/`, model weights (`*.gguf`), personal documents, logs, caches,
`.env` files, or compiled `llama.cpp` build outputs in a source ZIP. Confirm that
licenses for Python dependencies, the model, and `llama.cpp` are compatible with
your intended distribution. The scripts here are convenience helpers, not a signed
installer or a complete license audit.
