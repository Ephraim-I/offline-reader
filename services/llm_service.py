import logging
from collections import OrderedDict
from threading import RLock

import requests
from flask import Flask, jsonify, request

from app_config import API_HOST, API_PORT, EXPLANATION_CACHE_SIZE, LLM_COMPLETIONS_URL, LLM_REQUEST_TIMEOUT_SECONDS, MAX_COMPLETION_TOKENS


app = Flask(__name__)
cache = OrderedDict()
_cache_lock = RLock()


def classify_input(text):
    words = text.split()
    return "word" if len(words) == 1 else "phrase" if len(words) <= 8 else "sentence"


def error_response(code, message, status):
    return jsonify({"error": {"code": code, "message": message}}), status


def request_data():
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else None


def clean_completion(value):
    if not isinstance(value, str):
        return None
    text = " ".join(value.strip().split())
    for prefix in ("Answer:", "Explanation:", "Definition:"):
        if text.lower().startswith(prefix.lower()):
            text = text[len(prefix):].strip()
    return text or None


def query_llm(prompt):
    try:
        response = requests.post(
            LLM_COMPLETIONS_URL,
            json={"prompt": prompt, "max_tokens": MAX_COMPLETION_TOKENS, "temperature": 0.1, "stop": ["\n\n", "Selected text:"]},
            timeout=LLM_REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        data = response.json()
        text = clean_completion(data.get("choices", [{}])[0].get("text"))
        if text is None:
            app.logger.warning("LLM returned an empty or malformed completion")
        return text
    except (requests.RequestException, ValueError, AttributeError, IndexError, TypeError) as error:
        app.logger.warning("LLM request failed: %s", error)
        return None


def explanation_prompt(text, sentence, input_type):
    task = {"word": "Define the selected word in this context.", "phrase": "Explain the selected phrase in this context.", "sentence": "Rewrite the selected sentence in simpler language."}[input_type]
    return f"""You are an accurate reading assistant. {task}

Selected text: {text}
Context sentence: {sentence}

Rules:
- Explain the selected text, not the whole document.
- Keep important medical or scientific meaning accurate.
- Use plain language and one or two concise sentences.
- Do not invent facts, diagnoses, or treatment advice.
- If the selected text is incomplete, say so briefly.
- Return only the explanation.

Explanation:
"""


def cached_explanation(key):
    """Return and promote a cached explanation without racing other requests."""
    with _cache_lock:
        value = cache.get(key)
        if value is not None:
            cache.move_to_end(key)
        return value


def cache_explanation(key, value):
    """Store successful results; a non-positive capacity disables the cache."""
    capacity = max(0, EXPLANATION_CACHE_SIZE)
    with _cache_lock:
        if capacity == 0:
            cache.clear()
            return
        cache[key] = value
        cache.move_to_end(key)
        while len(cache) > capacity:
            cache.popitem(last=False)


@app.post("/explain")
def explain():
    data = request_data()
    if data is None:
        return error_response("invalid_request", "Expected a JSON object.", 400)
    text, sentence = data.get("word", ""), data.get("sentence", "")
    if not isinstance(text, str) or not isinstance(sentence, str) or not text.strip():
        return error_response("invalid_request", "'word' must be a non-empty string and 'sentence' must be a string.", 400)
    if len(text) > 1000 or len(sentence) > 8000:
        return error_response("input_too_large", "The selected text or context is too long.", 413)
    text, sentence = text.strip(), sentence.strip()
    key = (text, sentence)
    cached = cached_explanation(key)
    if cached is not None:
        return jsonify({"meaning": cached, "cached": True})
    response = query_llm(explanation_prompt(text, sentence, classify_input(text)))
    if response is None:
        return error_response("llm_unavailable", "The local AI model is unavailable. Please try again.", 503)
    cache_explanation(key, response)
    return jsonify({"meaning": response, "cached": False})


@app.post("/ask")
def ask():
    data = request_data()
    if data is None:
        return error_response("invalid_request", "Expected a JSON object.", 400)
    question = data.get("question", "")
    if not isinstance(question, str) or not question.strip():
        return error_response("invalid_request", "'question' must be a non-empty string.", 400)
    if len(question) > 9000:
        return error_response("input_too_large", "The question is too long.", 413)
    prompt = f"""You are an accurate reading assistant.

Reader question and context:
{question.strip()}

Answer directly in one or two concise paragraphs. Use the supplied context only when it supports the answer. Do not invent medical facts or treatment advice.

Answer:
"""
    response = query_llm(prompt)
    if response is None:
        return error_response("llm_unavailable", "The local AI model is unavailable. Please try again.", 503)
    return jsonify({"answer": response})


@app.get("/health")
@app.get("/")
def health():
    return {"status": "ok", "llm_url": LLM_COMPLETIONS_URL}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    app.run(host=API_HOST, port=API_PORT, threaded=True)
