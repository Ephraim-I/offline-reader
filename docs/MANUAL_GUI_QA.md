# Manual GUI Quality Assurance

This checklist covers the visual and desktop-interaction validation that unit
tests cannot establish. Run it on a target Linux laptop or desktop session
before describing a build as visually validated.

## Setup

From the project directory, start the reader with its project environment:

```bash
source .venv/bin/activate
python run_reader.py
```

Use `sample.txt` and at least one text-based PDF. Record the display scale,
window size, document type, and any observed issue for each failed check.

## Adaptive layout and navigation

- At 1120 px or wider, verify the full library rail and reading controls are
  visible and balanced.
- Between 920 px and 1119 px, verify the standard layout remains usable.
- Between 760 px and 919 px, verify the compact layout removes only secondary
  header controls, automatically collapses the library rail, and leaves the
  reading canvas unobstructed.
- Use `Ctrl+B` to reopen the library in compact layout. Explicitly hiding it
  must remain respected after the window is widened again.
- Use `Ctrl+O`, `Ctrl+F`, and `Ctrl+B`; verify each action is reachable from
  both keyboard and visible controls.
- Resize repeatedly while a document is open. The reader must stay responsive
  and retain the current document and reading position.

## Reading and fullscreen

- Check text size, line spacing, and line length with a long TXT document.
- Check PDF fit, zoom, page navigation, and rendering after resizing.
- Enter and leave fullscreen with `F11` and `Ctrl+Shift+F`; then press Escape.
  Confirm the expected exit behavior and preserved document position.
- Tab through header controls, menus, the library, and reading controls.
  Focus must be visible and the controls must be operable without a mouse.

## Contextual popup

- Select text near each edge and corner of the reading area. The popup must
  remain inside the reader window and move above a low selection when needed.
- Verify Copy, Save note, Highlight, the `More` menu, and available `Study`
  actions. Menu choices must be keyboard reachable and return focus sensibly.
- Ask a follow-up, then close the popup or select new text before the answer
  returns. A stale answer must not reopen or overwrite the new popup.
- Check a long response for readable wrapping and scrolling.

## Themes and learner workflows

- Toggle light/dark theme with `Ctrl+Shift+D`; verify contrast in the reader,
  popup, menus, and focused controls.
- Save a note, a highlight, and a bookmark. Then create revision notes, a quiz,
  and flashcards from a selection when their supporting workflow is available.
  Confirm the source document is not modified unexpectedly.

## Pass criteria

Pass only when the checks above work on the target display without clipped
controls, inaccessible focus, popup overflow, lost reading position, or
noticeable interaction stalls. Keep failures as reproducible observations;
they should become the next small repair milestone rather than being treated
as a release sign-off.
