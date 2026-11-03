"""Local document search for Offline Reader.

The index stores small passages and source locations, never a prompt-sized copy
of the whole document. It can combine lexical ranking with optional local
embedding similarity.
"""
from __future__ import annotations

import logging
import math
import re
import shutil
from dataclasses import dataclass
from typing import Iterable

from documents.embeddings import EmbeddingError, EmbeddingProvider, LocalEmbeddingClient

_LOGGER = logging.getLogger(__name__)
_TOKEN_RE = re.compile(r"[\w'-]+", re.UNICODE)
_MARKDOWN_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$")
_NUMBERED_HEADING_RE = re.compile(r"^\s*\d+(?:\.\d+)*(?:[.)])?\s+\D")
# Small, transparent query expansion helps a lexical index bridge common
# wording differences without requiring a downloaded embedding model.
_SYNONYMS = {
    "cause": ("reason", "because", "factor"),
    "reason": ("cause", "factor"),
    "effect": ("result", "consequence", "impact"),
    "result": ("effect", "consequence", "outcome"),
    "define": ("meaning", "definition", "describe"),
    "meaning": ("define", "definition", "explain"),
    "increase": ("rise", "grow", "higher", "greater"),
    "decrease": ("reduce", "fall", "lower", "less"),
    "method": ("approach", "technique", "process"),
    "advantage": ("benefit", "strength", "merit"),
    "disadvantage": ("drawback", "limitation", "weakness"),
    "problem": ("issue", "difficulty", "challenge"),
    "solve": ("solution", "resolve", "fix"),
    "important": ("significant", "key", "essential"),
    "similar": ("alike", "comparable", "same"),
    "difference": ("contrast", "distinction", "different"),
    "blood": ("circulation", "artery", "vein"),
    "voltage": ("potential", "electrical", "volt"),
    "current": ("amperage", "electricity", "ampere"),
    "resistance": ("resistor", "ohm", "impedance"),
}
_STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "been", "but", "by",
    "for", "from", "had", "has", "have", "how", "i", "in", "into", "is",
    "it", "its", "of", "on", "or", "that", "the", "their", "this", "to",
    "was", "were", "what", "when", "where", "which", "who", "why", "with",
}


@dataclass(frozen=True)
class Passage:
    """A searchable document excerpt with a stable location in the open file."""

    ref: str
    text: str
    location: str
    document_path: str
    document_format: str
    char_start: int | None = None
    char_end: int | None = None
    page_index: int | None = None
    section: str | None = None
    score: float = 0.0


def _normalize_token(token: str) -> str:
    """Apply conservative suffix normalization for common English inflections."""
    token = token.casefold()
    if len(token) > 5 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 5 and token.endswith("ing"):
        base = token[:-3]
        if len(base) > 2 and base[-1:] == base[-2:-1]:
            base = base[:-1]
        return base
    if len(token) > 4 and token.endswith("ed"):
        return token[:-2]
    if len(token) > 4 and token.endswith("es"):
        return token[:-2]
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def _tokens(text: str) -> list[str]:
    tokens = []
    for token in _TOKEN_RE.findall(text):
        folded = token.casefold()
        # Preserve stop words before stemming, otherwise words like "this"
        # and "was" would become accidental content terms ("thi", "wa").
        tokens.append(folded if folded in _STOP_WORDS else _normalize_token(folded))
    return tokens


def _expanded_query_terms(terms: list[str]) -> tuple[dict[str, float], set[str]]:
    """Return normalized query weights and the original terms for phrase scoring."""
    weights: dict[str, float] = {}
    original = set(terms)
    for term in terms:
        weights[term] = weights.get(term, 0.0) + 1.0
        for synonym in _SYNONYMS.get(term, ()):
            normalized = _normalize_token(synonym)
            if normalized not in original:
                weights[normalized] = max(weights.get(normalized, 0.0), 0.32)
    return weights, original


