import unittest
from collections import OrderedDict

from app.ui import ReaderUI


class PdfRenderBufferTests(unittest.TestCase):
    def test_cache_is_lru_and_bounded(self):
        ui = ReaderUI.__new__(ReaderUI)
        ui._pdf_render_cache = OrderedDict()
        ui._pdf_render_cache_limit = 2

        ui._cache_pdf_render(("doc", 0, 1.0), b"page0")
        ui._cache_pdf_render(("doc", 1, 1.0), b"page1")
        # Accessing page 0 makes it the most recently used entry.
        ui._pdf_render_cache.move_to_end(("doc", 0, 1.0))
        ui._cache_pdf_render(("doc", 2, 1.0), b"page2")

        self.assertIn(("doc", 0, 1.0), ui._pdf_render_cache)
        self.assertIn(("doc", 2, 1.0), ui._pdf_render_cache)
        self.assertNotIn(("doc", 1, 1.0), ui._pdf_render_cache)
        self.assertEqual(len(ui._pdf_render_cache), 2)

    def test_cache_key_distinguishes_page_and_zoom(self):
        ui = ReaderUI.__new__(ReaderUI)
        class FakeDocument:
            path = __import__("pathlib").Path("sample.pdf")
        ui.pdf_document = FakeDocument()
        self.assertNotEqual(ui._pdf_cache_key(0, 1.0), ui._pdf_cache_key(1, 1.0))
        self.assertNotEqual(ui._pdf_cache_key(0, 1.0), ui._pdf_cache_key(0, 1.5))


if __name__ == "__main__":
    unittest.main()
