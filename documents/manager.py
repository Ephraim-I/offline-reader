from pathlib import Path

import pymupdf

from documents.models import PDFDocument, TextDocument


class DocumentOpenError(Exception):
    """A user-safe error raised when a document cannot be opened."""


class DocumentManager:
    SUPPORTED_SUFFIXES = {".txt", ".pdf"}

    def open(self, path: Path) -> TextDocument | PDFDocument:
        path = Path(path).expanduser()
        if not path.is_file():
            raise DocumentOpenError(f"Document not found: {path}")
        suffix = path.suffix.lower()
        if suffix not in self.SUPPORTED_SUFFIXES:
            raise DocumentOpenError("Please choose a TXT or PDF file.")
        try:
            if suffix == ".txt":
                return TextDocument(path=path, content=path.read_text(encoding="utf-8-sig"))
            if suffix == ".pdf":
                document = pymupdf.open(path)
                if document.needs_pass:
                    document.close()
                    raise DocumentOpenError("Password-protected PDFs are not supported yet.")
                if document.page_count == 0:
                    document.close()
                    raise DocumentOpenError("This PDF has no pages to display.")
                return PDFDocument(path=path, document=document)
        except DocumentOpenError:
            raise
        except (OSError, UnicodeError, RuntimeError, ValueError, pymupdf.FileDataError) as error:
            raise DocumentOpenError(str(error)) from error