def _make_chunks(text: str, max_chars: int = 850, overlap: int = 110) -> Iterable[tuple[int, int, str]]:
    """Yield readable overlapping windows, preferring paragraph/sentence breaks."""
    if not text:
        return
    start = 0
    length = len(text)
    while start < length:
        hard_end = min(length, start + max_chars)
        end = hard_end
        if hard_end < length:
            candidates = [
                text.rfind("\n\n", start + max_chars // 2, hard_end),
                text.rfind("\n", start + max_chars // 2, hard_end),
                text.rfind(". ", start + max_chars // 2, hard_end),
                text.rfind("? ", start + max_chars // 2, hard_end),
                text.rfind("! ", start + max_chars // 2, hard_end),
            ]
            boundary = max(candidates)
            if boundary > start:
                end = boundary + (2 if text[boundary:boundary + 2] in ("\n\n", ". ", "? ", "! ") else 1)
        excerpt = text[start:end].strip()
        if excerpt:
            left_trim = len(text[start:end]) - len(text[start:end].lstrip())
            right_trim = len(text[start:end].rstrip())
            yield start + left_trim, start + right_trim, excerpt
        if end >= length:
            break
        next_start = max(start + 1, end - overlap)
        # Avoid starting the next excerpt in the middle of a word.
        while next_start < end and not text[next_start - 1].isspace():
            next_start += 1
        start = next_start


def _text_heading_lines(text: str) -> list[tuple[int, int, str]]:
    """Find explicit or clearly isolated plain-text headings."""
    lines = text.splitlines(keepends=True)
    headings: list[tuple[int, int, str]] = []
    offset = 0
    for index, raw_line in enumerate(lines):
        line = raw_line.strip()
        start = offset + len(raw_line) - len(raw_line.lstrip())
        end = offset + len(raw_line.rstrip())
        offset += len(raw_line)
        if not line or len(line) > 100 or len(line.split()) > 14:
            continue

        markdown = _MARKDOWN_HEADING_RE.match(line)
        title = markdown.group(1).strip() if markdown else line
        is_numbered = bool(_NUMBERED_HEADING_RE.match(line))
        letters = [character for character in title if character.isalpha()]
        is_uppercase = bool(letters) and all(character.isupper() for character in letters)
        previous_blank = index == 0 or not lines[index - 1].strip()
        next_blank = index + 1 == len(lines) or not lines[index + 1].strip()
        words = [word for word in title.split() if word.casefold() not in {"a", "an", "and", "of", "the", "to", "in"}]
        is_title_case = bool(words) and all(
            word[:1].isupper() for word in words if word[:1].isalpha()
        )
        ends_like_sentence = title.endswith((".", "!", "?"))
        if markdown or is_numbered or (
            not ends_like_sentence
            and (is_uppercase or (previous_blank and next_blank and is_title_case))
        ):
            headings.append((start, end, title))
    return headings


def _pdf_heading_lines(page, page_text: str) -> list[tuple[int, int, str]]:
    """Use concise, larger-font PDF lines as section headings."""
    data = page.get_text("dict", sort=True)
    lines: list[tuple[str, list[float]]] = []
    sizes: list[float] = []
    for block in data.get("blocks", []):
        for line in block.get("lines", []):
            spans = line.get("spans", [])
            title = " ".join(span.get("text", "").strip() for span in spans).strip()
            font_sizes = [float(span.get("size", 0.0)) for span in spans if span.get("text", "").strip()]
            if title and font_sizes:
                lines.append((title, font_sizes))
                sizes.extend(font_sizes)
    if not sizes:
        return []

    sizes.sort()
    body_size = sizes[len(sizes) // 2]
    headings: list[tuple[int, int, str]] = []
    search_from = 0
    for title, font_sizes in lines:
        start = page_text.find(title, search_from)
        if start < 0:
            continue
        search_from = start + len(title)
        letters = [character for character in title if character.isalpha()]
        uppercase = bool(letters) and all(character.isupper() for character in letters)
        concise = len(title) <= 100 and len(title.split()) <= 14
        if concise and not title.endswith((".", "!", "?")) and (
            min(font_sizes) >= body_size * 1.18 or uppercase
        ):
            headings.append((start, start + len(title), title))
    return headings


def _make_section_chunks(
    text: str,
    headings: list[tuple[int, int, str]],
    inherited_section: str | None = None,
) -> Iterable[tuple[int, int, str, str | None]]:
    """Chunk within section boundaries and retain absolute text offsets."""
    boundaries: list[tuple[int, int, str | None]] = []
    if not headings:
        if text:
            boundaries.append((0, len(text), inherited_section))
    else:
        first_start = headings[0][0]
        if first_start:
            boundaries.append((0, first_start, inherited_section))
        for index, (start, _end, title) in enumerate(headings):
            end = headings[index + 1][0] if index + 1 < len(headings) else len(text)
            boundaries.append((start, end, title))

    for segment_start, segment_end, current_section in boundaries:
        segment = text[segment_start:segment_end]
        for start, end, excerpt in _make_chunks(segment):
            yield (
                segment_start + start,
                segment_start + end,
                excerpt,
                current_section,
            )


class DocumentSearchIndex:
    """Build once per open document, then search all its passages locally."""

    def __init__(self, document, embedding_client: EmbeddingProvider | None = None):
        self.document = document
        self.passages: list[Passage] = []
        self._term_frequencies: list[dict[str, int]] = []
        self._document_frequency: dict[str, int] = {}
        self._embedding_client = embedding_client
        if self._embedding_client is None:
            from app_config import EMBEDDING_MODEL_PATH

            if EMBEDDING_MODEL_PATH is not None:
                self._embedding_client = LocalEmbeddingClient()
        self._passage_vectors: list[list[float]] = []
        self._build()

    def _build(self) -> None:
        from documents.models import PDFDocument, TextDocument

        path = str(self.document.path)
        if isinstance(self.document, TextDocument):
            for start, end, excerpt, section in _make_section_chunks(
                self.document.content, _text_heading_lines(self.document.content)
            ):
                self.passages.append(Passage(
                    ref="", text=excerpt, location=f"Character {start + 1}",
                    document_path=path, document_format="TXT",
                    char_start=start, char_end=end, section=section,
                ))
        elif isinstance(self.document, PDFDocument):
            # Use a separate read-only PDF handle. Indexing can run in a worker
            # while the UI renders pages from the active viewer document.
            import pymupdf

            from app_config import OCR_DPI, OCR_ENABLED, OCR_LANGUAGE

            tesseract_available = OCR_ENABLED and shutil.which("tesseract") is not None
            ocr_unavailable_logged = False
            current_section = None
            with pymupdf.open(path) as searchable_pdf:
                for page_index in range(searchable_pdf.page_count):
                    page = searchable_pdf[page_index]
                    page_text = page.get_text("text", sort=True)
                    if len(page_text.strip()) < 24 and OCR_ENABLED:
                        if not tesseract_available:
                            if not ocr_unavailable_logged:
                                _LOGGER.warning(
                                    "Scanned PDF text was not extractable and Tesseract is unavailable; "
                                    "install Tesseract OCR to index scanned pages."
                                )
                                ocr_unavailable_logged = True
                        else:
                            try:
                                text_page = page.get_textpage_ocr(
                                    language=OCR_LANGUAGE, dpi=OCR_DPI, full=True
                                )
                                page_text = page.get_text(
                                    "text", textpage=text_page, sort=True
                                )
                            except (OSError, RuntimeError, ValueError) as error:
                                _LOGGER.warning(
                                    "OCR failed for PDF page %s; skipping OCR text: %s",
                                    page_index + 1,
                                    error,
                                )
                    headings = _pdf_heading_lines(page, page_text)
                    heading_starts = {start for start, _end, _title in headings}
                    headings.extend(
                        heading for heading in _text_heading_lines(page_text)
                        if heading[0] not in heading_starts
                    )
                    headings.sort(key=lambda heading: heading[0])
                    for start, end, excerpt, section in _make_section_chunks(
                        page_text, headings, current_section
                    ):
                        self.passages.append(Passage(
                            ref="", text=excerpt, location=f"Page {page_index + 1}",
                            document_path=path, document_format="PDF",
                            char_start=start, char_end=end, page_index=page_index,
                            section=section,
                        ))
                    if headings:
                        current_section = headings[-1][2]

        for passage in self.passages:
            terms = _tokens(passage.text)
            counts: dict[str, int] = {}
            for term in terms:
                counts[term] = counts.get(term, 0) + 1
            self._term_frequencies.append(counts)
            for term in counts:
                self._document_frequency[term] = self._document_frequency.get(term, 0) + 1
        if self._embedding_client is not None and self.passages:
            try:
                vectors = self._embedding_client.embed_texts(
                    [passage.text for passage in self.passages]
                )
                if len(vectors) != len(self.passages) or any(not vector for vector in vectors):
                    raise EmbeddingError("The local embeddings provider returned an incomplete index.")
                dimension = len(vectors[0])
                if any(len(vector) != dimension for vector in vectors):
                    raise EmbeddingError("The local embeddings provider returned inconsistent vector dimensions.")
                self._passage_vectors = vectors
            except EmbeddingError as error:
                _LOGGER.warning("Local semantic indexing unavailable; using lexical search: %s", error)

    def search(self, query: str, limit: int = 5) -> list[Passage]:
        if not isinstance(query, str) or not query.strip() or limit < 1:
            return []
        query_terms = _tokens(query)
        # Queries containing only function words have no useful retrieval
        # signal; returning no evidence is safer than ranking arbitrary text.
        meaningful = [term for term in query_terms if term not in _STOP_WORDS]
        if not meaningful:
            return []
        query_weights, original_terms = _expanded_query_terms(meaningful)
        count = len(self.passages)
        lexical_scores: list[float] = []
        phrase = " ".join(_tokens(query))
        for index, passage in enumerate(self.passages):
            terms = self._term_frequencies[index]
            score = 0.0
            for term, query_weight in query_weights.items():
                frequency = terms.get(term, 0)
                if not frequency:
                    continue
                document_frequency = self._document_frequency.get(term, 0)
                inverse_frequency = math.log(1 + (count - document_frequency + 0.5) / (document_frequency + 0.5))
                length_norm = 1 + 0.65 * (sum(terms.values()) / 120.0)
                score += inverse_frequency * (frequency * 2.2 / (frequency + 1.2 * length_norm)) * query_weight
            normalized_text = " ".join(_tokens(passage.text))
            if phrase and len(phrase) > 3 and phrase in normalized_text:
                score += 3.0
            matched = sum(1 for term in original_terms if term in terms)
            if matched > 1:
                score += 0.45 * matched
            # Reward coverage of the user's actual query, not just expanded terms.
            coverage = matched / max(1, len(set(meaningful)))
            score *= 0.75 + 0.25 * coverage
            if passage.section:
                section_terms = set(_tokens(passage.section))
                section_matches = sum(term in section_terms for term in set(meaningful))
                if section_matches:
                    score += 0.65 * section_matches / max(1, len(set(meaningful)))
            lexical_scores.append(score)

        query_vector: list[float] | None = None
        if self._embedding_client is not None and self._passage_vectors:
            try:
                query_vectors = self._embedding_client.embed_texts([query])
                if len(query_vectors) != 1 or not query_vectors[0]:
                    raise EmbeddingError("The local embeddings provider returned no query vector.")
                query_vector = query_vectors[0]
            except EmbeddingError as error:
                _LOGGER.warning("Local semantic query unavailable; using lexical search: %s", error)

        scored: list[tuple[float, int]] = []
        max_lexical_score = max(lexical_scores, default=0.0)
        for index, lexical_score in enumerate(lexical_scores):
            score = lexical_score
            if query_vector is not None:
                passage_vector = self._passage_vectors[index]
                if len(query_vector) != len(passage_vector):
                    _LOGGER.warning(
                        "Local semantic query returned a different vector dimension; using lexical search."
                    )
                    query_vector = None
                    scored.clear()
                    break
                similarity = sum(left * right for left, right in zip(query_vector, passage_vector))
                # Normalize lexical scores per query so cosine similarity can
                # contribute without changing the existing lexical-only scale.
                lexical_component = lexical_score / max_lexical_score if max_lexical_score else 0.0
                score = 0.55 * lexical_component + 0.45 * max(0.0, similarity)
                if lexical_score <= 0 and similarity < 0.25:
                    continue
            if score > 0:
                scored.append((score, index))
        if query_vector is None:
            scored = [
                (score, index)
                for index, score in enumerate(lexical_scores)
                if score > 0
            ]
        scored.sort(key=lambda item: (-item[0], item[1]))

        # Overlapping chunks can otherwise fill the results with nearly
        # identical passages. Prefer distinct evidence locations where possible.
        chosen: list[tuple[float, int]] = []
        for score, index in scored:
            candidate = self.passages[index]
            redundant = False
            for _chosen_score, chosen_index in chosen:
                existing = self.passages[chosen_index]
                if candidate.page_index != existing.page_index:
                    continue
                if candidate.char_start is None or existing.char_start is None:
                    continue
                overlap = max(0, min(candidate.char_end or 0, existing.char_end or 0) - max(candidate.char_start, existing.char_start))
                shortest = max(1, min((candidate.char_end or 0) - candidate.char_start, (existing.char_end or 0) - existing.char_start))
                if overlap / shortest >= 0.62:
                    redundant = True
                    break
            if not redundant:
                chosen.append((score, index))
            if len(chosen) >= limit:
                break
        results = []
        for rank, (score, index) in enumerate(chosen, start=1):
            passage = self.passages[index]
            results.append(Passage(
                ref=f"R{rank}", text=passage.text, location=passage.location,
                document_path=passage.document_path, document_format=passage.document_format,
                char_start=passage.char_start, char_end=passage.char_end,
                page_index=passage.page_index, section=passage.section, score=score,
            ))
        return results


def format_evidence(passages: list[Passage], max_chars: int = 4800) -> str:
    """Build a bounded, labeled evidence block for the local model prompt."""
    blocks = []
    used = 0
    for passage in passages:
        excerpt = passage.text.strip()
        remaining = max_chars - used
        if remaining <= 0:
            break
        section_name = getattr(passage, "section", None)
        section = f" · {section_name}" if section_name else ""
        header = f"[{passage.ref}] {passage.location}{section}\n"
        excerpt = excerpt[:max(0, remaining - len(header))]
        if not excerpt:
            continue
        block = header + excerpt
        blocks.append(block)
        used += len(block) + 2
    return "\n\n".join(blocks)
