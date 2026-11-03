#!/usr/bin/env python3
"""Build a local Debian/Linux Mint desktop package for Offline Reader.

The package contains the application, menu entry, and first-run setup dialog.
It intentionally excludes model weights and llama.cpp: users point the setup
dialog at their existing local model and llama-server executable.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def version() -> str:
    return (ROOT / "VERSION").read_text(encoding="utf-8").strip()


def package_name(release_version: str) -> str:
    return f"offline-reader_{release_version}_all.deb"


def control_file(maintainer: str, release_version: str) -> str:
    return "\n".join((
        "Package: offline-reader",
        f"Version: {release_version}",
        "Section: education",
        "Priority: optional",
        "Architecture: all",
        "Depends: python3 (>= 3.10), python3-tk, python3-flask, python3-requests, python3-pymupdf",
        f"Maintainer: {maintainer}",
        "Description: Privacy-first local document reader and study workspace",
        " Offline Reader opens TXT and PDF documents, keeping notes and study tools local.",
        " A compatible GGUF model and llama.cpp llama-server are selected on first launch",
        " and are not included in this package.",
        "",
    ))


def install_tree(staging: Path) -> None:
    from scripts.package_source_release import collect_files

    application = staging / "opt" / "offline-reader"
    output = staging / "ignore.deb"
    files, _ = collect_files(ROOT, output)
    for source in files:
        destination = application / source.relative_to(ROOT)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def write_launcher(staging: Path) -> None:
    launcher = staging / "usr" / "bin" / "offline-reader"
    launcher.parent.mkdir(parents=True, exist_ok=True)
    launcher.write_text(
        "#!/bin/sh\nexec /usr/bin/python3 /opt/offline-reader/scripts/desktop_launcher.py \"$@\"\n",
        encoding="utf-8",
    )
    launcher.chmod(0o755)

    desktop = staging / "usr" / "share" / "applications" / "offline-reader.desktop"
    desktop.parent.mkdir(parents=True, exist_ok=True)
    desktop.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Offline Reader\n"
        "Comment=Read and study local documents privately\n"
        "Exec=offline-reader\n"
        "Icon=offline-reader\n"
        "Terminal=false\n"
        "Categories=Office;Education;Utility;\n"
        "Keywords=reader;pdf;notes;study;offline;\n",
        encoding="utf-8",
    )

    icon_source = ROOT / "packaging" / "linux" / "offline-reader.svg"
    icon = staging / "usr" / "share" / "icons" / "hicolor" / "scalable" / "apps" / "offline-reader.svg"
    icon.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(icon_source, icon)

    docs = staging / "usr" / "share" / "doc" / "offline-reader"
    docs.mkdir(parents=True, exist_ok=True)
    (docs / "copyright").write_text(
        "Offline Reader\n\n"
        "Copyright (C) 2026 Ephraim-I\n\n"
        "Offline Reader is licensed under the GNU Affero General Public License, "
        "version 3 only. See /opt/offline-reader/LICENSE for the complete terms.\n",
        encoding="utf-8",
    )


def build(output: Path, maintainer: str) -> Path:
    if not shutil.which("dpkg-deb"):
        raise RuntimeError("dpkg-deb is required. Build this package on Debian, Ubuntu, or Linux Mint.")
    release_version = version()
    with tempfile.TemporaryDirectory(prefix="offline-reader-deb-") as temporary:
        staging = Path(temporary) / "offline-reader"
        debian = staging / "DEBIAN"
        debian.mkdir(parents=True)
        (debian / "control").write_text(control_file(maintainer, release_version), encoding="utf-8")
        install_tree(staging)
        write_launcher(staging)
        output.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["dpkg-deb", "--build", "--root-owner-group", str(staging), str(output)], check=True)
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--maintainer", required=True, help="Debian package maintainer, for example 'Name <email@example.com>'")
    parser.add_argument("--output", type=Path, help="Package path (defaults to dist/offline-reader_VERSION_all.deb)")
    args = parser.parse_args()
    output = (args.output or ROOT / "dist" / package_name(version())).expanduser().resolve()
    package = build(output, args.maintainer)
    print(f"Created: {package} ({package.stat().st_size} bytes)")
    print("The package excludes llama.cpp and GGUF models; first launch asks for their local paths.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
