import unittest

from app.ui import ReaderUI


class DummyText:
    def __init__(self):
        self.options = None

    def configure(self, **kwargs):
        self.options = kwargs


class DummyStatus:
    def __init__(self):
        self.value = ""

    def set(self, value):
        self.value = value


class ReadingModeTests(unittest.TestCase):
    def make_ui(self):
        ui = ReaderUI.__new__(ReaderUI)
        ui._line_spacing = 1.0
        ui.text_font_size = 12
        ui.text = DummyText()
        ui.status = DummyStatus()
        ui._persist_font_size = lambda: None
        return ui

    def test_line_spacing_cycles_through_three_readable_levels(self):
        ui = self.make_ui()
        ui.cycle_line_spacing()
        self.assertEqual(ui._line_spacing, 1.35)
        self.assertEqual(ui.text.options["spacing1"], 7)
        self.assertEqual(ui.text.options["spacing3"], 9)
        ui.cycle_line_spacing()
        self.assertEqual(ui._line_spacing, 0.75)
        ui.cycle_line_spacing()
        self.assertEqual(ui._line_spacing, 1.0)

    def test_line_spacing_announces_current_setting(self):
        ui = self.make_ui()
        ui.cycle_line_spacing()
        self.assertEqual(ui.status.value, "Spacious line spacing")


if __name__ == "__main__":
    unittest.main()
