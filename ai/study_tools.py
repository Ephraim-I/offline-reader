"""Structured, passage-grounded study material generation."""
from __future__ import annotations

import json

from documents.models import ReadingContext
from notes.store import Flashcard, StudyQuestion
from services.api_client import ServiceResponse, ask


QUESTION_KINDS = {"multiple_choice", "true_false", "short_answer"}
DIFFICULTIES = {"easy", "medium", "hard"}


def parse_quiz_questions(
    payload: str,
    count: int,
    kind: str,
) -> tuple[StudyQuestion, ...]:
    if kind not in QUESTION_KINDS or not isinstance(count, int) or not 1 <= count <= 10:
        raise ValueError("Invalid quiz configuration.")
    try:
        parsed = json.loads(payload)
    except (TypeError, json.JSONDecodeError) as error:
        raise ValueError("The assistant returned invalid quiz JSON.") from error
    if not isinstance(parsed, list) or len(parsed) != count:
        raise ValueError(f"The assistant must return exactly {count} quiz questions.")

    questions = []
    seen_prompts = set()
    for index, item in enumerate(parsed, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"Question {index} must be a JSON object.")
        prompt = item.get("question")
        answer = item.get("answer")
        explanation = item.get("explanation")
        if not all(
            isinstance(value, str) and value.strip()
            for value in (prompt, answer, explanation)
        ):
            raise ValueError(
                f"Question {index} needs non-empty question, answer, and explanation text."
            )
        normalized_prompt = " ".join(prompt.split()).casefold()
        if normalized_prompt in seen_prompts:
            raise ValueError("The assistant returned duplicate questions.")
        seen_prompts.add(normalized_prompt)
        raw_options = item.get("options", [])
        if kind == "multiple_choice":
            if (
                not isinstance(raw_options, list)
                or not 3 <= len(raw_options) <= 5
                or any(not isinstance(option, str) or not option.strip() for option in raw_options)
            ):
                raise ValueError(
                    f"Question {index} needs between 3 and 5 non-empty answer options."
                )
            options = tuple(option.strip() for option in raw_options)
            if len({option.casefold() for option in options}) != len(options):
                raise ValueError(f"Question {index} has duplicate answer options.")
            if answer.strip().casefold() not in {option.casefold() for option in options}:
                raise ValueError(
                    f"Question {index}'s correct answer must match one answer option."
                )
        elif kind == "true_false":
            options = ("True", "False")
            if answer.strip().casefold() not in {"true", "false"}:
                raise ValueError(f"Question {index}'s answer must be True or False.")
            answer = "True" if answer.strip().casefold() == "true" else "False"
        else:
            options = ()
        questions.append(
            StudyQuestion(
                prompt=" ".join(prompt.split()),
                kind=kind,
                options=options,
                answer=answer.strip(),
                explanation=explanation.strip(),
            )
        )
    return tuple(questions)


