"""Regression tests for safe citation rendering in the explanation popup."""
import unittest
from dataclasses import dataclass

from app.popup import (
    ACCENT,
    BACKGROUND,
    BORDER,
    TEXT,
    _popup_bounds,
    _popup_position,
    _study_menu_actions,
    valid_citation_spans,
)


@dataclass(frozen=True)
class Source:
    ref: str
    location: str


class PopupPresentationTests(unittest.TestCase):
    def test_popup_uses_light_high_contrast_palette(self):
        self.assertEqual(BACKGROUND, "#ffffff")
        self.assertEqual(TEXT, "#252b32")
        self.assertEqual(BORDER, "#dfe4e8")
        self.assertTrue(ACCENT.startswith("#"))

    def test_only_known_citations_are_linkable(self):
        source = Source("R1", "Page 2")
        text = "See [R1], but ignore [R99] and malformed [R2x]."
        spans = valid_citation_spans(text, [source])
        self.assertEqual(len(spans), 1)
        start, end, matched = spans[0]
        self.assertEqual(text[start:end], "[R1]")
        self.assertIs(matched, source)

    def test_repeated_known_citations_each_resolve(self):
        source = Source("R2", "Page 4")
        text = "[R2] supports this; another detail is in [R2]."
        spans = valid_citation_spans(text, [source])
        self.assertEqual([text[start:end] for start, end, _ in spans], ["[R2]", "[R2]"])
        self.assertTrue(all(item is source for _, _, item in spans))

    def test_empty_sources_produce_no_links(self):
        self.assertEqual(valid_citation_spans("Claim [R1].", []), [])
        self.assertEqual(valid_citation_spans("Claim [R1].", None), [])

    def test_popup_bounds_follow_the_reader_window(self):
        root = type(
            "Root", (),
            {
                "winfo_rootx": lambda self: 200,
                "winfo_rooty": lambda self: 120,
                "winfo_width": lambda self: 760,
                "winfo_height": lambda self: 520,
            },
        )()
        self.assertEqual(_popup_bounds(root), (200, 120, 960, 640))

    def test_popup_position_stays_inside_reader_window_and_flips_above_anchor(self):
        position = _popup_position(
            900, 590, 300, 180, (200, 120, 960, 640), anchor_y=590
        )
        self.assertEqual(position, (652, 398))

    def test_popup_position_clamps_to_the_top_left_window_margin(self):
        position = _popup_position(0, 0, 300, 180, (200, 120, 960, 640))
        self.assertEqual(position, (208, 128))

    def test_study_menu_contains_only_available_actions(self):
        calls = []
        actions = _study_menu_actions(
            lambda value: calls.append(("revision", value)),
            None,
            lambda value: calls.append(("flashcards", value)),
        )
        self.assertEqual([label for label, _handler in actions], [
            "Revision notes", "Create flashcards",
        ])
        for _label, handler in actions:
            handler("selected passage")
        self.assertEqual(calls, [
            ("revision", "selected passage"),
            ("flashcards", "selected passage"),
        ])


if __name__ == "__main__":
    unittest.main()
