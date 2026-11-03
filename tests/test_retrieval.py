import tempfile
import unittest
from pathlib import Path
from typing import Sequence
from unittest.mock import patch

import pymupdf

from ai.assistant import LocalReaderAssistant
from documents.manager import DocumentManager
from documents.embeddings import EmbeddingError
from documents.models import ReadingContext
from documents.retrieval import DocumentSearchIndex, format_evidence
from services.api_client import ServiceResponse


class FakeEmbeddingClient:
    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        vectors = []
        for text in texts:
            folded = text.casefold()
            if "physician" in folded:
                vectors.append([1.0, 0.0])
            elif "astronom" in folded:
                vectors.append([0.0, 1.0])
            else:
                vectors.append([0.99, 0.01])
        return vectors


class DocumentRetrievalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_text_search_finds_related_passage_beyond_selection(self):
        path = self.root / "chapter.txt"
        path.write_text(
            "Opening notes about anatomy.\n\n"
            "The heart pumps blood through the circulatory system. Arteries carry blood away from the heart.\n\n"
            "A separate paragraph discusses electrical resistance."
        )
        document = DocumentManager().open(path)
        index = DocumentSearchIndex(document)
        results = index.search("How does blood travel from the heart?", limit=3)

        self.assertTrue(results)
        self.assertIn("arteries", results[0].text.lower())
        self.assertEqual(results[0].ref, "R1")
        self.assertIsNotNone(results[0].char_start)
        self.assertTrue(results[0].location.startswith("Character "))

    def test_pdf_search_records_clickable_page_location(self):
        path = self.root / "chapter.pdf"
        pdf = pymupdf.open()
        first = pdf.new_page()
        first.insert_text((72, 72), "Introduction to circuits and voltage.")
        second = pdf.new_page()
        second.insert_text((72, 72), "Ohm's law relates voltage, current, and resistance.")
        pdf.save(path)
        pdf.close()

        document = DocumentManager().open(path)
        results = DocumentSearchIndex(document).search("voltage current resistance", limit=3)
        self.assertTrue(results)
        self.assertEqual(results[0].page_index, 1)
        self.assertEqual(results[0].location, "Page 2")
        document.close()

    def test_stopword_only_query_returns_no_evidence(self):
        path = self.root / "notes.txt"
        path.write_text("The circuit is ready and the system is in the lab.")
        document = DocumentManager().open(path)
        index = DocumentSearchIndex(document)

        self.assertEqual(index.search("the is and", limit=5), [])
        self.assertEqual(index.search("   ", limit=5), [])

    def test_evidence_context_is_bounded_and_labeled(self):
        path = self.root / "chapter.txt"
        path.write_text(" " .join(["voltage current resistance"] * 500))
        document = DocumentManager().open(path)
        results = DocumentSearchIndex(document).search("voltage current", limit=5)
        evidence = format_evidence(results, max_chars=900)
        self.assertLessEqual(len(evidence), 900)
        self.assertIn("[R1]", evidence)


    def test_controller_exposes_search_results_with_document_locations(self):
        from app.reader_controller import ReaderController

        path = self.root / "notes.txt"
        path.write_text("First paragraph.\n\nCapacitors store electrical energy in an electric field.")
        controller = ReaderController()
        try:
            controller.open_document(path)
            results = controller.search_document("capacitors electrical energy")
            self.assertTrue(results)
            self.assertIn("capacitors", results[0].text.lower())
            self.assertEqual(results[0].document_path, str(path))
            self.assertIsNotNone(results[0].char_start)
        finally:
            controller.close_document()


    def test_query_expansion_finds_common_synonyms(self):
        path = self.root / "notes.txt"
        path.write_text(
            "A useful technique is to divide the task into smaller steps.\n\n"
            "This unrelated paragraph discusses weather and seasons."
        )
        document = DocumentManager().open(path)
        results = DocumentSearchIndex(document).search("method for solving a task", limit=3)
        self.assertTrue(results)
        self.assertIn("technique", results[0].text.lower())

    def test_section_heading_terms_help_rank_its_passages(self):
        path = self.root / "sections.txt"
        path.write_text(
            "CELLULAR ENERGY\n"
            "ATP production powers the cell.\n\n"
            "ASTRONOMY\n"
            "Stars shine across the night sky."
        )
        document = DocumentManager().open(path)
        results = DocumentSearchIndex(document).search("cellular energy", limit=2)

        self.assertTrue(results)
        self.assertEqual(results[0].section, "CELLULAR ENERGY")
        self.assertIn("ATP production", results[0].text)

    def test_pdf_font_heading_is_carried_into_section_passages(self):
        path = self.root / "sections.pdf"
        pdf = pymupdf.open()
        page = pdf.new_page()
        page.insert_text((72, 72), "METHODS", fontsize=20)
        page.insert_text((72, 105), "We compare two experimental approaches.", fontsize=11)
        pdf.save(path)
        pdf.close()

        document = DocumentManager().open(path)
        results = DocumentSearchIndex(document).search("methods", limit=2)
        self.assertTrue(results)
        self.assertEqual(results[0].section, "METHODS")
        self.assertEqual(results[0].page_index, 0)
        document.close()

    def test_scanned_pdf_uses_local_ocr_when_tesseract_is_available(self):
        path = self.root / "scanned.pdf"
        pdf = pymupdf.open()
        pdf.new_page()
        pdf.save(path)
        pdf.close()
        document = DocumentManager().open(path)

        def fake_get_text(page, option, *args, **kwargs):
            if option == "dict":
                return {"blocks": []}
            if kwargs.get("textpage") is not None:
                return "CELLULAR ENERGY\nATP production powers the cell."
            return ""

        with (
            patch("documents.retrieval.shutil.which", return_value="/usr/bin/tesseract"),
            patch.object(pymupdf.Page, "get_textpage_ocr", return_value=object()),
            patch.object(pymupdf.Page, "get_text", fake_get_text),
        ):
            results = DocumentSearchIndex(document).search("cellular energy")

        self.assertTrue(results)
        self.assertIn("ATP production", results[0].text)
        self.assertEqual(results[0].section, "CELLULAR ENERGY")
        self.assertEqual(results[0].page_index, 0)
        document.close()

    def test_scanned_pdf_without_tesseract_logs_warning_and_has_no_ocr_passages(self):
        path = self.root / "no-ocr.pdf"
        pdf = pymupdf.open()
        pdf.new_page()
        pdf.save(path)
        pdf.close()
        document = DocumentManager().open(path)

        with (
            patch("documents.retrieval.shutil.which", return_value=None),
            patch("app_config.OCR_ENABLED", True),
            self.assertLogs("documents.retrieval", level="WARNING") as logs,
        ):
            index = DocumentSearchIndex(document)

        self.assertEqual(index.passages, [])
        self.assertIn("Tesseract is unavailable", logs.output[0])
        document.close()

    def test_local_embeddings_retrieve_semantically_similar_passage(self):
        path = self.root / "semantic.pdf"
        pdf = pymupdf.open()
        physician_page = pdf.new_page()
        physician_page.insert_text(
            (72, 72), "A physician examines patients and diagnoses illnesses."
        )
        astronomy_page = pdf.new_page()
        astronomy_page.insert_text(
            (72, 72), "Astronomers observe distant stars and galaxies."
        )
        pdf.save(path)
        pdf.close()

        document = DocumentManager().open(path)
        index = DocumentSearchIndex(document, embedding_client=FakeEmbeddingClient())
        results = index.search("clinician", limit=2)

        self.assertTrue(results)
        self.assertIn("physician", results[0].text.lower())
        self.assertEqual(results[0].page_index, 0)
        document.close()

    def test_embedding_failure_falls_back_to_lexical_search(self):
        class UnavailableEmbeddingClient:
            def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
                raise EmbeddingError("local model unavailable")

        path = self.root / "fallback.txt"
        path.write_text("A capacitor stores electrical energy in an electric field.")
        document = DocumentManager().open(path)
        with self.assertLogs("documents.retrieval", level="WARNING"):
            index = DocumentSearchIndex(
                document, embedding_client=UnavailableEmbeddingClient()
            )
        results = index.search("capacitor electrical energy")
        self.assertTrue(results)
        self.assertIn("capacitor", results[0].text.lower())

    def test_embedding_query_failure_falls_back_to_lexical_search(self):
        class IntermittentEmbeddingClient:
            def __init__(self):
                self.calls = 0

            def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
                self.calls += 1
                if self.calls > 1:
                    raise EmbeddingError("local model unavailable")
                return [[1.0, 0.0] for _text in texts]

        path = self.root / "query-fallback.txt"
        path.write_text("A capacitor stores electrical energy in an electric field.")
        document = DocumentManager().open(path)
        index = DocumentSearchIndex(
            document, embedding_client=IntermittentEmbeddingClient()
        )

        with self.assertLogs("documents.retrieval", level="WARNING"):
            results = index.search("capacitor electrical energy")

        self.assertTrue(results)
        self.assertIn("capacitor", results[0].text.lower())

    def test_pdf_source_passage_can_be_located_for_highlighting(self):
        path = self.root / "highlight.pdf"
        pdf = pymupdf.open()
        page = pdf.new_page()
        page.insert_text((72, 72), "Ohm's law relates voltage, current, and resistance.")
        pdf.save(path)
        pdf.close()
        document = DocumentManager().open(path)
        try:
            rectangles = document.find_text_rects(0, "Ohm's law relates voltage, current, and resistance.")
            self.assertTrue(rectangles)
            self.assertEqual(len(rectangles[0]), 4)
        finally:
            document.close()

    def test_pdf_source_highlight_matches_wrapped_text_with_punctuation_variations(self):
        path = self.root / "wrapped-highlight.pdf"
        pdf = pymupdf.open()
        page = pdf.new_page()
        page.insert_text((72, 72), "Voltage, current")
        page.insert_text((72, 92), "and resistance describe an electrical circuit.")
        pdf.save(path)
        pdf.close()
        document = DocumentManager().open(path)
        try:
            rectangles = document.find_text_rects(
                0, "Voltage current and resistance describe an electrical circuit"
            )
            self.assertGreaterEqual(len(rectangles), 2)
            self.assertTrue(all(len(rectangle) == 4 for rectangle in rectangles))
        finally:
            document.close()

    def test_scanned_pdf_source_highlight_uses_ocr_word_locations(self):
        path = self.root / "scanned-highlight.pdf"
        pdf = pymupdf.open()
        pdf.new_page()
        pdf.save(path)
        pdf.close()
        document = DocumentManager().open(path)
        ocr_words = [
            (72.0, 72.0, 110.0, 84.0, "Local", 0, 0, 0),
            (114.0, 72.0, 150.0, 84.0, "search", 0, 0, 1),
            (154.0, 72.0, 192.0, 84.0, "works", 0, 0, 2),
        ]

        def fake_get_text(page, option, *args, **kwargs):
            return ocr_words if option == "words" and kwargs.get("textpage") is not None else []

        try:
            with (
                patch("documents.models.shutil.which", return_value="/usr/bin/tesseract"),
                patch.object(pymupdf.Page, "get_textpage_ocr", return_value=object()),
                patch.object(pymupdf.Page, "get_text", fake_get_text),
            ):
                rectangles = document.find_text_rects(0, "Local search works")
            self.assertEqual(len(rectangles), 1)
            self.assertEqual(rectangles[0], (72.0, 72.0, 192.0, 84.0))
        finally:
            document.close()

    def test_inline_citations_only_link_known_retrieval_references(self):
        from app.popup import valid_citation_spans
        source = type("Source", (), {"ref": "R1", "location": "Page 2"})()
        spans = valid_citation_spans("Supported [R1], but not [R99].", [source])
        self.assertEqual(len(spans), 1)
        self.assertEqual(spans[0][2], source)
        self.assertEqual("Supported [R1], but not [R99]."[spans[0][0]:spans[0][1]], "[R1]")

    def test_followup_prompt_stays_within_local_api_budget(self):
        context = ReadingContext(
            selected_text="selected " * 500,
            surrounding_text="context " * 2000,
            document_title="chapter.txt", document_format="TXT", location="Character 1",
        )
        source = type("Source", (), {
            "ref": "R1", "location": "Character 1", "text": "evidence " * 200,
        })()
        with patch("ai.assistant.ask", return_value=ServiceResponse(ok=True, text="Answer.")) as mocked_ask:
            LocalReaderAssistant().answer("question " * 3000, context, sources=[source])
        self.assertLessEqual(len(mocked_ask.call_args.args[0]), 9000)

    def test_followup_prompt_includes_retrieved_evidence_and_returns_sources(self):
        context = ReadingContext(
            selected_text="pumps", surrounding_text="The heart pumps blood.",
            document_title="chapter.txt", document_format="TXT", location="Character 10",
        )
        source = type("Source", (), {
            "ref": "R1", "location": "Character 80", "text": "Arteries carry blood away from the heart.",
            "document_path": "/tmp/chapter.txt", "document_format": "TXT", "char_start": 79,
            "char_end": 120, "page_index": None, "score": 2.0,
        })()
        with patch("ai.assistant.ask", return_value=ServiceResponse(ok=True, text="Arteries carry blood away from the heart [R1].")) as mocked_ask:
            answer = LocalReaderAssistant().answer("Where does blood go?", context, sources=[source])
        self.assertIn("[R1]", mocked_ask.call_args.args[0])
        self.assertIn("Arteries carry blood", mocked_ask.call_args.args[0])
        self.assertEqual(answer.sources, (source,))


if __name__ == "__main__":
    unittest.main()
