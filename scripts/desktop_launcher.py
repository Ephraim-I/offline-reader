#!/usr/bin/env python3
"""Desktop entry point with a one-time local runtime setup dialog.

The distributable Debian package deliberately excludes llama.cpp and GGUF model
files. They are large, platform-specific, and have independent licences. This
launcher records the user's local paths before starting the normal stack.
"""
from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def runtime_config_path() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "offline-reader" / "runtime.json"


def load_runtime_config(path: Path | None = None) -> dict[str, str]:
    path = path or runtime_config_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        key: value for key, value in data.items()
        if key in {"model", "llama_server"} and isinstance(value, str) and value.strip()
    }


def save_runtime_config(config: dict[str, str], path: Path | None = None) -> None:
    path = path or runtime_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.parent.chmod(0o700)
    except OSError:
        pass
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


def resolve_runtime(config: dict[str, str] | None = None) -> tuple[Path, Path]:
    config = config or load_runtime_config()
    model = Path(os.environ.get("OFFLINE_READER_MODEL", config.get("model", ""))).expanduser()
    server = Path(os.environ.get("OFFLINE_READER_LLAMA_SERVER", config.get("llama_server", ""))).expanduser()
    return model, server


def runtime_is_valid(model: Path, server: Path) -> bool:
    return model.is_file() and server.is_file() and os.access(server, os.X_OK)


def request_runtime_setup(model: Path, server: Path) -> tuple[Path, Path] | None:
    """Collect local paths without requiring a terminal on first desktop launch."""
    import tkinter as tk
    from tkinter import filedialog, messagebox

    root = tk.Tk()
    root.title("Set up Offline Reader")
    root.configure(bg="#f5f6f8")
    root.geometry("680x260")
    root.minsize(580, 240)

    model_value = tk.StringVar(value=str(model) if str(model) != "." else "")
    server_value = tk.StringVar(value=str(server) if str(server) != "." else "")
    result: dict[str, Path] = {}

    shell = tk.Frame(root, bg="#ffffff", padx=24, pady=22)
    shell.pack(fill="both", expand=True, padx=18, pady=18)
    tk.Label(
        shell, text="Finish Offline Reader setup", bg="#ffffff", fg="#20252b",
        font=("Segoe UI", 14, "bold"), anchor="w",
    ).pack(fill="x")
    tk.Label(
        shell,
        text="Choose your local GGUF model and the llama.cpp llama-server executable. "
             "These paths stay on this computer.",
        bg="#ffffff", fg="#68717c", justify="left", anchor="w", wraplength=590,
        font=("Segoe UI", 9),
    ).pack(fill="x", pady=(5, 16))

    def chooser(label: str, value: tk.StringVar, choose_file):
        row = tk.Frame(shell, bg="#ffffff")
        row.pack(fill="x", pady=(0, 10))
        tk.Label(row, text=label, width=18, anchor="w", bg="#ffffff", fg="#252b32", font=("Segoe UI", 9, "bold")).pack(side="left")
        tk.Entry(row, textvariable=value, bg="#ffffff", fg="#252b32", relief="solid", borderwidth=1).pack(side="left", fill="x", expand=True, ipady=4)
        tk.Button(row, text="Browse…", command=choose_file, relief="flat", padx=10, pady=5).pack(side="left", padx=(8, 0))

    def choose_model():
        value = filedialog.askopenfilename(parent=root, title="Choose GGUF model", filetypes=[("GGUF models", "*.gguf"), ("All files", "*")])
        if value:
            model_value.set(value)

    def choose_server():
        value = filedialog.askopenfilename(parent=root, title="Choose llama-server executable")
        if value:
            server_value.set(value)

    chooser("Local GGUF model", model_value, choose_model)
    chooser("llama-server", server_value, choose_server)

    buttons = tk.Frame(shell, bg="#ffffff")
    buttons.pack(fill="x", pady=(4, 0))

    def save_and_start():
        chosen_model = Path(model_value.get()).expanduser()
        chosen_server = Path(server_value.get()).expanduser()
        if not runtime_is_valid(chosen_model, chosen_server):
            messagebox.showerror(
                "Check the selected files",
                "Choose an existing .gguf model and an executable llama-server file.",
                parent=root,
            )
            return
        result["model"] = chosen_model
        result["server"] = chosen_server
        root.destroy()

    tk.Button(buttons, text="Cancel", command=root.destroy, relief="flat", padx=12, pady=6).pack(side="right")
    tk.Button(buttons, text="Save and start", command=save_and_start, relief="flat", bg="#b9e4d7", padx=12, pady=6).pack(side="right", padx=(0, 8))
    root.mainloop()
    if not result:
        return None
    return result["model"], result["server"]


def main() -> int:
    model, server = resolve_runtime()
    if not runtime_is_valid(model, server):
        chosen = request_runtime_setup(model, server)
        if chosen is None:
            return 2
        model, server = chosen
        save_runtime_config({"model": str(model), "llama_server": str(server)})
    os.environ["OFFLINE_READER_MODEL"] = str(model)
    os.environ["OFFLINE_READER_LLAMA_SERVER"] = str(server)

    # Import only after environment setup: app_config reads these variables at
    # module import time.
    from run_reader import main as run_reader
    return run_reader()


if __name__ == "__main__":
    raise SystemExit(main())
