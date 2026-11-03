#!/usr/bin/env python3
"""Run repeatable, local release checks for Offline Reader.

This script checks project hygiene, Python syntax, required Python dependencies,
and (unless --skip-tests is supplied) the unittest suite. It does not launch the
Tkinter GUI, start llama.cpp, or verify live model inference.
"""

from __future__ import annotations

import argparse
import importlib.util
import fnmatch
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_FILES = (
    "README.md",
    "DEPLOYMENT.md",
    "requirements.txt",
    "run_reader.py",
    "app/main.py",
    "services/llm_service.py",
    "tests",
)
DEPENDENCY_MODULES = {
    "Flask": "flask",
    "PyMuPDF": "fitz",
    "requests": "requests",
}
FORBIDDEN_RELEASE_PATTERNS = (
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "*.gguf",
    "*.sqlite",
    "*.sqlite3",
    "*.db",
)
IGNORED_DIRS = {
    ".git", ".venv", "venv", "__pycache__", ".pytest_cache", "build", "dist",
    "archive", "llama.cpp", "models",
}


def report(label: str, ok: bool, details: str = "") -> bool:
    status = "PASS" if ok else "FAIL"
    suffix = f": {details}" if details else ""
    print(f"[{status}] {label}{suffix}")
    return ok


def project_files():
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(ROOT)
        if any(part in IGNORED_DIRS for part in relative.parts):
            continue
        yield path, relative


def check_required_files() -> bool:
    missing = [name for name in REQUIRED_FILES if not (ROOT / name).exists()]
    return report("Required project files", not missing, "missing: " + ", ".join(missing) if missing else "all present")


def check_dependencies() -> bool:
    missing = [package for package, module in DEPENDENCY_MODULES.items() if importlib.util.find_spec(module) is None]
    return report("Python dependencies", not missing, "missing modules: " + ", ".join(missing) if missing else "Flask, PyMuPDF, requests available")


def check_syntax() -> bool:
    source_files = [path for path, _ in project_files() if path.suffix == ".py"]
    failures = []
    for path in source_files:
        try:
            compile(path.read_bytes(), str(path), "exec")
        except (SyntaxError, UnicodeDecodeError, OSError) as exc:
            failures.append(f"{path.relative_to(ROOT)} ({exc})")
    return report("Python syntax", not failures, f"checked {len(source_files)} files" + ("; " + "; ".join(failures) if failures else ""))


def check_release_hygiene() -> bool:
    matches = []
    for _, relative in project_files():
        for pattern in FORBIDDEN_RELEASE_PATTERNS:
            if fnmatch.fnmatch(relative.name, pattern):
                # .env.example is an intentional template, not a secret file.
                if relative.as_posix() == ".env.example":
                    continue
                matches.append(relative.as_posix())
                break
    return report(
        "Release archive hygiene",
        not matches,
        "potentially private/large files found: " + ", ".join(sorted(matches)) if matches else "no common secret, database, model, or key files found",
    )


def run_tests() -> bool:
    print("\nRunning: python -m unittest discover -s tests -v\n", flush=True)
    completed = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
        cwd=ROOT,
        check=False,
    )
    return report("Unittest suite", completed.returncode == 0, f"exit code {completed.returncode}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-tests", action="store_true", help="run static checks only")
    args = parser.parse_args()

    print(f"Offline Reader release check: {ROOT}\n")
    results = [check_required_files(), check_dependencies(), check_syntax(), check_release_hygiene()]
    if not args.skip_tests:
        results.append(run_tests())
    print("\nSummary: " + ("all checks passed" if all(results) else "one or more checks failed"))
    if args.skip_tests:
        print("Note: tests were intentionally skipped; this is not a full release verification.")
    print("Note: GUI interaction, llama.cpp startup, and real model inference require manual smoke testing.")
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
