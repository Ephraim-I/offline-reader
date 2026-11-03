from __future__ import annotations

import logging
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

import pymupdf

_HIGHLIGHT_TOKEN_RE = re.compile(r"[\w'-]+", re.UNICODE)
_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReadingContext:
    selected_text: str
    surrounding_text: str
    document_title: str
    document_format: str
    location: str
    char_start: int | None = None
    char_end: int | None = None
    page_index: int | None = None
    selection_rect: tuple[float, float, float, float] | None = None


@dataclass(frozen=True)
class RenderedPage:
    png_bytes: bytes
    width: int
    height: int


def _sentence_bounds(text: str, start: int, end: int) -> tuple[int, int]:
    """Return a compact sentence-like window around ``start:end``."""
    start_boundary = max(
        text.rfind(".", 0, start),
        text.rfind("!", 0, start),
        text.rfind("?", 0, start),
        text.rfind("\n", 0, start),
    ) + 1
    end_candidates = [
        position
        for position in (
            text.find(".", end),
            text.find("!", end),
            text.find("?", end),
            text.find("\n", end),
        )
        if position >= 0
    ]
    end_boundary = min(end_candidates, default=len(text) - 1)
    if end_boundary < len(text) - 1 and text[end_boundary] in ".!?":
        end_boundary += 1
    return start_boundary, end_boundary


@dataclass(frozen=True)
class TextDocument:
    path: Path
    content: str

    @property
    def title(self) -> str:
        return self.path.name

    @property
    def format(self) -> str:
        return "TXT"

    def context_for_selection(self, selected_text: str, start_offset: int) -> ReadingContext:
        """Build context from the exact selected range, never a guessed match.

        Tk's Text widget can return whitespace around a selection. Validate the
        raw range first, then normalize only the text sent to the assistant.
        The offset remains tied to the actual selection, so duplicate phrases
        cannot silently resolve to a different occurrence.
        """
        if not isinstance(selected_text, str) or not selected_text.strip():
            raise ValueError("Selected text must be a non-empty string.")
        if not isinstance(start_offset, int) or start_offset < 0:
            raise ValueError("Selection offset must be a non-negative integer.")
        if start_offset > len(self.content):
            raise ValueError("Selection offset is outside the document.")

        leading_whitespace = len(selected_text) - len(selected_text.lstrip())
        normalized_text = selected_text.strip()
        actual_offset = start_offset + leading_whitespace
        if self.content[actual_offset : actual_offset + len(normalized_text)] != normalized_text:
            raise ValueError(
                "The selected text no longer matches this document position. "
                "Please select the text again."
            )

        end_offset = actual_offset + len(normalized_text)
        start, end = _sentence_bounds(self.content, actual_offset, end_offset)
        return ReadingContext(
            selected_text=normalized_text,
            surrounding_text=self.content[start:end].strip(),
            document_title=self.title,
            document_format=self.format,
            location=f"Character {actual_offset + 1}",
            char_start=actual_offset,
            char_end=end_offset,
        )


