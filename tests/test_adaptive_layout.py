import unittest

from app.ui import (
    ReaderUI,
    _layout_profile_for_width,
    _reading_column_metrics,
    _sidebar_recent_label,
)


class FakeWidget:
    def __init__(self, managed=True):
        self.managed = managed
        self.pack_calls = []
        self.configure_calls = []

    def winfo_manager(self):
        return "pack" if self.managed else ""

    def pack(self, **kwargs):
        self.managed = True
        self.pack_calls.append(kwargs)

    def pack_forget(self):
        self.managed = False

    def configure(self, **kwargs):
        self.configure_calls.append(kwargs)


class FakeRoot:
    def __init__(self):
        self.idle_callbacks = []

    def after_idle(self, callback):
        self.idle_callbacks.append(callback)


class AdaptiveLayoutBehaviourTests(unittest.TestCase):
    def make_ui(self):
        ui = ReaderUI.__new__(ReaderUI)
        ui._layout_job = None
        ui._layout_profile = None
        ui._focus_mode = False
        ui._sidebar_user_visible = True
        ui._sidebar_auto_collapsed = False
        ui._navigation_sidebar_visible = True
        ui.root = FakeRoot()
        ui.header = FakeWidget()
        ui.header_subtitle = FakeWidget()
        ui.line_spacing_button = FakeWidget()
        ui.reading_zoom_in_button = FakeWidget()
        ui.reading_zoom_out_button = FakeWidget()
        ui.search_label = FakeWidget()
        ui.search_entry = FakeWidget()
        ui.navigation_sidebar = FakeWidget()
        ui._resize_reading_surface = lambda: None
        return ui

    def test_compact_layout_collapses_only_the_surrounding_chrome(self):
        ui = self.make_ui()

        ui._apply_adaptive_layout(760)

        self.assertEqual(ui._layout_profile, "compact")
        self.assertFalse(ui.navigation_sidebar.managed)
        self.assertTrue(ui._sidebar_auto_collapsed)
        self.assertFalse(ui.header_subtitle.managed)
        self.assertFalse(ui.line_spacing_button.managed)
        self.assertFalse(ui.reading_zoom_in_button.managed)
        self.assertFalse(ui.reading_zoom_out_button.managed)
        self.assertFalse(ui.search_label.managed)

    def test_expanding_restores_an_automatically_collapsed_sidebar(self):
        ui = self.make_ui()
        ui._apply_adaptive_layout(760)

        ui._apply_adaptive_layout(1120)

        self.assertTrue(ui.navigation_sidebar.managed)
        self.assertFalse(ui._sidebar_auto_collapsed)
        self.assertTrue(ui.header_subtitle.managed)
        self.assertTrue(ui.line_spacing_button.managed)
        self.assertTrue(ui.search_label.managed)

    def test_expanding_preserves_an_explicitly_hidden_sidebar(self):
        ui = self.make_ui()
        ui._apply_adaptive_layout(760)
        ui._sidebar_user_visible = False

        ui._apply_adaptive_layout(1120)

        self.assertFalse(ui.navigation_sidebar.managed)
        self.assertTrue(ui._sidebar_auto_collapsed)


class AdaptiveLayoutTests(unittest.TestCase):
    def test_compact_profile_is_selected_for_constrained_windows(self):
        self.assertEqual(_layout_profile_for_width(760), "compact")
        self.assertEqual(_layout_profile_for_width(919), "compact")

    def test_standard_profile_preserves_normal_desktop_controls(self):
        self.assertEqual(_layout_profile_for_width(920), "standard")
        self.assertEqual(_layout_profile_for_width(1119), "standard")

    def test_expanded_profile_is_selected_for_wide_windows(self):
        self.assertEqual(_layout_profile_for_width(1120), "expanded")

    def test_reading_column_contracts_for_a_narrow_window(self):
        self.assertEqual(_reading_column_metrics(300), (280, 18))
        self.assertEqual(_reading_column_metrics(500), (468, 18))

    def test_reading_column_stays_comfortable_but_bounded_on_wide_windows(self):
        self.assertEqual(_reading_column_metrics(720), (688, 42))
        self.assertEqual(_reading_column_metrics(2000), (920, 42))

    def test_recent_document_labels_are_shortened_for_the_navigation_rail(self):
        self.assertEqual(_sidebar_recent_label("/tmp/notes.txt"), "notes.txt")
        self.assertEqual(
            _sidebar_recent_label("/tmp/a-very-long-document-name-for-study.txt", limit=16),
            "a-very-long-doc…",
        )


if __name__ == "__main__":
    unittest.main()
