# Local deployment checklist

## Supported runtime

- Linux desktop with Tk installed
- Python 3.10 or newer
- A locally built `llama-server` and a GGUF model

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m unittest discover -s tests -v
```

Run tests from the repository root with the virtual environment active. The full suite imports Flask in `tests/test_services.py`, so a missing-Flask import error means dependencies were not installed in that environment. Resolve it with `python -m pip install -r requirements.txt` and rerun the suite; do not treat a partial run as a passing release check.

## Start

```bash
python run_reader.py
```

`run_reader.py` starts missing local services, opens the reader, and stops services it created after the reader exits. It does not stop an already-running llama.cpp or reader API.

## User data and backups

Reader preferences and recent-file state are stored in
`~/.config/offline-reader/settings.json`. Notes are stored separately in
`$XDG_DATA_HOME/offline-reader/notes.sqlite3`, or
`~/.local/share/offline-reader/notes.sqlite3` when `XDG_DATA_HOME` is unset.
The notes database contains note text, collections, tags, highlights, bookmarks,
quizzes, flashcard decks and review progress, submitted quiz answers, and source
locators; it does not copy the original documents into the application data directory.

To back up notes, close the application and copy `notes.sqlite3` to a protected
backup location. Restore by closing the app and replacing the database at the
same path. Keep source documents separately: a note link is only opened when the
saved path exists and its SHA-256 fingerprint still matches. There is no automatic
recovery for moved or modified documents yet.

## Configuration

Set these environment variables before starting when defaults do not match your computer:

- `OFFLINE_READER_MODEL`
- `OFFLINE_READER_LLAMA_SERVER`
- `OFFLINE_READER_API_PORT`
- `OFFLINE_READER_LLM_PORT`
- `OFFLINE_READER_API_TIMEOUT`
- `OFFLINE_READER_LLM_TIMEOUT`
- `OFFLINE_READER_MAX_TOKENS`
- `OFFLINE_READER_EMBEDDING_MODEL` (optional; local embedding-capable GGUF path)
- `OFFLINE_READER_EMBEDDING_HOST` (optional; defaults to `127.0.0.1`)
- `OFFLINE_READER_EMBEDDING_PORT` (optional; defaults to `8081`)
- `OFFLINE_READER_EMBEDDING_POOLING` (optional; defaults to `mean`)
- `OFFLINE_READER_OCR_ENABLED` (optional; defaults to `true`)
- `OFFLINE_READER_OCR_LANGUAGE` (optional; defaults to `eng`)
- `OFFLINE_READER_OCR_DPI` (optional; defaults to `180`)

When an embedding model is configured, `run_reader.py` starts a second
llama.cpp server with embedding mode enabled. The model is not downloaded by
the application. Confirm it is an embedding-capable GGUF supported by the
installed llama.cpp build and use the pooling mode expected by that model.
The embeddings server binds to loopback by default; if its host is overridden,
queries and passage text are sent to that configured endpoint. If semantic
indexing or querying fails, the application logs the error and continues with
lexical search.

Scanned-PDF OCR uses the system Tesseract executable and its locally installed
language data. Install Tesseract separately using the operating system package
manager; no OCR model or language pack is downloaded by the app. OCR runs only
when a page has little extractable text and can be disabled with
`OFFLINE_READER_OCR_ENABLED=false`.

## Release checks

1. Run `python -m unittest discover -s tests -v` and require no failures or import errors.
2. Verify a TXT document and a text-based PDF, including selection and follow-up questions.
3. Verify the unavailable-model message by stopping llama.cpp, then restart it and confirm recovery.
4. Switch documents and close/replace a selection popup while an explanation is pending; confirm stale results do not overwrite the current popup.
5. Verify a scanned PDF with Tesseract installed: search should index OCR text and opening a result should attempt an OCR-based highlight. Also verify the documented warning/fallback behavior with Tesseract unavailable or OCR disabled.
6. Confirm shutdown only terminates processes started by `run_reader.py`, not services that were already running before launch.
7. Check licensing before redistribution: PyMuPDF is AGPL/commercial, and the model plus llama.cpp have their own terms. Also review licenses for every model and bundled binary.
8. Confirm the source archive omits `archive/prototypes/`; these historical files are not part of the supported runtime.
9. Do not describe the build as release-ready until both automated checks and the manual GUI/model smoke test have been completed on the target environment.
10. Verify notes from TXT and PDF selections, edit/save, tag or content search, and ensure missing or changed source files do not trigger a passage jump.
11. Verify that TXT character-range highlights and PDF page/rectangle highlights persist after reopening, bookmarks navigate to the correct page or passage, and changed source files do not receive stale highlights.
12. Verify revision notes from a selected passage, including assistant-unavailable handling, editing before save, and the persisted source link. Confirm that selection-scoped output is not presented as a chapter summary.
13. Generate quizzes from selected TXT and PDF passages with each question style and difficulty. Confirm malformed model output is rejected, answers remain hidden until submission, submitted answers survive closing/reopening a quiz, and changed source files do not navigate.
14. Generate flashcards from selected TXT and PDF passages. Confirm malformed output is rejected, the back remains hidden until reveal, review ratings persist with their due dates, Again cards return after ten minutes, and future cards are excluded from the due queue.
15. Start **Review due flashcards** with cards in multiple decks. Confirm it includes only due cards, updates the current deck/source as the session crosses decks, removes rated cards until due again, and shows a clear empty state when no cards are due.

16. With `OFFLINE_READER_EMBEDDING_MODEL` configured, search for a passage using related wording that does not occur in the passage and confirm the local embeddings server returns it with the correct source location. Stop or misconfigure that server and confirm the warning is logged and lexical search remains available.
17. Verify that a TXT heading and a larger-font PDF heading appear in search results and that a query matching a section heading finds passages within that section.

## Health checks

The local reader API responds at `http://127.0.0.1:5000/health` by default. The model server responds at `http://127.0.0.1:8080/health` by default. These endpoints establish reachability only; test an actual explanation through the GUI to verify end-to-end inference. Override default hosts/ports using the `OFFLINE_READER_*` variables documented above.

```bash
python -c "from urllib.request import urlopen; print(urlopen('http://127.0.0.1:5000/health', timeout=2).read().decode())"
python -c "from urllib.request import urlopen; print(urlopen('http://127.0.0.1:8080/health', timeout=2).read().decode())"
```

## Reading mode controls

- `F11`: toggle full-screen.
- `Ctrl+Shift+F`: toggle distraction-free focus mode.
- `Escape`: exit focus mode, then full-screen, then close a contextual popup.
- `Ctrl+Plus` / `Ctrl+Minus`: change reading size or PDF zoom.
- `Ctrl+0`: reset text size or fit the PDF to width.
- `Line spacing`: cycle compact, comfortable, and spacious TXT spacing.

These are UI-only additions and do not require new Python dependencies or changes to the local AI service.
