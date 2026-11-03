# Linux desktop deployment

Offline Reader can be packaged as a Linux Mint/Debian `.deb` application. The
package installs a launcher in the application menu and opens a short setup
dialog on first launch.

## What the package contains

- The Offline Reader Python application and desktop icon.
- A menu entry named **Offline Reader**.
- A first-run dialog that remembers the paths to a local GGUF model and
  llama.cpp `llama-server` executable in `~/.config/offline-reader/runtime.json`.

It deliberately does **not** include a model or llama.cpp. Both are large and
have separate redistribution terms. The selected paths remain local and the
application still binds its services to loopback by default.

## Build

Build on Linux Mint, Ubuntu, or Debian after the automated checks pass:

```bash
python packaging/linux/build_deb.py \
  --maintainer "Your Name <you@example.com>"
```

The output is `dist/offline-reader_0.1.0_all.deb`. Install it locally with:

```bash
sudo apt install ./dist/offline-reader_0.1.0_all.deb
```

Then start **Offline Reader** from the application menu. On the first launch,
choose a compatible `.gguf` model and executable `llama-server` binary.

## Before sharing

This package is a local desktop-installation artifact, not confirmation of a
public release. Before distributing it, complete the manual GUI/model checks,
review the package contents, and verify the redistribution terms of PyMuPDF,
llama.cpp, and every model you plan to bundle or recommend. Offline Reader's
own code is licensed under AGPL-3.0-only; see `LICENSE`.
