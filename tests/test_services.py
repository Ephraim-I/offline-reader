import unittest
from unittest.mock import patch

import requests

from services import api_client, llm_service


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self.ok = 200 <= status_code < 400
        self.payload = payload

    def json(self):
        return self.payload


class ServiceTests(unittest.TestCase):
    def setUp(self):
        llm_service.cache.clear()
        self.client = llm_service.app.test_client()

    def test_explanation_response_is_cached_only_after_success(self):
        with patch("services.llm_service.query_llm", return_value="A hereditary enzyme deficiency."):
            first = self.client.post("/explain", json={"word": "enzymopenic", "sentence": "An enzymopenic condition affects enzyme activity."})
        second = self.client.post("/explain", json={"word": "enzymopenic", "sentence": "An enzymopenic condition affects enzyme activity."})
        self.assertEqual(first.status_code, 200)
        self.assertFalse(first.get_json()["cached"])
        self.assertTrue(second.get_json()["cached"])

    def test_unavailable_llm_is_a_typed_error_and_not_cached(self):
        with patch("services.llm_service.query_llm", return_value=None):
            response = self.client.post("/explain", json={"word": "enzyme", "sentence": "An enzyme changes a reaction."})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.get_json()["error"]["code"], "llm_unavailable")
        self.assertFalse(llm_service.cache)

    def test_api_client_returns_typed_success_and_failure(self):
        with patch("services.api_client.requests.post", return_value=FakeResponse(200, {"meaning": "A test answer."})):
            success = api_client.explain("test", "A test sentence.")
        with patch("services.api_client.requests.post", return_value=FakeResponse(503, {"error": {"code": "llm_unavailable"}})):
            failure = api_client.explain("test", "A test sentence.")
        self.assertTrue(success.ok)
        self.assertEqual(success.text, "A test answer.")
        self.assertFalse(failure.ok)
        self.assertEqual(failure.code, "llm_unavailable")


    def test_query_llm_handles_timeout_and_connection_failure(self):
        for exception in (requests.Timeout("slow model"), requests.ConnectionError("model offline")):
            with self.subTest(exception=type(exception).__name__):
                with patch("services.llm_service.requests.post", side_effect=exception):
                    self.assertIsNone(llm_service.query_llm("test prompt"))

    def test_query_llm_handles_invalid_json_and_malformed_payloads(self):
        class InvalidJsonResponse:
            def raise_for_status(self):
                return None

            def json(self):
                raise ValueError("invalid json")

        class MalformedResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {"choices": [{"text": None}]}

        for fake_response in (InvalidJsonResponse(), MalformedResponse()):
            with self.subTest(response=type(fake_response).__name__):
                with patch("services.llm_service.requests.post", return_value=fake_response):
                    self.assertIsNone(llm_service.query_llm("test prompt"))

    def test_api_client_maps_timeout_connection_and_invalid_json(self):
        with patch("services.api_client.requests.post", side_effect=requests.Timeout()):
            timed_out = api_client.ask("question")
        with patch("services.api_client.requests.post", side_effect=requests.ConnectionError()):
            offline = api_client.ask("question")
        with patch("services.api_client.requests.post", return_value=FakeResponse(200, None)):
            malformed = api_client.ask("question")

        self.assertEqual(timed_out.code, "timeout")
        self.assertFalse(timed_out.ok)
        self.assertEqual(offline.code, "offline")
        self.assertFalse(offline.ok)
        self.assertEqual(malformed.code, "empty_response")
        self.assertFalse(malformed.ok)

    def test_api_client_maps_non_json_response_to_invalid_response(self):
        class InvalidJsonResponse:
            status_code = 200
            ok = True

            def json(self):
                raise ValueError("not json")

        with patch("services.api_client.requests.post", return_value=InvalidJsonResponse()):
            result = api_client.ask("question")
        self.assertEqual(result.code, "invalid_response")
        self.assertFalse(result.ok)

    def test_cache_capacity_zero_disables_caching(self):
        with patch("services.llm_service.EXPLANATION_CACHE_SIZE", 0):
            llm_service.cache_explanation(("term", "context"), "meaning")
        self.assertFalse(llm_service.cache)

    def test_cache_evicts_oldest_entry_at_capacity(self):
        with patch("services.llm_service.EXPLANATION_CACHE_SIZE", 2):
            llm_service.cache_explanation(("one", "context"), "first")
            llm_service.cache_explanation(("two", "context"), "second")
            llm_service.cache_explanation(("three", "context"), "third")
        self.assertIsNone(llm_service.cached_explanation(("one", "context")))
        self.assertEqual(llm_service.cached_explanation(("two", "context")), "second")
        self.assertEqual(llm_service.cached_explanation(("three", "context")), "third")


if __name__ == "__main__":
    unittest.main()
