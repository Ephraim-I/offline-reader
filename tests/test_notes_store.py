import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timezone

from notes.store import (
    Annotation,
    Flashcard,
    Note,
    NotesStore,
    SourceReference,
    StudyQuestion,
    fingerprint_file,
)


class NotesStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="offline-reader-notes-")
        self.directory = Path(self.temp.name)
        self.database_path = self.directory / "data" / "notes.sqlite3"
        self.document_path = self.directory / "course.txt"
        self.document_path.write_text(
            "The heart pumps blood through the body.", encoding="utf-8"
        )
        self.store = NotesStore(self.database_path)

    def tearDown(self):
        self.temp.cleanup()

    def _source(self, *, page_index=None, rect=None, document_path=None):
        source_path = document_path or self.document_path
        return SourceReference(
            document_path=str(source_path),
            document_format="PDF" if page_index is not None else "TXT",
            fingerprint=fingerprint_file(source_path),
            location=f"Page {page_index + 1}" if page_index is not None else "Character 5",
            text="pumps blood",
            char_start=None if page_index is not None else 10,
            char_end=None if page_index is not None else 21,
            page_index=page_index,
            rect=rect,
        )

    def test_create_and_reload_source_linked_note(self):
        source = self._source(page_index=2, rect=(12.5, 20.0, 130.0, 48.5))
        note = self.store.create_note(
            "  Circulation  ",
            "Review the pump's role.",
            tags="biology, #exam",
            collection="Biology",
            source=source,
        )

        reopened = NotesStore(self.database_path).get_note(note.id)

        self.assertIsInstance(reopened, Note)
        self.assertEqual(reopened.title, "Circulation")
        self.assertEqual(reopened.body, "Review the pump's role.")
        self.assertEqual(reopened.tags, ("biology", "exam"))
        self.assertEqual(reopened.collection, "Biology")
        self.assertEqual(reopened.source, source)

    def test_text_source_retains_offsets_and_document_fingerprint(self):
        source = self._source()
        note = self.store.create_note("Text locator", "Body", source=source)

        restored = self.store.get_note(note.id).source

        self.assertEqual(restored.char_start, 10)
        self.assertEqual(restored.char_end, 21)
        self.assertIsNone(restored.page_index)
        self.assertEqual(restored.fingerprint, source.fingerprint)
        self.assertEqual(Path(restored.document_path), self.document_path.resolve())

    def test_document_versions_are_kept_separate_for_old_notes(self):
        old_source = self._source()
        old_note = self.store.create_note("Old source", source=old_source)
        self.document_path.write_text("A revised source with different contents.", encoding="utf-8")
        new_source = self._source()
        new_note = self.store.create_note("New source", source=new_source)

        self.assertNotEqual(old_source.fingerprint, new_source.fingerprint)
        self.assertEqual(self.store.get_note(old_note.id).source.fingerprint, old_source.fingerprint)
        self.assertEqual(self.store.get_note(new_note.id).source.fingerprint, new_source.fingerprint)

    def test_search_matches_title_body_tags_and_collection(self):
        first = self.store.create_note(
            "Cardiac cycle",
            "Ventricles contract.",
            tags="physiology",
            collection="Biology",
        )
        self.store.create_note("Study plan", "Review the chapter.", tags="exam")

        self.assertEqual([note.id for note in self.store.list_notes("cardiac")], [first.id])
        self.assertEqual([note.id for note in self.store.list_notes("ventricles")], [first.id])
        self.assertEqual([note.id for note in self.store.list_notes("physio")], [first.id])
        self.assertEqual([note.id for note in self.store.list_notes("biology")], [first.id])
        self.assertEqual([note.id for note in self.store.list_notes(tag="PHYSIOLOGY")], [first.id])
        self.assertEqual(
            [note.id for note in self.store.list_notes(collection="biology")], [first.id]
        )

    def test_update_changes_editable_fields_without_removing_source(self):
        note = self.store.create_note(
            "First title", "First body", tags=("one",), source=self._source()
        )

        updated = self.store.update_note(
            note.id, "Updated title", "Updated body", tags="two, three",
            collection="Class notes",
        )

        self.assertEqual(updated.title, "Updated title")
        self.assertEqual(updated.body, "Updated body")
        self.assertEqual(updated.tags, ("three", "two"))
        self.assertEqual(updated.collection, "Class notes")
        self.assertEqual(updated.source, note.source)
        self.assertGreater(updated.updated_at, note.updated_at)

    def test_delete_removes_note_tags_and_source_reference(self):
        note = self.store.create_note(
            "Remove me", "Body", tags=("private",), source=self._source()
        )
        self.store.delete_note(note.id)

        self.assertIsNone(self.store.get_note(note.id))
        with sqlite3.connect(self.database_path) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM source_references WHERE note_id = ?",
                    (note.id,),
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM note_tags WHERE note_id = ?", (note.id,)
                ).fetchone()[0],
                0,
            )

    def test_validation_rejects_empty_titles_and_unknown_updates(self):
        with self.assertRaisesRegex(ValueError, "title is required"):
            self.store.create_note("  ")
        with self.assertRaisesRegex(KeyError, "Note not found"):
            self.store.update_note("missing", "Title", "Body")
        with self.assertRaisesRegex(KeyError, "Note not found"):
            self.store.delete_note("missing")

    def test_newer_database_schema_fails_explicitly(self):
        future_path = self.directory / "future.sqlite3"
        with sqlite3.connect(future_path) as connection:
            connection.execute("PRAGMA user_version = 999")

        with self.assertRaisesRegex(RuntimeError, "newer than supported"):
            NotesStore(future_path)

    def test_create_list_and_delete_highlights_and_bookmarks(self):
        text_source = self._source()
        highlight = self.store.create_annotation(text_source, "highlight")
        pdf_path = self.directory / "course.pdf"
        pdf_path.write_bytes(b"PDF source")
        pdf_source = self._source(
            page_index=2,
            rect=(12.5, 20.0, 130.0, 48.5),
            document_path=pdf_path,
        )
        bookmark = self.store.create_annotation(pdf_source, "bookmark")

        reopened = NotesStore(self.database_path)
        annotations = reopened.list_annotations()

        self.assertEqual(len(annotations), 2)
        self.assertTrue(all(isinstance(annotation, Annotation) for annotation in annotations))
        self.assertEqual(
            {annotation.id: annotation.kind for annotation in annotations},
            {highlight.id: "highlight", bookmark.id: "bookmark"},
        )
        self.assertEqual(
            reopened.list_annotations(document_path=self.document_path, kind="highlight")[0],
            highlight,
        )
        self.assertEqual(
            reopened.list_annotations(
                fingerprint=pdf_source.fingerprint, kind="bookmark"
            )[0],
            bookmark,
        )
        self.assertEqual(bookmark.source.document_format, "PDF")
        self.assertEqual(bookmark.source.rect, pdf_source.rect)
        reopened.delete_annotation(highlight.id)
        self.assertEqual(
            [item.id for item in reopened.list_annotations()], [bookmark.id]
        )

    def test_annotation_schema_migrates_existing_notes_database(self):
        existing_note = self.store.create_note("Existing note", "Keep this note")
        with sqlite3.connect(self.database_path) as connection:
            connection.execute("DROP INDEX annotations_document_idx")
            connection.execute("DROP TABLE annotations")
            connection.execute("DROP INDEX study_sets_updated_idx")
            connection.execute("DROP TABLE study_sets")
            connection.execute("DROP INDEX flashcard_decks_updated_idx")
            connection.execute("DROP TABLE flashcard_decks")
            connection.execute("PRAGMA user_version = 1")

        migrated = NotesStore(self.database_path)

        with sqlite3.connect(self.database_path) as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            annotation_table = connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'annotations'"
            ).fetchone()
        self.assertEqual(version, NotesStore.SCHEMA_VERSION)
        self.assertIsNotNone(annotation_table)
        self.assertEqual(migrated.get_note(existing_note.id).body, "Keep this note")
        note = migrated.create_note("Still working")
        self.assertIsNotNone(migrated.get_note(note.id))

    def test_highlights_are_filtered_to_the_exact_document_version(self):
        old_source = self._source()
        annotation = self.store.create_annotation(old_source, "highlight")
        self.document_path.write_text("A revised source with new contents.", encoding="utf-8")
        new_source = self._source()

        self.assertNotEqual(old_source.fingerprint, new_source.fingerprint)
        self.assertEqual(
            self.store.list_annotations(
                self.document_path, new_source.fingerprint, "highlight"
            ),
            [],
        )
        self.assertEqual(
            self.store.list_annotations(
                self.document_path, old_source.fingerprint, "highlight"
            )[0].id,
            annotation.id,
        )

    def test_annotation_validation_rejects_missing_locators_and_unknown_kind(self):
        source_without_offsets = SourceReference(
            document_path=str(self.document_path),
            document_format="TXT",
            fingerprint=fingerprint_file(self.document_path),
            location="Character 1",
            text="The heart",
        )

        with self.assertRaisesRegex(ValueError, "character offsets"):
            self.store.create_annotation(source_without_offsets, "highlight")
        with self.assertRaisesRegex(ValueError, "kind must be"):
            self.store.create_annotation(self._source(), "comment")

    def test_study_set_persists_questions_source_and_attempt_answers(self):
        source = self._source()
        questions = (
            StudyQuestion(
                prompt="What pumps blood?",
                kind="multiple_choice",
                options=("The heart", "The lungs", "The liver"),
                answer="The heart",
                explanation="The passage identifies the heart as the pump.",
            ),
            StudyQuestion(
                prompt="What does it pump?",
                kind="short_answer",
                options=(),
                answer="Blood",
                explanation="The passage says the heart pumps blood.",
            ),
        )
        study_set = self.store.create_study_set(
            "Heart review", "medium", questions, source
        )

        reopened = NotesStore(self.database_path)
        loaded = reopened.get_study_set(study_set.id)
        self.assertEqual(loaded, study_set)
        self.assertEqual(loaded.answers, (None, None))
        with sqlite3.connect(self.database_path) as connection:
            question_json = connection.execute(
                "SELECT questions_json FROM study_sets WHERE id = ?",
                (study_set.id,),
            ).fetchone()[0]
        self.assertNotIn("The heart pumps blood", question_json)
        self.assertIn("The heart", question_json)

        updated = reopened.save_study_set_answer(study_set.id, 0, "The heart")
        self.assertEqual(updated.answers, ("The heart", None))
        self.assertEqual(
            reopened.list_study_sets()[0].answers, ("The heart", None)
        )
        reopened.delete_study_set(study_set.id)
        self.assertEqual(reopened.list_study_sets(), [])

    def test_study_set_storage_migrates_existing_annotation_database(self):
        with sqlite3.connect(self.database_path) as connection:
            connection.execute("DROP INDEX study_sets_updated_idx")
            connection.execute("DROP TABLE study_sets")
            connection.execute("DROP INDEX flashcard_decks_updated_idx")
            connection.execute("DROP TABLE flashcard_decks")
            connection.execute("PRAGMA user_version = 2")
        migrated = NotesStore(self.database_path)

        with sqlite3.connect(self.database_path) as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(study_sets)")
            }
        self.assertEqual(version, NotesStore.SCHEMA_VERSION)
        self.assertTrue({"questions_json", "answers_json", "source_json"} <= columns)
        self.assertEqual(migrated.list_study_sets(), [])

    def test_study_set_rejects_invalid_difficulty_and_response_index(self):
        question = StudyQuestion(
            prompt="Question?",
            kind="short_answer",
            options=(),
            answer="Answer",
            explanation="Evidence.",
        )
        with self.assertRaisesRegex(ValueError, "Difficulty must be"):
            self.store.create_study_set(
                "Invalid", "expert", (question,), self._source()
            )
        study_set = self.store.create_study_set(
            "Valid", "easy", (question,), self._source()
        )
        with self.assertRaises(IndexError):
            self.store.save_study_set_answer(study_set.id, 2, "Response")

    def test_flashcard_deck_persists_source_and_review_progress(self):
        source = self._source()
        cards = (
            Flashcard(
                front="What does the heart pump?",
                back="Blood",
                explanation="The passage says the heart pumps blood.",
            ),
            Flashcard(
                front="Where does it pump blood?",
                back="Through the body",
                explanation="This is stated in the selected passage.",
            ),
        )
        deck = self.store.create_flashcard_deck("Heart", cards, source)
        reopened = NotesStore(self.database_path)
        self.assertEqual(reopened.get_flashcard_deck(deck.id), deck)
        self.assertEqual(deck.reviewed, (False, False))

        updated = reopened.set_flashcard_reviewed(deck.id, 1, True)
        self.assertEqual(updated.reviewed, (False, True))
        self.assertEqual(
            reopened.list_flashcard_decks()[0].source.fingerprint,
            source.fingerprint,
        )
        reopened.delete_flashcard_deck(deck.id)
        self.assertEqual(reopened.list_flashcard_decks(), [])

    def test_flashcard_deck_storage_migrates_version_three_database(self):
        with sqlite3.connect(self.database_path) as connection:
            connection.execute("DROP INDEX flashcard_decks_updated_idx")
            connection.execute("DROP TABLE flashcard_decks")
            connection.execute("PRAGMA user_version = 3")

        migrated = NotesStore(self.database_path)

        with sqlite3.connect(self.database_path) as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(flashcard_decks)")
            }
        self.assertEqual(version, NotesStore.SCHEMA_VERSION)
        self.assertTrue({"cards_json", "reviewed_json", "source_json"} <= columns)
        self.assertEqual(migrated.list_flashcard_decks(), [])

    def test_flashcard_store_rejects_bad_review_values_and_card_indexes(self):
        deck = self.store.create_flashcard_deck(
            "Heart",
            [Flashcard("Front", "Back", "Evidence.")],
            self._source(),
        )
        with self.assertRaises(ValueError):
            self.store.set_flashcard_reviewed(deck.id, 0, "yes")
        with self.assertRaises(ValueError):
            self.store.record_flashcard_review(deck.id, 0, "maybe")
        with self.assertRaises(IndexError):
            self.store.set_flashcard_reviewed(deck.id, 1, True)

    def test_flashcard_ratings_schedule_recurrence_and_remove_card_from_due_queue(self):
        deck = self.store.create_flashcard_deck(
            "Heart",
            [
                Flashcard("Front one", "Back one", "Evidence."),
                Flashcard("Front two", "Back two", "Evidence."),
            ],
            self._source(),
        )
        self.assertEqual(len(self.store.list_due_flashcards()), 2)

        deck = self.store.record_flashcard_review(deck.id, 0, "good")
        self.assertEqual(deck.schedules[0].interval_days, 1)
        self.assertEqual(deck.schedules[0].repetitions, 1)
        self.assertIsNotNone(deck.schedules[0].last_reviewed_at)
        due_cards = self.store.list_due_flashcards()
        self.assertEqual([(item.id, index) for item, index in due_cards], [(deck.id, 1)])

        deck = self.store.record_flashcard_review(deck.id, 0, "good")
        self.assertEqual(deck.schedules[0].interval_days, 6)
        deck = self.store.record_flashcard_review(deck.id, 0, "again")
        self.assertEqual(deck.schedules[0].repetitions, 0)
        self.assertAlmostEqual(deck.schedules[0].interval_days, 1 / 144)
        self.assertGreater(
            datetime.fromisoformat(deck.schedules[0].due_at),
            datetime.now(timezone.utc),
        )

        reopened = NotesStore(self.database_path)
        self.assertEqual(reopened.get_flashcard_deck(deck.id), deck)

    def test_due_flashcards_include_multiple_decks_and_exclude_rescheduled_cards(self):
        source = self._source()
        card = Flashcard("Front", "Back", "Evidence.")
        first = self.store.create_flashcard_deck("Alpha", [card], source)
        second = self.store.create_flashcard_deck("Beta", [card], source)

        due_cards = self.store.list_due_flashcards()
        self.assertEqual(
            [(deck.id, index) for deck, index in due_cards],
            [(first.id, 0), (second.id, 0)],
        )

        self.store.record_flashcard_review(first.id, 0, "easy")
        self.assertEqual(
            [(deck.id, index) for deck, index in self.store.list_due_flashcards()],
            [(second.id, 0)],
        )

    def test_v4_flashcard_migration_preserves_cards_and_marks_them_due(self):
        deck = self.store.create_flashcard_deck(
            "Heart",
            [Flashcard("Front", "Back", "Evidence.")],
            self._source(),
        )
        self.store.set_flashcard_reviewed(deck.id, 0, True)
        with sqlite3.connect(self.database_path) as connection:
            connection.execute("ALTER TABLE flashcard_decks DROP COLUMN schedule_json")
            connection.execute("PRAGMA user_version = 4")

        migrated = NotesStore(self.database_path)
        loaded = migrated.get_flashcard_deck(deck.id)
        self.assertEqual(loaded.cards, deck.cards)
        self.assertEqual(loaded.reviewed, (True,))
        self.assertEqual(len(loaded.schedules), 1)
        self.assertEqual(
            [(item.id, index) for item, index in migrated.list_due_flashcards()],
            [(deck.id, 0)],
        )

    @unittest.skipUnless(os.name == "posix", "POSIX file permissions are unavailable")
    def test_database_and_private_directory_have_restricted_permissions(self):
        self.assertEqual(self.database_path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.database_path.parent.stat().st_mode & 0o777, 0o700)


if __name__ == "__main__":
    unittest.main()
