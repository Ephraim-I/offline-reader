from dataclasses import dataclass

import requests

from app_config import API_BASE_URL, API_REQUEST_TIMEOUT_SECONDS


@dataclass(frozen=True)
class ServiceResponse:
    ok: bool
    text: str
    code: str | None = None


def _failure(text, code):
    return ServiceResponse(ok=False, text=text, code=code)


def _service_failure(response, data):
    if response.status_code == 503:
        return _failure("The local AI model is unavailable. Please try again.", "llm_unavailable")
    if response.status_code >= 500:
        return _failure("The AI service encountered an error. Please try again.", "server_error")
    error = data.get("error") if isinstance(data, dict) else None
    if isinstance(error, dict) and isinstance(error.get("message"), str):
        return _failure(error["message"], error.get("code", "invalid_request"))
    return _failure("The AI service could not process that request.", "invalid_request")


def _post(endpoint, payload, response_key):
    try:
        response = requests.post(f"{API_BASE_URL}{endpoint}", json=payload, timeout=API_REQUEST_TIMEOUT_SECONDS)
    except requests.Timeout:
        return _failure("The AI service took too long to respond. Please try again.", "timeout")
    except requests.ConnectionError:
        return _failure("The AI service is unavailable. Start the local reader stack and try again.", "offline")
    except requests.RequestException:
        return _failure("The AI service is unavailable. Please try again.", "network_error")
    try:
        data = response.json()
    except ValueError:
        return _failure("The AI service returned an invalid response. Please try again.", "invalid_response")
    if not response.ok:
        return _service_failure(response, data)
    answer = data.get(response_key) if isinstance(data, dict) else None
    if not isinstance(answer, str) or not answer.strip():
        return _failure("The AI service returned no answer. Please try again.", "empty_response")
    return ServiceResponse(ok=True, text=answer.strip())


def explain(text, sentence):
    return _post("/explain", {"word": text, "sentence": sentence}, "meaning")


def ask(question):
    return _post("/ask", {"question": question}, "answer")