class PDFDocument:
    def __init__(self, path: Path, document: pymupdf.Document):
        self.path = path
        self._document = document

    @property
    def title(self) -> str:
        return self.path.name

    @property
    def format(self) -> str:
        return "PDF"

    @property
    def page_count(self) -> int:
        return self._document.page_count

    def _page(self, page_index: int):
        if not isinstance(page_index, int) or not 0 <= page_index < self.page_count:
            raise IndexError(f"PDF page index must be between 0 and {self.page_count - 1}.")
        return self._document[page_index]

    def page_width(self, page_index: int) -> float:
        return self._page(page_index).rect.width

    def close(self) -> None:
        self._document.close()

    def find_text_rects(self, page_index: int, text: str) -> list[tuple[float, float, float, float]]:
        """Find source rectangles using phrase search and normalized word runs."""
        if not isinstance(text, str) or not text.strip():
            return []
        page = self._page(page_index)
        words = text.split()
        for count in (10, 8, 6, 4, 3, 2):
            phrase = " ".join(words[:count]).strip()
            if not phrase:
                continue
            try:
                rectangles = page.search_for(phrase, quads=False)
            except Exception:
                rectangles = []
            if rectangles:
                return [tuple(float(value) for value in rect) for rect in rectangles[:8]]

        page_words = page.get_text("words", sort=True)
        rectangles = self._match_word_rectangles(page_words, text)
        if rectangles:
            return rectangles

        from app_config import OCR_DPI, OCR_ENABLED, OCR_LANGUAGE

        if OCR_ENABLED and not page_words:
            if not shutil.which("tesseract"):
                _LOGGER.warning(
                    "Cannot OCR a scanned PDF source passage because Tesseract is unavailable."
                )
                return []
            try:
                text_page = page.get_textpage_ocr(
                    language=OCR_LANGUAGE, dpi=OCR_DPI, full=True
                )
                page_words = page.get_text(
                    "words", textpage=text_page, sort=True
                )
            except (OSError, RuntimeError, ValueError) as error:
                _LOGGER.warning("OCR failed while locating a PDF source passage: %s", error)
                return []
            return self._match_word_rectangles(page_words, text)
        return []

    @staticmethod
    def _match_word_rectangles(page_words, text: str) -> list[tuple[float, float, float, float]]:
        target = [token.casefold() for token in _HIGHLIGHT_TOKEN_RE.findall(text)]
        if not target:
            return []

        flattened: list[tuple[str, int]] = []
        for word_index, word in enumerate(page_words):
            flattened.extend(
                (token.casefold(), word_index)
                for token in _HIGHLIGHT_TOKEN_RE.findall(str(word[4]))
            )

        match_start = None
        match_length = 0
        for start in range(len(flattened)):
            matched = 0
            while (
                matched < min(12, len(target))
                and start + matched < len(flattened)
                and flattened[start + matched][0] == target[matched]
            ):
                matched += 1
            if matched > match_length:
                match_start = start
                match_length = matched
        minimum_match = 1 if len(target) == 1 and len(target[0]) >= 6 else 2
        if match_start is None or match_length < minimum_match:
            return []

        matched_word_indexes = {
            flattened[position][1]
            for position in range(match_start, match_start + match_length)
        }
        line_bounds: dict[tuple[int, int], list[float]] = {}
        for word_index in matched_word_indexes:
            word = page_words[word_index]
            key = (int(word[5]), int(word[6]))
            bounds = line_bounds.setdefault(key, [float(word[0]), float(word[1]), float(word[2]), float(word[3])])
            bounds[0] = min(bounds[0], float(word[0]))
            bounds[1] = min(bounds[1], float(word[1]))
            bounds[2] = max(bounds[2], float(word[2]))
            bounds[3] = max(bounds[3], float(word[3]))
        return [tuple(bounds) for _line, bounds in sorted(
            line_bounds.items(), key=lambda item: (item[0][0], item[0][1])
        )[:8]]

    def render_page(self, page_index: int, scale: float) -> RenderedPage:
        if not isinstance(scale, (int, float)) or scale <= 0:
            raise ValueError("Page scale must be greater than zero.")
        page = self._page(page_index)
        pixmap = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
        return RenderedPage(pixmap.tobytes("png"), pixmap.width, pixmap.height)

    def context_for_rectangle(
        self,
        page_index: int,
        left: float,
        top: float,
        right: float,
        bottom: float,
    ) -> ReadingContext | None:
        page = self._page(page_index)
        rectangle = pymupdf.Rect(
            min(left, right), min(top, bottom), max(left, right), max(top, bottom)
        )
        if rectangle.width <= 0:
            return None
        # A word merely touching the drag rectangle should not be selected.
        # Use word centers, which avoids pulling adjacent words in when the
        # rectangle clips their edges. A small vertical tolerance supports the
        # natural left-to-right drag users make when selecting a single line.
        if rectangle.height < 6:
            center_y = (rectangle.y0 + rectangle.y1) / 2
            rectangle.y0 = center_y - 6
            rectangle.y1 = center_y + 6
        if rectangle.is_empty:
            return None

        all_words = page.get_text("words", sort=True)
        selected_words = []
        for word in all_words:
            word_rectangle = pymupdf.Rect(word[:4])
            center_x = (word_rectangle.x0 + word_rectangle.x1) / 2
            center_y = (word_rectangle.y0 + word_rectangle.y1) / 2
            if rectangle.x0 <= center_x <= rectangle.x1 and rectangle.y0 <= center_y <= rectangle.y1:
                selected_words.append(word)

        # Keep line breaks in the selected passage so the model receives a
        # faithful reading of multi-line selections rather than one long line.
        lines = []
        previous_line = None
        current_line = []
        for word in selected_words:
            line_key = (word[5], word[6])
            if previous_line is not None and line_key != previous_line:
                lines.append(" ".join(current_line))
                current_line = []
            current_line.append(word[4])
            previous_line = line_key
        if current_line:
            lines.append(" ".join(current_line))
        selected_text = "\n".join(lines).strip()
        if not selected_text:
            return None

        # Build nearby context from geometrically adjacent lines in the same
        # column. PDF extraction blocks can span columns, so using the whole
        # block may accidentally mix unrelated text from the neighboring column.
        line_groups = {}
        for word in all_words:
            line_groups.setdefault((word[5], word[6]), []).append(word)
        selected_line_keys = {(word[5], word[6]) for word in selected_words}
        selected_lines = [line_groups[key] for key in selected_line_keys if key in line_groups]
        anchor_x0 = min(word[0] for line in selected_lines for word in line)
        anchor_x1 = max(word[2] for line in selected_lines for word in line)
        anchor_y0 = min(word[1] for line in selected_lines for word in line)
        anchor_y1 = max(word[3] for line in selected_lines for word in line)
        anchor_width = max(1.0, anchor_x1 - anchor_x0)
        anchor_center_x = (anchor_x0 + anchor_x1) / 2
        anchor_center_y = (anchor_y0 + anchor_y1) / 2

        contextual_lines = []
        for line in line_groups.values():
            line_x0 = min(word[0] for word in line)
            line_x1 = max(word[2] for word in line)
            line_y0 = min(word[1] for word in line)
            line_y1 = max(word[3] for word in line)
            line_center_x = (line_x0 + line_x1) / 2
            line_center_y = (line_y0 + line_y1) / 2
            horizontal_overlap = max(0.0, min(anchor_x1, line_x1) - max(anchor_x0, line_x0))
            overlap_ratio = horizontal_overlap / min(anchor_width, max(1.0, line_x1 - line_x0))
            same_column = overlap_ratio >= 0.15 or abs(line_center_x - anchor_center_x) < 100
            near_selection = abs(line_center_y - anchor_center_y) <= 85
            if same_column and near_selection:
                contextual_lines.append((line_y0, line_x0, " ".join(word[4] for word in sorted(line, key=lambda item: item[0]))))
        contextual_lines.sort(key=lambda item: (item[0], item[1]))
        # Prefer the sentence on the actual selected line. This prevents a
        # repeated word elsewhere in the same extracted line from shifting
        # context to the wrong occurrence.
        selected_line = selected_lines[0]
        selected_line_sorted = sorted(selected_line, key=lambda item: item[0])
        line_text_parts = []
        selected_start = None
        selected_end = None
        cursor = 0
        for word in selected_line_sorted:
            if line_text_parts:
                line_text_parts.append(" ")
                cursor += 1
            word_start = cursor
            line_text_parts.append(word[4])
            cursor += len(word[4])
            if word in selected_words:
                selected_start = word_start if selected_start is None else min(selected_start, word_start)
                selected_end = cursor
        selected_line_text = "".join(line_text_parts)
        if selected_start is not None and selected_end is not None:
            sentence_start, sentence_end = _sentence_bounds(selected_line_text, selected_start, selected_end)
            surrounding_text = selected_line_text[sentence_start:sentence_end].strip()
        else:
            surrounding_text = ""
        # If a selected sentence is unusually short, include nearby lines from
        # the same column, but never pull the full page/block indiscriminately.
        if len(surrounding_text) < 55:
            nearby = [item[2] for item in contextual_lines if item[2] != selected_line_text]
            if nearby:
                surrounding_text = "\n".join((nearby[-1], surrounding_text, nearby[0])).strip()
        if not surrounding_text:
            surrounding_text = selected_text

        return ReadingContext(
            selected_text=selected_text,
            surrounding_text=surrounding_text,
            document_title=self.title,
            document_format=self.format,
            location=f"Page {page_index + 1}",
            page_index=page_index,
            selection_rect=(rectangle.x0, rectangle.y0, rectangle.x1, rectangle.y1),
        )