def generate_quiz(
    context: ReadingContext,
    count: int,
    difficulty: str,
    kind: str,
) -> tuple[ServiceResponse, tuple[StudyQuestion, ...]]:
    if not isinstance(count, int) or not 1 <= count <= 10:
        return ServiceResponse(False, "Choose between 1 and 10 questions.", "invalid_request"), ()
    if difficulty not in DIFFICULTIES:
        return ServiceResponse(False, "Choose easy, medium, or hard difficulty.", "invalid_request"), ()
    if kind not in QUESTION_KINDS:
        return ServiceResponse(False, "Choose a supported question style.", "invalid_request"), ()

    options_instruction = (
        'Each item must contain "question", "options" (3 to 5 distinct strings), '
        '"answer" (exactly one option), and "explanation".'
        if kind == "multiple_choice"
        else (
            'Each item must contain "question", "answer" ("True" or "False"), '
            'and "explanation".'
            if kind == "true_false"
            else 'Each item must contain "question", "answer", and "explanation".'
        )
    )
    prompt = "\n\n".join(
        (
            "Create a source-grounded quiz using only the supplied passage and immediate context.",
            f"Return exactly {count} distinct questions at {difficulty} difficulty.",
            f"Question style: {kind}. {options_instruction}",
            'Return only a valid JSON array. Do not add Markdown fences or prose around it.',
            "Questions, answers, and explanations must be supported by the supplied text. "
            "Do not invent facts. If it cannot support the requested quiz, return an empty array.",
            "The source scope is only the selected passage and its immediate context. "
            "Do not claim to cover a whole section, chapter, or document.",
            f"Document: {context.document_title} ({context.location})",
            f"Selected passage:\n{context.selected_text[:2400]}",
            f"Immediate context:\n{context.surrounding_text[:3200]}",
        )
    )
    response = ask(prompt)
    if not response.ok:
        return response, ()
    try:
        questions = parse_quiz_questions(response.text, count, kind)
    except ValueError as error:
        return ServiceResponse(
            False,
            f"The assistant returned an unusable quiz: {error}",
            "invalid_quiz",
        ), ()
    return response, questions


def parse_flashcards(payload: str, count: int) -> tuple[Flashcard, ...]:
    if not isinstance(count, int) or not 1 <= count <= 10:
        raise ValueError("Invalid flashcard count.")
    try:
        parsed = json.loads(payload)
    except (TypeError, json.JSONDecodeError) as error:
        raise ValueError("The assistant returned invalid flashcard JSON.") from error
    if not isinstance(parsed, list) or len(parsed) != count:
        raise ValueError(
            f"The assistant must return exactly {count} flashcards."
        )

    cards = []
    seen_fronts = set()
    for index, item in enumerate(parsed, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"Flashcard {index} must be a JSON object.")
        front = item.get("front")
        back = item.get("back")
        explanation = item.get("explanation")
        if not all(
            isinstance(value, str) and value.strip()
            for value in (front, back, explanation)
        ):
            raise ValueError(
                f"Flashcard {index} needs non-empty front, back, and explanation text."
            )
        normalized_front = " ".join(front.split()).casefold()
        if normalized_front in seen_fronts:
            raise ValueError("The assistant returned duplicate flashcards.")
        seen_fronts.add(normalized_front)
        cards.append(
            Flashcard(
                front=" ".join(front.split()),
                back=" ".join(back.split()),
                explanation=" ".join(explanation.split()),
            )
        )
    return tuple(cards)


def generate_flashcards(
    context: ReadingContext, count: int
) -> tuple[ServiceResponse, tuple[Flashcard, ...]]:
    if not isinstance(count, int) or not 1 <= count <= 10:
        return (
            ServiceResponse(
                False, "Choose between 1 and 10 flashcards.", "invalid_request"
            ),
            (),
        )
    prompt = "\n\n".join(
        (
            "Create concise, source-grounded study flashcards from only the supplied "
            "selected passage and its immediate context.",
            f"Return exactly {count} distinct flashcards.",
            'Each item must contain "front", "back", and "explanation" as non-empty strings.',
            'Return only a valid JSON array. Do not add Markdown fences or prose around it.',
            "The back and explanation must be supported by the supplied text. "
            "Do not invent facts. If there is not enough information, return an empty array.",
            "The scope is only the selected passage and its immediate context. "
            "Do not claim to cover a whole section, chapter, or document.",
            f"Document: {context.document_title} ({context.location})",
            f"Selected passage:\n{context.selected_text[:2400]}",
            f"Immediate context:\n{context.surrounding_text[:3200]}",
        )
    )
    response = ask(prompt)
    if not response.ok:
        return response, ()
    try:
        cards = parse_flashcards(response.text, count)
    except ValueError as error:
        return ServiceResponse(
            False,
            f"The assistant returned unusable flashcards: {error}",
            "invalid_flashcards",
        ), ()
    return response, cards
