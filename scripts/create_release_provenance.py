#!/usr/bin/env python3
"""Create a SHA-256 checksum and provenance manifest for a source release."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", required=True, type=Path, help="source ZIP to attest")
    parser.add_argument("--version-file", type=Path, default=Path("VERSION"))
    parser.add_argument("--commit", required=True, help="full source commit SHA")
    parser.add_argument("--ref", required=True, help="Git ref or tag associated with the build")
    parser.add_argument("--output-dir", type=Path, help="directory for sidecar files (defaults to archive directory)")
    args = parser.parse_args()

    archive = args.archive.expanduser().resolve()
    version_file = args.version_file.expanduser().resolve()
    output_dir = (args.output_dir or archive.parent).expanduser().resolve()
    if not archive.is_file():
        parser.error(f"archive does not exist: {archive}")
    if not version_file.is_file():
        parser.error(f"version file does not exist: {version_file}")
    version = version_file.read_text(encoding="utf-8").strip()
    if not VERSION_RE.fullmatch(version):
        parser.error(f"invalid version in {version_file}: {version!r}; expected semantic version such as 0.1.0")
    if not args.commit.strip():
        parser.error("--commit must not be empty")
    if not args.ref.strip():
        parser.error("--ref must not be empty")

    checksum = sha256_file(archive)
    output_dir.mkdir(parents=True, exist_ok=True)
    checksum_path = output_dir / f"{archive.name}.sha256"
    manifest_path = output_dir / f"{archive.name}.provenance.json"
    checksum_path.write_text(f"{checksum}  {archive.name}\n", encoding="utf-8")
    manifest = {
        "schema_version": 1,
        "version": version,
        "commit": args.commit.strip(),
        "ref": args.ref.strip(),
        "archive": archive.name,
        "sha256": checksum,
        "created_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Version: {version}")
    print(f"Commit: {args.commit.strip()}")
    print(f"Ref: {args.ref.strip()}")
    print(f"SHA-256: {checksum}")
    print(f"Checksum: {checksum_path}")
    print(f"Provenance: {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
