from pathlib import Path
import threading

from ai.assistant import Answer, Explanation, LocalReaderAssistant
from ai.study_tools import generate_flashcards, generate_quiz
from documents.manager import DocumentManager
from documents.models import PDFDocument, ReadingContext, TextDocument
from documents.retrieval import DocumentSearchIndex, Passage


class ReaderController:
    def __init__(self):
        self.documents = DocumentManager()
        self.assistant = LocalReaderAssistant()
        self.document: TextDocument | PDFDocument | None = None
        self.search_index: DocumentSearchIndex | None = None
        self._search_lock = threading.Lock()
        # Changes whenever the active document changes; workers use this to
        # avoid publishing results from a document that is no longer active.
        self._document_generation = 0

    def open_document(self, path: Path) -> TextDocument | PDFDocument:
        # Open first, then replace the active document. If the new file is
        # invalid or unreadable, the current document remains usable.
        new_document = self.documents.open(path)
        with self._search_lock:
            previous_document = self.document
            self._document_generation += 1
            if isinstance(previous_document, PDFDocument):
                previous_document.close()
            self.document = new_document
            self.search_index = None  # Built by search workers outside this lock.
        return new_document

    def close_document(self) -> None:
        with self._search_lock:
            self._document_generation += 1
            if isinstance(self.document, PDFDocument):
                self.document.close()
            self.document = None
            self.search_index = None

    def text_context(self, selected_text: str, start_offset: int) -> ReadingContext:
        if not isinstance(self.document, TextDocument):
            raise RuntimeError("The active document is not text.")
        return self.document.context_for_selection(selected_text, start_offset)

    def pdf_context(
        self,
        page_index: int,
        left: float,
        top: float,
        right: float,
        bottom: float,
    ) -> ReadingContext | None:
        if not isinstance(self.document, PDFDocument):
            raise RuntimeError("The active document is not a PDF.")
        return self.document.context_for_rectangle(page_index, left, top, right, bottom)

    def search_document(self, query: str, limit: int = 6) -> list[Passage]:
        # Never build a potentially expensive index while holding the lock used
        # by open_document/close_document. Otherwise switching documents can
        # stall the UI while a background search is indexing a large PDF.
        with self._search_lock:
            document = self.document
            generation = self._document_generation
            index = self.search_index
        if document is None:
            return []

        if index is None:
            candidate = DocumentSearchIndex(document)
            with self._search_lock:
                # A different document may have become active during indexing.
                # Do not install an index for that stale document.
                if generation != self._document_generation or document is not self.document:
                    return []
                if self.search_index is None:
                    self.search_index = candidate
                index = self.search_index

        results = index.search(query, limit=limit)
        with self._search_lock:
            # Results from an old document must not be surfaced after a switch.
            if generation != self._document_generation or document is not self.document:
                return []
        return results

    def explain(self, context: ReadingContext) -> Explanation:
        return self.assistant.explain(context)

    def answer(self, question: str, context: ReadingContext) -> Answer:
        # The query includes the question and selected passage, but only a few
        # ranked excerpts are sent to the model, never the whole document.
        sources = self.search_document(f"{question}\n{context.selected_text}", limit=5)
        return self.assistant.answer(question, context, sources=sources)

    def generate_revision_notes(self, context: ReadingContext):
        return self.assistant.generate_revision_notes(context)

    def generate_quiz(self, context: ReadingContext, count: int, difficulty: str, kind: str):
        return generate_quiz(context, count, difficulty, kind)

    def generate_flashcards(self, context: ReadingContext, count: int):
        return generate_flashcards(context, count)
