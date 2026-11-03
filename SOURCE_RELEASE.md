# Creating a portable source release

From the project root, run:

```bash
python scripts/package_source_release.py --dry-run
python scripts/package_source_release.py
```

The default output is `dist/offline-reader-source.zip`. The archive has a top-level project directory and is intended to be extracted before following `INSTALL_LINUX_MINT.md`.

## What the packager excludes

The script excludes common virtual environments, VCS metadata, Python caches/bytecode, build outputs, logs, local environment files, interactive history files such as `.Rhistory`, credentials/key-like files, databases, backup files, model weights (`.gguf`, `.safetensors`, `.pt`, `.pth`, `.onnx`), the `llama.cpp/` directory, and `archive/` (historical prototypes and patch notes). The inference engine and model should be installed separately according to the setup guide, which avoids creating a huge archive and reduces accidental disclosure of local files.

## Review before sharing

Always inspect the included-file list from `--dry-run`, then inspect the ZIP contents after creation. The packager is a conservative denylist, not a complete secret detector: unusual credential names, personal content stored under ordinary filenames, or new model formats may not be recognized automatically. Add exclusions when new local-only paths are introduced. Do not include user documents, diaries, API tokens, or private keys in a source release.

To choose a different output path:

```bash
python scripts/package_source_release.py --output /tmp/offline-reader-source.zip
```

To package a different project directory:

```bash
python scripts/package_source_release.py --root /path/to/os_root --dry-run
```

This archive contains source code, not a self-contained executable. Python dependencies, system packages, `llama.cpp`, and model weights remain separate prerequisites.

## Test the packager

Run the regression tests from the project root:

```bash
python -m unittest tests.test_source_release_packager -v
```

The tests use temporary fixture projects and cover sensitive/generated-file exclusions, preservation of required source files, symlink omission, output-path handling, missing required files, dry-run behavior, and ZIP integrity. They do not prove that every possible secret filename or model format is recognized, so the dry-run and archive review remain required.

## Versioned artifacts and provenance

The canonical release version is stored in `VERSION` (currently `0.1.0`). For a versioned CI build, create a Git tag whose name is `v` followed by that exact version, for example:

```bash
git tag v0.1.0
git push origin v0.1.0
```

Before pushing a tag, update `VERSION` and commit that change. CI rejects a `v*` tag that does not match `VERSION`. The workflow does not automatically publish a GitHub Release; it uploads the source archive and its verification files as a workflow artifact after checks pass.

Each successful workflow build provides three files:

- `offline-reader-source-VERSION.zip`: the source archive.
- `offline-reader-source-VERSION.zip.sha256`: a SHA-256 checksum sidecar.
- `offline-reader-source-VERSION.zip.provenance.json`: the version, source commit SHA, Git ref, archive filename, checksum, and UTC build time.

To verify the archive after downloading all three files, run this from the directory containing them:

```bash
sha256sum --check offline-reader-source-0.1.0.zip.sha256
```

Then inspect the matching `.provenance.json` file and confirm its `version`, `commit`, and `ref` correspond to the intended build. A checksum detects accidental corruption or mismatch; by itself it does not prove who produced the archive. For stronger trust, obtain the files directly from the corresponding GitHub Actions run and compare the commit to the repository history.

The provenance generator can also be run manually after packaging:

```bash
python scripts/create_release_provenance.py \
  --archive dist/offline-reader-source-0.1.0.zip \
  --version-file VERSION \
  --commit "$(git rev-parse HEAD)" \
  --ref "$(git describe --tags --always)" \
  --output-dir dist
```

Run its focused tests with:

```bash
python -m unittest tests.test_release_provenance -v
```


## Verify GitHub's signed build attestation

The CI workflow uses GitHub's OIDC identity and the `actions/attest-build-provenance` action to create a signed provenance attestation for the exact source ZIP after its contents and checksum have been checked. No long-lived signing key is stored in repository secrets. The workflow requires the narrowly scoped `id-token: write` and `attestations: write` permissions in addition to `contents: read`.

After downloading the archive from the corresponding GitHub Actions run, verify its checksum first:

```bash
sha256sum --check offline-reader-source-0.1.0.zip.sha256
```

Then, with a recent GitHub CLI (`gh`) installed and authenticated, verify the attestation against the expected repository:

```bash
gh attestation verify offline-reader-source-0.1.0.zip --repo OWNER/REPOSITORY
```

Replace `OWNER/REPOSITORY` with the actual GitHub repository. The command should report a valid attestation whose source repository and workflow identity match the project you trust. Do not treat a valid checksum alone as proof of publisher identity. Attestation creation and verification must be confirmed against the actual GitHub-hosted run; they cannot be fully reproduced in a local checkout.
