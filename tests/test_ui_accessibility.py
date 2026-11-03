"""Lightweight regression checks for keyboard-accessibility affordances."""
import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
UI_SOURCE = (ROOT / "app" / "ui.py").read_text(encoding="utf-8")


class UIAccessibilitySourceTests(unittest.TestCase):
    def test_ui_module_parses(self):
        ast.parse(UI_SOURCE)

    def test_sidebar_has_keyboard_shortcut(self):
        self.assertIn('"<Control-b>"', UI_SOURCE)
        self.assertIn('Toggle navigation sidebar (Ctrl+B)', UI_SOURCE)
        self.assertIn('def _toggle_sidebar_shortcut', UI_SOURCE)

    def test_search_shortcut_selects_existing_query(self):
        self.assertIn('"<Control-f>"', UI_SOURCE)
        self.assertIn('def _focus_document_search', UI_SOURCE)
        self.assertIn('self.search_entry.selection_range(0, tk.END)', UI_SOURCE)

    def test_buttons_have_visible_focus_affordance(self):
        self.assertIn('"highlightthickness": 2', UI_SOURCE)
        self.assertIn('"highlightcolor": self.FOCUS', UI_SOURCE)
        self.assertIn('"takefocus": True', UI_SOURCE)


if __name__ == "__main__":
    unittest.main()
