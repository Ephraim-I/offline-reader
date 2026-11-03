import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pymupdf

from app.reader_controller import ReaderController
from documents.manager import DocumentOpenError
from documents.models import PDFDocument, TextDocument
from services.api_client import ServiceResponse


class DocumentArchitectureTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.controller = ReaderController()

    def tearDown(self):
        self.controller.close_document()
        self.directory.cleanup()


    def test_failed_open_keeps_previous_document_active(self):
        path = self.root / "existing.txt"
        path.write_text("keep this document open")
        original = self.controller.open_document(path)

        with self.assertRaises(DocumentOpenError):
            self.controller.open_document(self.root / "missing.txt")

        self.assertIs(self.controller.document, original)
        self.assertEqual(self.controller.document.content, "keep this document open")

    def test_background_index_build_does_not_block_document_switch_or_publish_stale_results(self):
        import threading
        from unittest.mock import patch
        from documents.retrieval import DocumentSearchIndex

        first_path = self.root / "first.txt"
        first_path.write_text("Capacitors store electrical energy.")
        second_path = self.root / "second.txt"
        second_path.write_text("Inductors store energy in a magnetic field.")
        self.controller.open_document(first_path)

        build_started = threading.Event()
        allow_build_to_finish = threading.Event()
        search_result = []

        def delayed_index(document):
            build_started.set()
            self.assertTrue(allow_build_to_finish.wait(timeout=3))
            return DocumentSearchIndex(document)

        with patch("app.reader_controller.DocumentSearchIndex", side_effect=delayed_index):
            worker = threading.Thread(
                target=lambda: search_result.extend(
                    self.controller.search_document("capacitors electrical")
                ),
                daemon=True,
            )
            worker.start()
            self.assertTrue(build_started.wait(timeout=3))

            # This should complete while the old document's index build is paused.
            active = self.controller.open_document(second_path)
            self.assertEqual(active.path, second_path)

            allow_build_to_finish.set()
            worker.join(timeout=3)
            self.assertFalse(worker.is_alive())

        self.assertEqual(search_result, [])
        self.assertIsNone(self.controller.search_index)
        current_results = self.controller.search_document("inductors magnetic")
        self.assertTrue(current_results)
        self.assertIn("inductors", current_results[0].text.lower())

    def test_text_document_provides_sentence_context(self):
        path = self.root / "notes.txt"
        path.write_text("The heart pumps blood. Oxygen travels through arteries.")

        document = self.controller.open_document(path)
        self.assertIsInstance(document, TextDocument)
        context = self.controller.text_context("pumps", 10)

        self.assertEqual(context.document_format, "TXT")
        self.assertEqual(context.selected_text, "pumps")
        self.assertEqual(context.surrounding_text, "The heart pumps blood.")

    def test_pdf_document_renders_and_recovers_selected_text(self):
        path = self.root / "study.pdf"
        source = pymupdf.open()
        page = source.new_page()
        page.insert_text((72, 72), "The heart pumps blood through the body.")
        source.save(path)
        source.close()

        document = self.controller.open_document(path)
        self.assertIsInstance(document, PDFDocument)
        rendered = document.render_page(0, 1.0)
        self.assertTrue(rendered.png_bytes.startswith(b"\x89PNG"))

        word = next(item for item in document._document[0].get_text("words") if item[4] == "pumps")
        word_rectangle = (word[0], word[1], word[2], word[3])
        context = self.controller.pdf_context(0, *word_rectangle)

        self.assertIsNotNone(context)
        self.assertEqual(context.document_format, "PDF")
        self.assertEqual(context.selected_text, "pumps")
        self.assertIn("heart pumps blood", context.surrounding_text.lower())


    def test_text_selection_preserves_exact_punctuation_and_whitespace(self):
        path = self.root / "exact.txt"
        content = "Alpha, beta!  Alpha, beta!"
        path.write_text(content)
        document = self.controller.open_document(path)

        first = document.context_for_selection("Alpha,", 0)
        second_offset = content.rfind("Alpha,")
        second = document.context_for_selection("Alpha,", second_offset)

        self.assertEqual(first.selected_text, "Alpha,")
        self.assertEqual(second.selected_text, "Alpha,")
        self.assertEqual(first.location, "Character 1")
        self.assertEqual(second.location, f"Character {second_offset + 1}")

    def test_text_selection_rejects_stale_offset_instead_of_using_another_occurrence(self):
        path = self.root / "duplicate.txt"
        path.write_text("same phrase, then same phrase")
        document = self.controller.open_document(path)

        with self.assertRaisesRegex(ValueError, "select the text again"):
            document.context_for_selection("same phrase", 3)

    def test_pdf_selection_uses_word_centers_not_any_intersecting_word(self):
        path = self.root / "selection.pdf"
        source = pymupdf.open()
        page = source.new_page()
        page.insert_text((72, 72), "alpha beta gamma")
        source.save(path)
        source.close()

        document = self.controller.open_document(path)
        words = document._document[0].get_text("words", sort=True)
        alpha, beta, gamma = words
        # This rectangle clips the edge of beta but does not include its center.
        context = document.context_for_rectangle(
            0, alpha[0], alpha[1], beta[0] + 1, alpha[3]
        )
        self.assertIsNotNone(context)
        self.assertEqual(context.selected_text, "alpha")
        # A perfectly horizontal drag remains valid and selects the word whose
        # center is inside the horizontal range.
        horizontal = document.context_for_rectangle(
            0, alpha[0], (alpha[1] + alpha[3]) / 2,
            alpha[2], (alpha[1] + alpha[3]) / 2,
        )
        self.assertIsNotNone(horizontal)
        self.assertEqual(horizontal.selected_text, "alpha")

    def test_text_selection_trims_edge_whitespace_without_shifting_context(self):
        path = self.root / "whitespace.txt"
        content = "Before.   selected phrase   After."
        path.write_text(content)
        document = self.controller.open_document(path)
        start = content.index("   selected phrase   ")

        context = document.context_for_selection("   selected phrase   ", start)

        self.assertEqual(context.selected_text, "selected phrase")
        self.assertEqual(context.location, f"Character {content.index('selected phrase') + 1}")
        self.assertIn("selected phrase", context.surrounding_text)

    def test_failed_document_open_keeps_previous_document_active(self):
        original_path = self.root / "original.txt"
        original_path.write_text("Still open")
        active = self.controller.open_document(original_path)

        with self.assertRaises(DocumentOpenError):
            self.controller.open_document(self.root / "missing.txt")

        self.assertIs(self.controller.document, active)
        self.assertEqual(self.controller.document.content, "Still open")

    def test_pdf_context_uses_selected_occurrence_when_phrase_repeats(self):
        path = self.root / "repeated.pdf"
        source = pymupdf.open()
        page = source.new_page()
        page.insert_text((72, 72), "The heart pumps blood. The machine pumps water.")
        source.save(path)
        source.close()

        document = self.controller.open_document(path)
        words = document._document[0].get_text("words", sort=True)
        selected_word = next(word for word in words if word[4] == "pumps" and word[0] > 180)
        context = document.context_for_rectangle(0, *selected_word[:4])

        self.assertIsNotNone(context)
        self.assertEqual(context.selected_text, "pumps")
        self.assertIn("machine pumps water", context.surrounding_text.lower())
        self.assertNotIn("heart pumps blood", context.surrounding_text.lower())


    def test_pdf_context_does_not_mix_adjacent_columns(self):
        path = self.root / "columns.pdf"
        source = pymupdf.open()
        page = source.new_page(width=600, height=500)
        page.insert_text((40, 100), "bond distance is the distance between two bonded atoms.")
        page.insert_text((360, 100), "the patient's blood was drawn for the test.")
        source.save(path)
        source.close()

        document = self.controller.open_document(path)
        words = document._document[0].get_text("words", sort=True)
        selected_word = next(word for word in words if word[4] == "distance" and word[0] < 300)
        context = document.context_for_rectangle(0, *selected_word[:4])

        self.assertIsNotNone(context)
        self.assertIn("bonded atoms", context.surrounding_text.lower())
        self.assertNotIn("blood was drawn", context.surrounding_text.lower())

    def test_controller_uses_the_same_context_for_explanations_and_followups(self):
        path = self.root / "notes.txt"
        path.write_text("The heart pumps blood.")
        self.controller.open_document(path)
        context = self.controller.text_context("pumps", 10)

        with patch(
            "ai.assistant.explain",
            return_value=ServiceResponse(ok=True, text="Moves blood."),
        ):
            explanation = self.controller.explain(context)
        with patch(
            "ai.assistant.ask",
            return_value=ServiceResponse(
                ok=True, text="It moves blood around the body."
            ),
        ):
            answer = self.controller.answer("What does it do?", context)

        self.assertEqual(explanation.context, context)
        self.assertEqual(explanation.text, "Moves blood.")
        self.assertEqual(answer.context, context)
        self.assertIn("moves blood", answer.text)


if __name__ == "__main__":
    unittest.main()
