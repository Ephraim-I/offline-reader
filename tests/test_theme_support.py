import json
import tempfile
import unittest
from pathlib import Path

from app.popup import PALETTES as POPUP_PALETTES
from app.ui import ReaderUI


def _luminance(color):
    channels = [int(color[index:index + 2], 16) / 255 for index in (1, 3, 5)]
    linear = [
        channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4
        for channel in channels
    ]
    return sum(weight * channel for weight, channel in zip((0.2126, 0.7152, 0.0722), linear))


def _contrast_ratio(foreground, background):
    lighter, darker = sorted(
        (_luminance(foreground), _luminance(background)), reverse=True
    )
    return (lighter + 0.05) / (darker + 0.05)


class ThemeSupportTests(unittest.TestCase):
    def test_theme_palettes_cover_the_same_color_roles(self):
        light = ReaderUI.THEMES["light"]
        dark = ReaderUI.THEMES["dark"]
        self.assertEqual(set(light), set(dark))
        for theme_name, other_theme in (("light", "dark"), ("dark", "light")):
            palette = ReaderUI.THEMES[theme_name]
            other_palette = ReaderUI.THEMES[other_theme]
            targets_by_source = {}
            for role, source_color in palette.items():
                targets_by_source.setdefault(source_color, set()).add(
                    other_palette[role]
                )
            self.assertTrue(all(len(targets) == 1 for targets in targets_by_source.values()))

    def test_theme_color_mapping_is_reversible(self):
        light_to_dark = ReaderUI._theme_color_mapping("light", "dark")
        dark_to_light = ReaderUI._theme_color_mapping("dark", "light")
        self.assertEqual(len(light_to_dark), len(set(ReaderUI.THEMES["light"].values())))
        for light_color, dark_color in light_to_dark.items():
            self.assertEqual(dark_to_light[dark_color], light_color)

    def test_reading_text_and_muted_text_meet_contrast_targets(self):
        for palette in ReaderUI.THEMES.values():
            self.assertGreaterEqual(
                _contrast_ratio(palette["TEXT"], palette["BACKGROUND"]), 4.5
            )
            self.assertGreaterEqual(
                _contrast_ratio(palette["READER_TEXT"], palette["EDITOR"]), 4.5
            )
            self.assertGreaterEqual(
                _contrast_ratio(palette["MUTED"], palette["PANEL"]), 4.5
            )

    def test_theme_preference_is_saved_with_existing_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            ui = ReaderUI.__new__(ReaderUI)
            ui.settings_path = Path(directory) / "settings.json"
            ui.last_directory = Path(directory)
            ui.recent_documents = []
            ui.text_font_size = 11
            ui._line_spacing = 1.0
            ui.theme = "dark"
            ui.document_states = {}

            ui._save_settings()

            saved = json.loads(ui.settings_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["theme"], "dark")
            self.assertEqual(saved["text_font_size"], 11)

    def test_popup_palettes_provide_light_and_dark_variants(self):
        self.assertEqual(set(POPUP_PALETTES), {"light", "dark"})
        self.assertNotEqual(
            POPUP_PALETTES["light"]["BACKGROUND"],
            POPUP_PALETTES["dark"]["BACKGROUND"],
        )
        self.assertEqual(
            set(POPUP_PALETTES["light"]),
            set(POPUP_PALETTES["dark"]),
        )

    def test_theme_toggle_is_keyboard_accessible(self):
        source = (Path(__file__).resolve().parents[1] / "app" / "ui.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('"<Control-Shift-d>"', source)
        self.assertIn('accelerator="Ctrl+Shift+D"', source)


if __name__ == "__main__":
    unittest.main()
