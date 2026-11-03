import unittest
from unittest.mock import patch

from documents.models import ReadingContext
from ai.study_tools import (
    generate_flashcards,
    generate_quiz,
    parse_flashcards,
    parse_quiz_questions,
)
from services.api_client import ServiceResponse


class RevisionNotesTests(unittest.TestCase):
    def setUp(self):
        self.context = ReadingContext(
            selected_text="A transformer transfers energy between circuits.",
            surrounding_text=(
                "A transformer transfers energy between circuits by "
                "electromagnetic induction."
            ),
            document_title="Electricity.txt",
            document_format="TXT",
            location="Character 1",
            char_start=0,
            char_end=49,
        )

    def test_revision_prompt_is_scoped_and_passes_through_local_api_response(self):
        from ai.assistant import LocalReaderAssistant

        response = ServiceResponse(
            ok=True,
            text="- Transfers energy between circuits\n- Uses electromagnetic induction",
        )
        with patch("ai.assistant.ask", return_value=response) as ask:
            result = LocalReaderAssistant().generate_revision_notes(self.context)

        self.assertIs(result, response)
        prompt = ask.call_args.args[0]
        self.assertIn(self.context.selected_text, prompt)
        self.assertIn(self.context.surrounding_text, prompt)
        self.assertIn(self.context.document_title, prompt)
        self.assertIn("only the selected passage", prompt)
        self.assertIn("3 to 6 short bullet points", prompt)
        self.assertIn("Do not add facts", prompt)

    def test_revision_generation_preserves_typed_local_api_failure(self):
        from ai.assistant import LocalReaderAssistant

        response = ServiceResponse(
            ok=False,
            text="The local AI model is unavailable. Please try again.",
            code="llm_unavailable",
        )
        with patch("ai.assistant.ask", return_value=response):
            result = LocalReaderAssistant().generate_revision_notes(self.context)

        self.assertFalse(result.ok)
        self.assertEqual(result.code, "llm_unavailable")
        self.assertEqual(result.text, response.text)

    def test_multiple_choice_quiz_structure_is_parsed_and_validated(self):
        payload = (
            '[{"question":"What transfers energy?","options":["A transformer",'
            '"A resistor","A capacitor"],"answer":"A transformer",'
            '"explanation":"The passage says the transformer transfers energy."}]'
        )

        questions = parse_quiz_questions(payload, 1, "multiple_choice")

        self.assertEqual(questions[0].kind, "multiple_choice")
        self.assertEqual(questions[0].options, ("A transformer", "A resistor", "A capacitor"))
        self.assertEqual(questions[0].answer, "A transformer")

    def test_true_false_and_short_answer_quizzes_are_validated(self):
        true_false = parse_quiz_questions(
            '[{"question":"Does it transfer energy?","answer":"true",'
            '"explanation":"The passage states this."}]',
            1,
            "true_false",
        )
        short_answer = parse_quiz_questions(
            '[{"question":"How is energy transferred?","answer":"By induction",'
            '"explanation":"This is stated in the surrounding context."}]',
            1,
            "short_answer",
        )

        self.assertEqual(true_false[0].answer, "True")
        self.assertEqual(true_false[0].options, ("True", "False"))
        self.assertEqual(short_answer[0].options, ())

    def test_invalid_quiz_json_wrong_count_and_invalid_answer_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "invalid quiz JSON"):
            parse_quiz_questions("not json", 1, "short_answer")
        with self.assertRaisesRegex(ValueError, "exactly 2"):
            parse_quiz_questions("[]", 2, "short_answer")
        with self.assertRaisesRegex(ValueError, "must match one answer option"):
            parse_quiz_questions(
                '[{"question":"What transfers energy?","options":["A","B","C"],'
                '"answer":"D","explanation":"Not supported"}]',
                1,
                "multiple_choice",
            )
        with self.assertRaisesRegex(ValueError, "duplicate questions"):
            parse_quiz_questions(
                '[{"question":"Same?","answer":"A","explanation":"One."},'
                '{"question":" same? ","answer":"B","explanation":"Two."}]',
                2,
                "short_answer",
            )

    def test_quiz_generation_is_scoped_and_preserves_service_failure(self):
        questions_json = (
            '[{"question":"What transfers energy?","options":["A transformer",'
            '"A resistor","A capacitor"],"answer":"A transformer",'
            '"explanation":"The passage states this."}]'
        )
        success = ServiceResponse(ok=True, text=questions_json)
        with patch("ai.study_tools.ask", return_value=success) as ask:
            response, questions = generate_quiz(
                self.context, 1, "easy", "multiple_choice"
            )

        self.assertTrue(response.ok)
        self.assertEqual(len(questions), 1)
        prompt = ask.call_args.args[0]
        self.assertIn(self.context.selected_text, prompt)
        self.assertIn(self.context.surrounding_text, prompt)
        self.assertIn("only the selected passage", prompt)
        self.assertIn("chapter", prompt)

        failure = ServiceResponse(
            ok=False, text="The local AI model is unavailable.", code="llm_unavailable"
        )
        with patch("ai.study_tools.ask", return_value=failure):
            failed_response, failed_questions = generate_quiz(
                self.context, 1, "easy", "multiple_choice"
            )
        self.assertEqual(failed_response.code, "llm_unavailable")
        self.assertEqual(failed_questions, ())

    def test_malformed_model_quiz_is_not_returned_as_success(self):
        with patch(
            "ai.study_tools.ask",
            return_value=ServiceResponse(ok=True, text="[]"),
        ):
            response, questions = generate_quiz(
                self.context, 2, "medium", "short_answer"
            )

        self.assertFalse(response.ok)
        self.assertEqual(response.code, "invalid_quiz")
        self.assertEqual(questions, ())

    def test_flashcard_json_is_normalized_and_checked(self):
        payload = (
            '[{"front":"  What transfers energy? ","back":"A transformer",'
            '"explanation":"The passage identifies a transformer."}]'
        )

        cards = parse_flashcards(payload, 1)

        self.assertEqual(cards[0].front, "What transfers energy?")
        self.assertEqual(cards[0].back, "A transformer")
        self.assertEqual(cards[0].explanation, "The passage identifies a transformer.")

    def test_invalid_flashcard_json_count_fields_and_duplicate_front_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "invalid flashcard JSON"):
            parse_flashcards("not json", 1)
        with self.assertRaisesRegex(ValueError, "exactly 2"):
            parse_flashcards("[]", 2)
        with self.assertRaisesRegex(ValueError, "non-empty front, back"):
            parse_flashcards(
                '[{"front":"A front","back":"","explanation":"Evidence"}]', 1
            )
        with self.assertRaisesRegex(ValueError, "duplicate flashcards"):
            parse_flashcards(
                '[{"front":"Same front","back":"One","explanation":"Evidence"},'
                '{"front":" same front ","back":"Two","explanation":"Evidence"}]',
                2,
            )

    def test_flashcard_generation_is_passage_scoped_and_preserves_service_failure(self):
        payload = (
            '[{"front":"What transfers energy?","back":"A transformer",'
            '"explanation":"The passage supports this."}]'
        )
        with patch(
            "ai.study_tools.ask",
            return_value=ServiceResponse(ok=True, text=payload),
        ) as ask:
            response, cards = generate_flashcards(self.context, 1)

        self.assertTrue(response.ok)
        self.assertEqual(len(cards), 1)
        prompt = ask.call_args.args[0]
        self.assertIn(self.context.selected_text, prompt)
        self.assertIn(self.context.surrounding_text, prompt)
        self.assertIn("only the supplied selected passage", prompt)
        self.assertIn("chapter", prompt)

        failure = ServiceResponse(
            ok=False, text="The local model is unavailable.", code="llm_unavailable"
        )
        with patch("ai.study_tools.ask", return_value=failure):
            failed_response, failed_cards = generate_flashcards(self.context, 1)
        self.assertEqual(failed_response.code, "llm_unavailable")
        self.assertEqual(failed_cards, ())

    def test_malformed_model_flashcards_are_not_returned_as_success(self):
        with patch(
            "ai.study_tools.ask",
            return_value=ServiceResponse(ok=True, text="[]"),
        ):
            response, cards = generate_flashcards(self.context, 2)

        self.assertFalse(response.ok)
        self.assertEqual(response.code, "invalid_flashcards")
        self.assertEqual(cards, ())


if __name__ == "__main__":
    unittest.main()
