# GUI/UX Foundation Audit

## Scope and baseline

This audit covers the supplied `os_root` working folder. It has no usable Git
metadata, so branch, commit, and pre-existing uncommitted-change state could not
be verified. The version file reports `0.1.0`.

The documented test command completed successfully before this milestone:

```text
python -m unittest discover -s tests -v
129 tests passed
```

The project virtual environment contains the declared GUI, PDF, Flask, and HTTP
dependencies. The local model file and `llama-server` executable are present.
The Tkinter application remained running during a five-second launch smoke test.
This confirms that it starts in this environment; it is not visual usability
validation on a real target laptop.

A temporary local-only full-stack smoke test also started llama.cpp and the
Flask API on loopback ports and completed an explanation request for
"photosynthesis". The servers were stopped after the check. This verifies one
real local-model request path, not model quality, sustained-load behaviour, or
GUI presentation.

## Verified foundations

- The application is a Python/Tkinter desktop reader, not a web application.
- TXT and PDF loading, page navigation, selection, local search, PDF render
  caching, OCR-backed source lookup, and source navigation are active.
- Explanation and follow-up requests use a local Flask service and local
  llama.cpp process, with request lifecycle guards for stale popups.
- Notes, highlights, bookmarks, revision notes, passage-scoped quizzes, and
  flashcards already persist locally in SQLite with source fingerprints.
- Light and dark themes, focus mode, full screen, keyboard navigation, and
  screen-positioned contextual popups already exist.
- Automated tests cover core data, retrieval, AI failure handling, popup
  lifecycle, themes, accessibility source checks, and packaging checks.

## Findings

1. **Constrained windows were the highest GUI risk.** The header and 210 px
   library rail were always packed, although the minimum window width is 760 px.
   This can crowd the reader when tiled or resized.
2. **The codebase is feature-rich but UI-centralized.** `app/ui.py` owns the
   primary reader and study windows. New visual work should remain incremental
   and use focused tests to avoid destabilizing document and AI workflows.
3. **No framework migration is justified.** Tkinter currently supports the
   required local-first desktop reader behaviour. A migration would add risk
   without solving an established blocker.
4. **Manual visual validation remains necessary.** Automated checks cannot
   verify Linux desktop scaling, screen-reader behaviour, popup placement near
   display edges, or the perceived quality of resizing and full-screen modes.

## Completed GUI milestone: adaptive reader chrome

The reader now uses live window width, not monitor resolution, to select a
layout profile:

- **Compact (under 920 px):** collapses the library rail and removes duplicate
  header zoom and line-spacing controls. The View menu and existing keyboard
  shortcuts keep every action available; `Ctrl+B` can reopen the rail.
- **Standard (920–1119 px):** retains the normal reading workspace.
- **Expanded (1120 px and above):** retains the full header and library rail.

Resize events are debounced for 80 ms to avoid repeated layout work and PDF
rerenders while the user drags a window edge. The rail returns on expansion only
when it was automatically collapsed, preserving an explicit user choice to hide
it.

## Completed GUI milestone: calmer workspace shell

The reader shell has been tightened around the primary reading task:

- The header now uses a calmer product title, concise icon controls with
  tooltips, and a single prominent **Open document** action instead of a row of
  competing text buttons.
- The navigation rail is wider and split into **Workspace**, **Study**, and
  **Recent documents** sections. Long labels such as highlights/bookmarks are
  represented by short, complete action names, while long recent filenames are
  shortened only in the rail (their full paths remain available in File).
- The empty reader state is now a card on the workspace background. The large
  outlined text column is not shown until a TXT document is opened.
- Text reading no longer forces a 420 px column and 42 px side gutters in a
  constrained window. The column can contract to 280 px and its gutter reduces
  to preserve actual text space; wide windows retain a bounded, readable line
  length.
- The local search control gains its own quiet surface and drops its descriptive
  label in compact mode, preserving more width for the query.

The reference design informed hierarchy, density, and grouped controls only;
the implementation intentionally avoids copying its imagery, branding, or
high-saturation background treatment.

## Completed GUI milestone: stable follow-up popups

Follow-up answers now retain the identity of the popup and selected passage
that started the request. If a learner changes selection, changes document, or
closes/replaces the popup before the local model replies, that stale result is
discarded instead of re-enabling or updating old controls.

Contextual popups are also bounded to the reader window rather than the whole
screen. A popup near the lower edge flips above the selection before it is
clamped, keeping it visually associated with the active reading workspace in a
tiled or restored window.

The action row now prioritizes frequent reading actions. Copy explanation and
bookmarks are grouped under **More**, while revision notes, quizzes, and
flashcards are grouped under a keyboard-focusable **Study** menu. No study or
note workflow was removed.

The focused adaptive-layout and popup-lifecycle tests and the complete suite
pass after these changes. The adaptive checks also verify that the library rail
returns only after an automatic collapse, never against an explicit user choice:

```text
144 tests passed
```

The source-release packager dry run also completes successfully and creates no
archive. It is a file-inclusion check, not a release approval or secret scan.

The documented local release check passes its required-file, dependency,
syntax, archive-hygiene, and 141-test checks. Its own output correctly leaves
real GUI interaction and real model inference to manual smoke testing.

`docs/MANUAL_GUI_QA.md` is the target-desktop checklist for the visual checks
that remain outside automated coverage.

An attempted automated desktop screenshot from this environment could not open
the active display. Consequently, this milestone has syntax and behavioural
test coverage but has not been visually approved from this execution session.

## Next GUI milestones

1. Manually validate compact, standard, maximized, focus, and full-screen modes
   on a Linux desktop at normal and high display scaling using
   `docs/MANUAL_GUI_QA.md`. Adjust breakpoints only from observed clipping or
   uncomfortable line lengths.
2. Improve navigation hierarchy: group study-library actions behind one compact
   entry in constrained mode, while retaining keyboard access and direct recent
   document navigation.
3. Refine contextual popup ergonomics: manually test left/right and top/bottom
   screen edges, long generated responses, and keyboard focus restoration.
   Avoid animation unless it is subtle and does not delay reading actions.
4. Move mature study tools into a coherent, optional workspace panel only after
   the reader shell has passed manual validation. Do not expand notes or AI
   features before then.
5. Run the documented target-machine release checks and manual model smoke test
   before any packaging or production claim.

## Packaging prompt

The requested dedicated packaging-and-deployment prompt is intentionally not
generated yet. It belongs at the end of the agreed GUI optimization and full
development work, after target-machine GUI and model validation are complete.
