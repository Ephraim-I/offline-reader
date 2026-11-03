"""Integration-oriented checks for the reader's document-to-AI workflow.

These tests exercise the controller and HTTP client without launching Tk or a
real local model. They are intentionally deterministic and safe for CI.
"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import pymupdf
import requests

from app.reader_controller import ReaderController
from documents.manager import DocumentOpenError
from services import api_client


class _Response:
    def __init__(self, status_code=200, payload=None, ok=True):
        self.status_code = status_code
        self._payload = payload
        self.ok = ok

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class ApplicationWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.controller = ReaderController()

    def tearDown(self):
        self.controller.close_document()
        self.temp_dir.cleanup()

    def test_txt_open_selection_and_follow_up_flow(self):
        path = self.root / "notes.txt"
        path.write_text("A transformer transfers energy between circuits. It uses magnetic coupling.", encoding="utf-8")
        self.controller.open_document(path)
        context = self.controller.text_context("transformer", 2)
        self.assertEqual(context.selected_text, "transformer")
        self.assertEqual(context.document_format, "TXT")

        with patch("ai.assistant.explain", return_value=api_client.ServiceResponse(True, "A transformer transfers energy magnetically.")):
            explanation = self.controller.explain(context)
        self.assertFalse(explanation.is_error)
        self.assertIn("magnetically", explanation.text)

        with patch("ai.assistant.ask", return_value=api_client.ServiceResponse(True, "It transfers energy through magnetic coupling.")):
            answer = self.controller.answer("How does it work?", context)
        self.assertFalse(answer.is_error)
        self.assertIn("magnetic coupling", answer.text)

    def test_pdf_open_and_selection_context(self):
        path = self.root / "notes.pdf"
        pdf = pymupdf.open()
        page = pdf.new_page()
        page.insert_text((72, 72), "Voltage is the electrical potential difference.")
        pdf.save(path)
        pdf.close()

        document = self.controller.open_document(path)
        words = document._document[0].get_text("words", sort=True)
        voltage = next(word for word in words if word[4] == "Voltage")
        context = self.controller.pdf_context(0, voltage[0], voltage[1], voltage[2], voltage[3])
        self.assertIsNotNone(context)
        self.assertEqual(context.document_format, "PDF")
        self.assertEqual(context.selected_text, "Voltage")

    def test_failed_document_switch_keeps_previous_document_available(self):
        path = self.root / "valid.txt"
        path.write_text("The active document remains available.", encoding="utf-8")
        original = self.controller.open_document(path)
        with self.assertRaises(DocumentOpenError):
            self.controller.open_document(self.root / "missing.pdf")
        self.assertIs(self.controller.document, original)
        self.assertIn("remains available", self.controller.document.content)

    def test_http_client_recovers_after_connection_failure(self):
        with patch("services.api_client.requests.post", side_effect=[requests.ConnectionError(), _Response(payload={"meaning": "Recovered"})]):
            failed = api_client.explain("term", "context")
            recovered = api_client.explain("term", "context")
        self.assertFalse(failed.ok)
        self.assertEqual(failed.code, "offline")
        self.assertTrue(recovered.ok)
        self.assertEqual(recovered.text, "Recovered")

    def test_timeout_and_malformed_response_are_user_safe(self):
        with patch("services.api_client.requests.post", side_effect=requests.Timeout):
            timed_out = api_client.ask("question")
        with patch("services.api_client.requests.post", return_value=_Response(payload=ValueError("bad json"))):
            malformed = api_client.ask("question")
        self.assertEqual(timed_out.code, "timeout")
        self.assertIn("too long", timed_out.text)
        self.assertEqual(malformed.code, "invalid_response")
        self.assertIn("invalid response", malformed.text)

    def test_model_unavailable_then_recovery(self):
        unavailable = _Response(status_code=503, payload={"error": "model unavailable"}, ok=False)
        healthy = _Response(payload={"answer": "The model is responding again."})
        with patch("services.api_client.requests.post", side_effect=[unavailable, healthy]):
            first = api_client.ask("question")
            second = api_client.ask("question")
        self.assertFalse(first.ok)
        self.assertEqual(first.code, "llm_unavailable")
        self.assertTrue(second.ok)
        self.assertEqual(second.text, "The model is responding again.")


if __name__ == "__main__":
    unittest.main()
