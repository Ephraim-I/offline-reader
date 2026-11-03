#!/usr/bin/env python3
"""Create a source-release ZIP while excluding local data and build artifacts."""
from __future__ import annotations

import argparse
import fnmatch
import sys
import zipfile
from pathlib import Path

EXCLUDED_DIRS = {
    ".git", ".venv", "venv", "env", "__pycache__", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", "node_modules", "dist", "build",
    "target", "llama.cpp", "archive",  # Native inference and historical material are separate.
}
EXCLUDED_NAMES = {
    ".env", ".env.local", ".env.production", "id_rsa", "id_ed25519",
    "known_hosts", "secrets.json", "credentials.json", "diary.db", ".Rhistory",
}
EXCLUDED_SUFFIXES = {".pyc", ".pyo", ".gguf", ".safetensors", ".pt", ".pth", ".onnx"}
EXCLUDED_PATTERNS = (
    "*.log", "*.sqlite", "*.sqlite3", "*.db", "*.pem", "*.key",
    "*.p12", "*.pfx", "*.token", "*.secret", "*.swp", "*~",
)


def is_excluded(path: Path, root: Path) -> str | None:
    rel = path.relative_to(root)
    parts = rel.parts
    if any(part in EXCLUDED_DIRS for part in parts[:-1]):
        return "excluded directory"
    name = path.name
    if name in EXCLUDED_NAMES:
        return "sensitive/local file"
    if path.suffix.lower() in EXCLUDED_SUFFIXES:
        return "model/binary artifact"
    if any(fnmatch.fnmatch(name, pattern) for pattern in EXCLUDED_PATTERNS):
        return "generated/sensitive file pattern"
    # Avoid shipping files that are commonly local-only even if not named .env.
    if name.startswith(".env.") or name.endswith((".bak", ".backup")):
        return "local configuration/backup"
    return None


def collect_files(root: Path, output: Path) -> tuple[list[Path], list[tuple[str, str]]]:
    included: list[Path] = []
    excluded: list[tuple[str, str]] = []
    for path in sorted(root.rglob("*")):
        # Do not follow symlinks: they can point outside the project and leak local data.
        if path.is_symlink() or not path.is_file() or path == output:
            continue
        reason = is_excluded(path, root)
        rel = path.relative_to(root).as_posix()
        if reason:
            excluded.append((rel, reason))
        else:
            included.append(path)
    return included, excluded


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1], help="project root (defaults to parent of scripts/)")
    parser.add_argument("--output", type=Path, help="output ZIP (defaults to ROOT/dist/offline-reader-source.zip)")
    parser.add_argument("--dry-run", action="store_true", help="show included/excluded files without creating the archive")
    args = parser.parse_args()

    root = args.root.expanduser().resolve()
    if not root.is_dir():
        parser.error(f"project root does not exist: {root}")
    output = (args.output or (root / "dist" / "offline-reader-source.zip")).expanduser().resolve()
    try:
        output.relative_to(root)
    except ValueError:
        pass
    else:
        # Output is excluded by directory rule, but explicitly skip it regardless.
        output.parent.mkdir(parents=True, exist_ok=True) if not args.dry_run else None

    files, excluded = collect_files(root, output)
    required = {"README.md", "requirements.txt", "run_reader.py"}
    present = {p.relative_to(root).as_posix() for p in files}
    missing = sorted(required - present)
    if missing:
        print("ERROR: required release files missing or excluded: " + ", ".join(missing), file=sys.stderr)
        return 2

    print(f"Source root: {root}")
    print(f"Included files ({len(files)}):")
    for path in files:
        print("  + " + path.relative_to(root).as_posix())
    print(f"Excluded files ({len(excluded)}):")
    for rel, reason in excluded:
        print(f"  - {rel} [{reason}]")
    if args.dry_run:
        print("Dry run only; no archive created.")
        return 0

    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=8) as archive:
        for path in files:
            archive.write(path, Path(root.name) / path.relative_to(root))
    print(f"Created: {output} ({output.stat().st_size} bytes)")
    print("Reminder: review the included-file list before sharing. This is a denylist-based source packager, not a secret scanner.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
