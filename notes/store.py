"""SQLite persistence for local notes and document source references."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path


def default_notes_path() -> Path:
    data_home = os.environ.get("XDG_DATA_HOME")
    base = Path(data_home).expanduser() if data_home else Path.home() / ".local" / "share"
    return base / "offline-reader" / "notes.sqlite3"


def fingerprint_file(path: Path) -> str:
    """Return a SHA-256 identity for the exact document bytes."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as document:
        for block in iter(lambda: document.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@dataclass(frozen=True)
class SourceReference:
    document_path: str
    document_format: str
    fingerprint: str
    location: str
    text: str
    char_start: int | None = None
    char_end: int | None = None
    page_index: int | None = None
    rect: tuple[float, float, float, float] | None = None


@dataclass(frozen=True)
class Note:
    id: str
    title: str
    body: str
    created_at: str
    updated_at: str
    tags: tuple[str, ...] = ()
    collection: str | None = None
    source: SourceReference | None = None


@dataclass(frozen=True)
class Annotation:
    id: str
    kind: str
    source: SourceReference
    created_at: str


@dataclass(frozen=True)
class StudyQuestion:
    prompt: str
    kind: str
    options: tuple[str, ...]
    answer: str
    explanation: str


@dataclass(frozen=True)
class StudySet:
    id: str
    title: str
    difficulty: str
    questions: tuple[StudyQuestion, ...]
    answers: tuple[str | None, ...]
    source: SourceReference
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class Flashcard:
    front: str
    back: str
    explanation: str


@dataclass(frozen=True)
class FlashcardSchedule:
    due_at: str
    interval_days: float
    ease_factor: float
    repetitions: int
    last_reviewed_at: str | None = None


@dataclass(frozen=True)
class FlashcardDeck:
    id: str
    title: str
    cards: tuple[Flashcard, ...]
    reviewed: tuple[bool, ...]
    schedules: tuple[FlashcardSchedule, ...]
    source: SourceReference
    created_at: str
    updated_at: str


def _next_flashcard_schedule(
    previous: FlashcardSchedule, rating: str, now: datetime
) -> FlashcardSchedule:
    ease = previous.ease_factor
    repetitions = previous.repetitions
    if rating == "again":
        interval_days = 1 / 144
        repetitions = 0
    elif rating == "hard":
        interval_days = max(1.0, previous.interval_days * 1.2)
        ease = max(1.3, ease - 0.15)
        repetitions += 1
    elif rating == "good":
        interval_days = (
            1.0 if repetitions == 0
            else 6.0 if repetitions == 1
            else max(1.0, round(previous.interval_days * ease))
        )
        repetitions += 1
    elif rating == "easy":
        interval_days = (
            4.0
            if repetitions == 0
            else max(1.0, round(previous.interval_days * (ease + 0.3)))
        )
        ease += 0.15
        repetitions += 1
    else:
        raise ValueError("Rating must be again, hard, good, or easy.")

    due_at = now + timedelta(days=interval_days)
    return FlashcardSchedule(
        due_at=due_at.isoformat(timespec="microseconds"),
        interval_days=interval_days,
        ease_factor=ease,
        repetitions=repetitions,
        last_reviewed_at=now.isoformat(timespec="microseconds"),
    )


class NotesStore:
    """Small per-operation SQLite connections keep the store safe across UI workers."""

    SCHEMA_VERSION = 5

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path is not None else default_notes_path()
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if os.name == "posix":
            os.chmod(self.path.parent, 0o700)
        self._initialize()
        if os.name == "posix":
            os.chmod(self.path, 0o600)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connection() as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version > self.SCHEMA_VERSION:
                raise RuntimeError(
                    f"Notes database version {version} is newer than supported "
                    f"version {self.SCHEMA_VERSION}."
                )
            if version == 0:
                connection.executescript(
                    """
                    CREATE TABLE documents (
                        id TEXT PRIMARY KEY,
                        path TEXT NOT NULL,
                        format TEXT NOT NULL,
                        fingerprint TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        UNIQUE (path, fingerprint)
                    );
                    CREATE TABLE collections (
                        id TEXT PRIMARY KEY,
                        name TEXT NOT NULL UNIQUE COLLATE NOCASE
                    );
                    CREATE TABLE notes (
                        id TEXT PRIMARY KEY,
                        title TEXT NOT NULL,
                        body TEXT NOT NULL DEFAULT '',
                        collection_id TEXT REFERENCES collections(id) ON DELETE SET NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    CREATE TABLE note_tags (
                        note_id TEXT NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
                        tag TEXT NOT NULL,
                        PRIMARY KEY (note_id, tag)
                    );
                    CREATE TABLE source_references (
                        id TEXT PRIMARY KEY,
                        note_id TEXT NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
                        document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE RESTRICT,
                        location TEXT NOT NULL,
                        quote TEXT NOT NULL,
                        char_start INTEGER,
                        char_end INTEGER,
                        page_index INTEGER,
                        rect_json TEXT
                    );
                    CREATE INDEX notes_updated_idx ON notes(updated_at DESC);
                    CREATE INDEX note_tags_tag_idx ON note_tags(tag);
                    PRAGMA user_version = 1;
                    """
                )
                version = 1
            if version == 1:
                connection.executescript(
                    """
                    CREATE TABLE annotations (
                        id TEXT PRIMARY KEY,
                        document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                        kind TEXT NOT NULL CHECK (kind IN ('highlight', 'bookmark')),
                        location TEXT NOT NULL,
                        quote TEXT NOT NULL,
                        char_start INTEGER,
                        char_end INTEGER,
                        page_index INTEGER,
                        rect_json TEXT,
                        created_at TEXT NOT NULL
                    );
                    CREATE INDEX annotations_document_idx
                        ON annotations(document_id, kind, created_at DESC);
                    PRAGMA user_version = 2;
                    """
                )
                version = 2
            if version == 2:
                connection.executescript(
                    """
                    CREATE TABLE study_sets (
                        id TEXT PRIMARY KEY,
                        document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                        title TEXT NOT NULL,
                        difficulty TEXT NOT NULL,
                        questions_json TEXT NOT NULL,
                        answers_json TEXT NOT NULL,
                        source_json TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    CREATE INDEX study_sets_updated_idx
                        ON study_sets(updated_at DESC);
                    PRAGMA user_version = 3;
                    """
                )
                version = 3
            if version == 3:
                connection.executescript(
                    """
                    CREATE TABLE flashcard_decks (
                        id TEXT PRIMARY KEY,
                        document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                        title TEXT NOT NULL,
                        cards_json TEXT NOT NULL,
                        reviewed_json TEXT NOT NULL,
                        source_json TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    CREATE INDEX flashcard_decks_updated_idx
                        ON flashcard_decks(updated_at DESC);
                    PRAGMA user_version = 4;
                    """
                )
                version = 4
            if version == 4:
                connection.execute(
                    "ALTER TABLE flashcard_decks ADD COLUMN schedule_json "
                    "TEXT NOT NULL DEFAULT '[]'"
                )
                now = self._now()
                rows = connection.execute(
                    "SELECT id, cards_json FROM flashcard_decks"
                ).fetchall()
                for row in rows:
                    card_count = len(json.loads(row["cards_json"]))
                    schedule = [
                        {
                            "due_at": now,
                            "interval_days": 0.0,
                            "ease_factor": 2.5,
                            "repetitions": 0,
                            "last_reviewed_at": None,
                        }
                        for _ in range(card_count)
                    ]
                    connection.execute(
                        "UPDATE flashcard_decks SET schedule_json = ? WHERE id = ?",
                        (json.dumps(schedule), row["id"]),
                    )
                connection.execute("PRAGMA user_version = 5")

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="microseconds")

    @staticmethod
    def _normalize_tags(tags) -> tuple[str, ...]:
        if tags is None:
            return ()
        if isinstance(tags, str):
            tags = tags.split(",")
        normalized = {
            tag.strip().lstrip("#") for tag in tags
            if isinstance(tag, str) and tag.strip().lstrip("#")
        }
        return tuple(sorted(normalized, key=str.casefold))

    @staticmethod
    def _source_from_row(row: sqlite3.Row) -> SourceReference:
        rect = tuple(json.loads(row["rect_json"])) if row["rect_json"] else None
        return SourceReference(
            document_path=row["path"],
            document_format=row["format"],
            fingerprint=row["fingerprint"],
            location=row["location"],
            text=row["quote"],
            char_start=row["char_start"],
            char_end=row["char_end"],
            page_index=row["page_index"],
            rect=rect,
        )

    def _get_note(self, connection: sqlite3.Connection, note_id: str) -> Note | None:
        row = connection.execute(
            """
            SELECT n.id, n.title, n.body, n.created_at, n.updated_at,
                   c.name AS collection
            FROM notes n
            LEFT JOIN collections c ON c.id = n.collection_id
            WHERE n.id = ?
            """,
            (note_id,),
        ).fetchone()
        if row is None:
            return None
        tags = tuple(
            tag_row["tag"] for tag_row in connection.execute(
                "SELECT tag FROM note_tags WHERE note_id = ? ORDER BY tag COLLATE NOCASE",
                (note_id,),
            )
        )
        source_row = connection.execute(
            """
            SELECT d.path, d.format, d.fingerprint, sr.location, sr.quote,
                   sr.char_start, sr.char_end, sr.page_index, sr.rect_json
            FROM source_references sr
            JOIN documents d ON d.id = sr.document_id
            WHERE sr.note_id = ?
            ORDER BY sr.rowid LIMIT 1
            """,
            (note_id,),
        ).fetchone()
        return Note(
            id=row["id"],
            title=row["title"],
            body=row["body"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            tags=tags,
            collection=row["collection"],
            source=self._source_from_row(source_row) if source_row else None,
        )

    def create_note(
        self,
        title: str,
        body: str = "",
        tags=(),
        source: SourceReference | None = None,
        collection: str | None = None,
    ) -> Note:
        clean_title = title.strip() if isinstance(title, str) else ""
        if not clean_title:
            raise ValueError("A note title is required.")
        if not isinstance(body, str):
            raise ValueError("Note body must be text.")
        note_id = str(uuid.uuid4())
        now = self._now()
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO notes
                    (id, title, body, collection_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    note_id, clean_title, body,
                    self._get_collection_id(connection, collection), now, now,
                ),
            )
            self._replace_tags(connection, note_id, tags)
            if source is not None:
                document_id = self._upsert_document(connection, source)
                connection.execute(
                    """
                    INSERT INTO source_references
                        (id, note_id, document_id, location, quote, char_start,
                         char_end, page_index, rect_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid.uuid4()), note_id, document_id, source.location,
                        source.text, source.char_start, source.char_end,
                        source.page_index,
                        json.dumps(source.rect) if source.rect is not None else None,
                    ),
                )
            note = self._get_note(connection, note_id)
        if note is None:
            raise RuntimeError("The note was created but could not be read back.")
        return note

    def update_note(
        self, note_id: str, title: str, body: str, tags=(),
        collection: str | None = None,
    ) -> Note:
        clean_title = title.strip() if isinstance(title, str) else ""
        if not clean_title:
            raise ValueError("A note title is required.")
        if not isinstance(body, str):
            raise ValueError("Note body must be text.")
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE notes SET title = ?, body = ?, collection_id = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    clean_title, body, self._get_collection_id(connection, collection),
                    self._now(), note_id,
                ),
            )
            if cursor.rowcount == 0:
                raise KeyError(f"Note not found: {note_id}")
            self._replace_tags(connection, note_id, tags)
            note = self._get_note(connection, note_id)
        if note is None:
            raise RuntimeError("The note was updated but could not be read back.")
        return note

    def _replace_tags(self, connection: sqlite3.Connection, note_id: str, tags) -> None:
        connection.execute("DELETE FROM note_tags WHERE note_id = ?", (note_id,))
        connection.executemany(
            "INSERT INTO note_tags (note_id, tag) VALUES (?, ?)",
            ((note_id, tag) for tag in self._normalize_tags(tags)),
        )

    @staticmethod
    def _get_collection_id(
        connection: sqlite3.Connection, collection: str | None
    ) -> str | None:
        name = collection.strip() if isinstance(collection, str) else ""
        if not name:
            return None
        connection.execute(
            "INSERT OR IGNORE INTO collections (id, name) VALUES (?, ?)",
            (str(uuid.uuid4()), name),
        )
        row = connection.execute(
            "SELECT id FROM collections WHERE name = ? COLLATE NOCASE", (name,)
        ).fetchone()
        if row is None:
            raise RuntimeError("The note collection could not be created.")
        return row["id"]

    @staticmethod
    def _upsert_document(connection: sqlite3.Connection, source: SourceReference) -> str:
        canonical_path = str(Path(source.document_path).expanduser().resolve())
        row = connection.execute(
            "SELECT id FROM documents WHERE path = ? AND fingerprint = ?",
            (canonical_path, source.fingerprint),
        ).fetchone()
        if row is not None:
            return row["id"]
        document_id = str(uuid.uuid4())
        connection.execute(
            """
            INSERT INTO documents (id, path, format, fingerprint, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                document_id, canonical_path, source.document_format,
                source.fingerprint, NotesStore._now(),
            ),
        )
        return document_id

    def get_note(self, note_id: str) -> Note | None:
        with self._connection() as connection:
            return self._get_note(connection, note_id)

    def list_notes(
        self, query: str = "", tag: str | None = None,
        collection: str | None = None,
    ) -> list[Note]:
        sql = """
            SELECT DISTINCT n.id
            FROM notes n
            LEFT JOIN collections c ON c.id = n.collection_id
        """
        parameters: list[str] = []
        conditions = []
        if tag:
            sql += " JOIN note_tags filter_tags ON filter_tags.note_id = n.id"
            conditions.append("filter_tags.tag = ? COLLATE NOCASE")
            parameters.append(tag.strip().lstrip("#"))
        if collection:
            conditions.append("c.name = ? COLLATE NOCASE")
            parameters.append(collection.strip())
        if query.strip():
            conditions.append(
                """
                (
                    instr(lower(n.title), lower(?)) > 0
                    OR instr(lower(n.body), lower(?)) > 0
                    OR EXISTS (
                        SELECT 1 FROM note_tags search_tags
                        WHERE search_tags.note_id = n.id
                          AND instr(lower(search_tags.tag), lower(?)) > 0
                    )
                    OR instr(lower(COALESCE(c.name, '')), lower(?)) > 0
                )
                """
            )
            parameters.extend((query.strip(), query.strip(), query.strip(), query.strip()))
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        sql += " ORDER BY n.updated_at DESC, n.id"
        with self._connection() as connection:
            ids = [row["id"] for row in connection.execute(sql, parameters)]
            notes = [self._get_note(connection, note_id) for note_id in ids]
        return [note for note in notes if note is not None]

    def delete_note(self, note_id: str) -> None:
        with self._connection() as connection:
            cursor = connection.execute("DELETE FROM notes WHERE id = ?", (note_id,))
            if cursor.rowcount == 0:
                raise KeyError(f"Note not found: {note_id}")

    @staticmethod
    def _annotation_from_row(row: sqlite3.Row) -> Annotation:
        rect = tuple(json.loads(row["rect_json"])) if row["rect_json"] else None
        source = SourceReference(
            document_path=row["path"],
            document_format=row["format"],
            fingerprint=row["fingerprint"],
            location=row["location"],
            text=row["quote"],
            char_start=row["char_start"],
            char_end=row["char_end"],
            page_index=row["page_index"],
            rect=rect,
        )
        return Annotation(
            id=row["id"],
            kind=row["kind"],
            source=source,
            created_at=row["created_at"],
        )

    def create_annotation(self, source: SourceReference, kind: str) -> Annotation:
        if not isinstance(kind, str) or kind not in {"highlight", "bookmark"}:
            raise ValueError("Annotation kind must be 'highlight' or 'bookmark'.")
        if not isinstance(source, SourceReference):
            raise ValueError("An annotation requires a source reference.")
        if not isinstance(source.text, str) or not source.text.strip():
            raise ValueError("An annotation requires selected source text.")
        if source.document_format not in {"TXT", "PDF"}:
            raise ValueError("Annotations are supported for TXT and PDF documents.")
        if source.document_format == "TXT" and (
            source.char_start is None or source.char_end is None
        ):
            raise ValueError("A text annotation requires character offsets.")
        if source.document_format == "PDF" and (
            source.page_index is None or source.rect is None
        ):
            raise ValueError("A PDF annotation requires a page and selection rectangle.")

        annotation_id = str(uuid.uuid4())
        created_at = self._now()
        with self._connection() as connection:
            document_id = self._upsert_document(connection, source)
            connection.execute(
                """
                INSERT INTO annotations
                    (id, document_id, kind, location, quote, char_start, char_end,
                     page_index, rect_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    annotation_id,
                    document_id,
                    kind,
                    source.location,
                    source.text,
                    source.char_start,
                    source.char_end,
                    source.page_index,
                    json.dumps(source.rect) if source.rect is not None else None,
                    created_at,
                ),
            )
            row = connection.execute(
                """
                SELECT a.id, a.kind, d.path, d.format, d.fingerprint,
                       a.location, a.quote, a.char_start, a.char_end,
                       a.page_index, a.rect_json, a.created_at
                FROM annotations a
                JOIN documents d ON d.id = a.document_id
                WHERE a.id = ?
                """,
                (annotation_id,),
            ).fetchone()
        if row is None:
            raise RuntimeError("The annotation was saved but could not be read back.")
        return self._annotation_from_row(row)

    def list_annotations(
        self,
        document_path: str | Path | None = None,
        fingerprint: str | None = None,
        kind: str | None = None,
    ) -> list[Annotation]:
        if kind is not None and (
            not isinstance(kind, str) or kind not in {"highlight", "bookmark"}
        ):
            raise ValueError("Annotation kind must be 'highlight' or 'bookmark'.")
        query = """
            SELECT a.id, a.kind, d.path, d.format, d.fingerprint,
                   a.location, a.quote, a.char_start, a.char_end,
                   a.page_index, a.rect_json, a.created_at
            FROM annotations a
            JOIN documents d ON d.id = a.document_id
        """
        conditions = []
        parameters: list[str] = []
        if document_path is not None:
            conditions.append("d.path = ?")
            parameters.append(str(Path(document_path).expanduser().resolve()))
        if fingerprint is not None:
            conditions.append("d.fingerprint = ?")
            parameters.append(fingerprint)
        if kind is not None:
            conditions.append("a.kind = ?")
            parameters.append(kind)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY a.created_at DESC, a.id"
        with self._connection() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [self._annotation_from_row(row) for row in rows]

    def delete_annotation(self, annotation_id: str) -> None:
        with self._connection() as connection:
            cursor = connection.execute(
                "DELETE FROM annotations WHERE id = ?", (annotation_id,)
            )
            if cursor.rowcount == 0:
                raise KeyError(f"Annotation not found: {annotation_id}")

    @staticmethod
    def _study_set_from_row(row: sqlite3.Row) -> StudySet:
        questions = tuple(
            StudyQuestion(
                prompt=item["prompt"],
                kind=item["kind"],
                options=tuple(item["options"]),
                answer=item["answer"],
                explanation=item["explanation"],
            )
            for item in json.loads(row["questions_json"])
        )
        source_data = json.loads(row["source_json"])
        source = SourceReference(
            document_path=source_data["document_path"],
            document_format=source_data["document_format"],
            fingerprint=source_data["fingerprint"],
            location=source_data["location"],
            text=source_data["text"],
            char_start=source_data["char_start"],
            char_end=source_data["char_end"],
            page_index=source_data["page_index"],
            rect=tuple(source_data["rect"]) if source_data["rect"] is not None else None,
        )
        return StudySet(
            id=row["id"],
            title=row["title"],
            difficulty=row["difficulty"],
            questions=questions,
            answers=tuple(json.loads(row["answers_json"])),
            source=source,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _study_set_select_sql() -> str:
        return """
            SELECT s.id, s.title, s.difficulty, s.questions_json, s.answers_json,
                   s.source_json, s.created_at, s.updated_at
            FROM study_sets s
        """

    def create_study_set(
        self,
        title: str,
        difficulty: str,
        questions: tuple[StudyQuestion, ...] | list[StudyQuestion],
        source: SourceReference,
    ) -> StudySet:
        clean_title = title.strip() if isinstance(title, str) else ""
        if not clean_title:
            raise ValueError("A study-set title is required.")
        if difficulty not in {"easy", "medium", "hard"}:
            raise ValueError("Difficulty must be easy, medium, or hard.")
        if not isinstance(source, SourceReference):
            raise ValueError("A study set requires a source reference.")
        if not questions or len(questions) > 10:
            raise ValueError("A study set must contain between 1 and 10 questions.")
        if any(not isinstance(question, StudyQuestion) for question in questions):
            raise ValueError("Study-set questions must be validated StudyQuestion values.")
        now = self._now()
        study_set_id = str(uuid.uuid4())
        question_values = [
            {
                "prompt": question.prompt,
                "kind": question.kind,
                "options": list(question.options),
                "answer": question.answer,
                "explanation": question.explanation,
            }
            for question in questions
        ]
        source_value = {
            "document_path": str(Path(source.document_path).expanduser().resolve()),
            "document_format": source.document_format,
            "fingerprint": source.fingerprint,
            "location": source.location,
            "text": source.text,
            "char_start": source.char_start,
            "char_end": source.char_end,
            "page_index": source.page_index,
            "rect": list(source.rect) if source.rect is not None else None,
        }
        with self._connection() as connection:
            document_id = self._upsert_document(connection, source)
            connection.execute(
                """
                INSERT INTO study_sets
                    (id, document_id, title, difficulty, questions_json,
                     answers_json, source_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    study_set_id,
                    document_id,
                    clean_title,
                    difficulty,
                    json.dumps(question_values, ensure_ascii=False),
                    json.dumps([None] * len(questions)),
                    json.dumps(source_value, ensure_ascii=False),
                    now,
                    now,
                ),
            )
            row = connection.execute(
                self._study_set_select_sql() + " WHERE s.id = ?", (study_set_id,)
            ).fetchone()
        if row is None:
            raise RuntimeError("The study set was saved but could not be read back.")
        return self._study_set_from_row(row)

    def get_study_set(self, study_set_id: str) -> StudySet | None:
        with self._connection() as connection:
            row = connection.execute(
                self._study_set_select_sql() + " WHERE s.id = ?", (study_set_id,)
            ).fetchone()
        return self._study_set_from_row(row) if row is not None else None

    def list_study_sets(self) -> list[StudySet]:
        with self._connection() as connection:
            rows = connection.execute(
                self._study_set_select_sql() + " ORDER BY s.updated_at DESC, s.id"
            ).fetchall()
        return [self._study_set_from_row(row) for row in rows]

    def save_study_set_answer(
        self, study_set_id: str, question_index: int, answer: str
    ) -> StudySet:
        if not isinstance(answer, str) or not answer.strip():
            raise ValueError("A submitted answer must not be empty.")
        with self._connection() as connection:
            row = connection.execute(
                "SELECT answers_json, questions_json FROM study_sets WHERE id = ?",
                (study_set_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"Study set not found: {study_set_id}")
            answers = json.loads(row["answers_json"])
            if not isinstance(question_index, int) or not 0 <= question_index < len(answers):
                raise IndexError("Question index is outside the study set.")
            answers[question_index] = answer.strip()
            connection.execute(
                """
                UPDATE study_sets SET answers_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (json.dumps(answers, ensure_ascii=False), self._now(), study_set_id),
            )
            updated = connection.execute(
                self._study_set_select_sql() + " WHERE s.id = ?", (study_set_id,)
            ).fetchone()
        if updated is None:
            raise RuntimeError("The study-set answer was saved but could not be read back.")
        return self._study_set_from_row(updated)

    def delete_study_set(self, study_set_id: str) -> None:
        with self._connection() as connection:
            cursor = connection.execute(
                "DELETE FROM study_sets WHERE id = ?", (study_set_id,)
            )
            if cursor.rowcount == 0:
                raise KeyError(f"Study set not found: {study_set_id}")

    @staticmethod
    def _flashcard_deck_from_row(row: sqlite3.Row) -> FlashcardDeck:
        cards = tuple(
            Flashcard(
                front=item["front"],
                back=item["back"],
                explanation=item["explanation"],
            )
            for item in json.loads(row["cards_json"])
        )
        source_data = json.loads(row["source_json"])
        source = SourceReference(
            document_path=source_data["document_path"],
            document_format=source_data["document_format"],
            fingerprint=source_data["fingerprint"],
            location=source_data["location"],
            text=source_data["text"],
            char_start=source_data["char_start"],
            char_end=source_data["char_end"],
            page_index=source_data["page_index"],
            rect=tuple(source_data["rect"]) if source_data["rect"] is not None else None,
        )
        schedules = tuple(
            FlashcardSchedule(**item) for item in json.loads(row["schedule_json"])
        )
        return FlashcardDeck(
            id=row["id"],
            title=row["title"],
            cards=cards,
            reviewed=tuple(json.loads(row["reviewed_json"])),
            schedules=schedules,
            source=source,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _flashcard_deck_select_sql() -> str:
        return """
            SELECT d.id, d.title, d.cards_json, d.reviewed_json,
                   d.schedule_json, d.source_json, d.created_at, d.updated_at
            FROM flashcard_decks d
        """

    def create_flashcard_deck(
        self,
        title: str,
        cards: tuple[Flashcard, ...] | list[Flashcard],
        source: SourceReference,
    ) -> FlashcardDeck:
        clean_title = title.strip() if isinstance(title, str) else ""
        if not clean_title:
            raise ValueError("A flashcard-deck title is required.")
        if not isinstance(source, SourceReference):
            raise ValueError("A flashcard deck requires a source reference.")
        if not cards or len(cards) > 10:
            raise ValueError("A flashcard deck must contain between 1 and 10 cards.")
        if any(not isinstance(card, Flashcard) for card in cards):
            raise ValueError("Deck cards must be validated Flashcard values.")
        now = self._now()
        initial_schedule = FlashcardSchedule(
            due_at=now,
            interval_days=0.0,
            ease_factor=2.5,
            repetitions=0,
        )
        deck_id = str(uuid.uuid4())
        card_values = [
            {
                "front": card.front,
                "back": card.back,
                "explanation": card.explanation,
            }
            for card in cards
        ]
        source_value = {
            "document_path": str(Path(source.document_path).expanduser().resolve()),
            "document_format": source.document_format,
            "fingerprint": source.fingerprint,
            "location": source.location,
            "text": source.text,
            "char_start": source.char_start,
            "char_end": source.char_end,
            "page_index": source.page_index,
            "rect": list(source.rect) if source.rect is not None else None,
        }
        with self._connection() as connection:
            document_id = self._upsert_document(connection, source)
            connection.execute(
                """
                INSERT INTO flashcard_decks
                    (id, document_id, title, cards_json, reviewed_json,
                     schedule_json, source_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    deck_id,
                    document_id,
                    clean_title,
                    json.dumps(card_values, ensure_ascii=False),
                    json.dumps([False] * len(cards)),
                    json.dumps(
                        [initial_schedule.__dict__ for _ in cards],
                        ensure_ascii=False,
                    ),
                    json.dumps(source_value, ensure_ascii=False),
                    now,
                    now,
                ),
            )
            row = connection.execute(
                self._flashcard_deck_select_sql() + " WHERE d.id = ?", (deck_id,)
            ).fetchone()
        if row is None:
            raise RuntimeError("The flashcard deck was saved but could not be read back.")
        return self._flashcard_deck_from_row(row)

    def get_flashcard_deck(self, deck_id: str) -> FlashcardDeck | None:
        with self._connection() as connection:
            row = connection.execute(
                self._flashcard_deck_select_sql() + " WHERE d.id = ?", (deck_id,)
            ).fetchone()
        return self._flashcard_deck_from_row(row) if row is not None else None

    def list_flashcard_decks(self) -> list[FlashcardDeck]:
        with self._connection() as connection:
            rows = connection.execute(
                self._flashcard_deck_select_sql()
                + " ORDER BY d.updated_at DESC, d.id"
            ).fetchall()
        return [self._flashcard_deck_from_row(row) for row in rows]

    def set_flashcard_reviewed(
        self, deck_id: str, card_index: int, reviewed: bool
    ) -> FlashcardDeck:
        if not isinstance(reviewed, bool):
            raise ValueError("Review status must be true or false.")
        with self._connection() as connection:
            row = connection.execute(
                "SELECT reviewed_json FROM flashcard_decks WHERE id = ?",
                (deck_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"Flashcard deck not found: {deck_id}")
            states = json.loads(row["reviewed_json"])
            if not isinstance(card_index, int) or not 0 <= card_index < len(states):
                raise IndexError("Card index is outside the flashcard deck.")
            states[card_index] = reviewed
            connection.execute(
                """
                UPDATE flashcard_decks SET reviewed_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (json.dumps(states), self._now(), deck_id),
            )
            updated = connection.execute(
                self._flashcard_deck_select_sql() + " WHERE d.id = ?", (deck_id,)
            ).fetchone()
        if updated is None:
            raise RuntimeError("The review status was saved but could not be read back.")
        return self._flashcard_deck_from_row(updated)

    def record_flashcard_review(
        self, deck_id: str, card_index: int, rating: str
    ) -> FlashcardDeck:
        if not isinstance(rating, str) or rating not in {
            "again", "hard", "good", "easy"
        }:
            raise ValueError("Rating must be again, hard, good, or easy.")
        now = datetime.now(timezone.utc)
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT reviewed_json, schedule_json FROM flashcard_decks
                WHERE id = ?
                """,
                (deck_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"Flashcard deck not found: {deck_id}")
            reviewed = json.loads(row["reviewed_json"])
            schedules = json.loads(row["schedule_json"])
            if not isinstance(card_index, int) or not 0 <= card_index < len(schedules):
                raise IndexError("Card index is outside the flashcard deck.")
            previous = FlashcardSchedule(**schedules[card_index])
            schedules[card_index] = _next_flashcard_schedule(previous, rating, now).__dict__
            reviewed[card_index] = True
            connection.execute(
                """
                UPDATE flashcard_decks
                SET reviewed_json = ?, schedule_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    json.dumps(reviewed),
                    json.dumps(schedules, ensure_ascii=False),
                    now.isoformat(timespec="microseconds"),
                    deck_id,
                ),
            )
            updated = connection.execute(
                self._flashcard_deck_select_sql() + " WHERE d.id = ?", (deck_id,)
            ).fetchone()
        if updated is None:
            raise RuntimeError("The review rating was saved but could not be read back.")
        return self._flashcard_deck_from_row(updated)

    def list_due_flashcards(
        self, now: datetime | None = None
    ) -> list[tuple[FlashcardDeck, int]]:
        current_time = now or datetime.now(timezone.utc)
        if current_time.tzinfo is None or current_time.utcoffset() is None:
            raise ValueError("The due-card query time must include a timezone.")
        due_cards = []
        for deck in self.list_flashcard_decks():
            for index, schedule in enumerate(deck.schedules):
                due_at = datetime.fromisoformat(schedule.due_at)
                if due_at <= current_time:
                    due_cards.append((due_at, deck, index))
        due_cards.sort(key=lambda item: (item[0], item[1].title.casefold(), item[2]))
        return [(deck, index) for _, deck, index in due_cards]

    def delete_flashcard_deck(self, deck_id: str) -> None:
        with self._connection() as connection:
            cursor = connection.execute(
                "DELETE FROM flashcard_decks WHERE id = ?", (deck_id,)
            )
            if cursor.rowcount == 0:
                raise KeyError(f"Flashcard deck not found: {deck_id}")
