from dataclasses import dataclass

from documents.models import ReadingContext
from documents.retrieval import Passage, format_evidence
from services.api_client import ServiceResponse, ask, explain


@dataclass(frozen=True)
class Explanation:
    text: str
    context: ReadingContext
    is_error: bool = False


@dataclass(frozen=True)
class Answer:
    text: str
    context: ReadingContext
    is_error: bool = False
    sources: tuple[Passage, ...] = ()


class LocalReaderAssistant:
    def explain(self, context: ReadingContext) -> Explanation:
        response = explain(context.selected_text, context.surrounding_text)
        return Explanation(
            text=response.text,
            context=context,
            is_error=not response.ok,
        )

    def answer(self, question: str, context: ReadingContext, sources=()) -> Answer:
        # Keep the combined request below the local API's 9,000-character
        # input limit even when a selected passage or sentence is unusually long.
        safe_question = question.strip()[:1400]
        safe_selected = context.selected_text[:1200]
        safe_context = context.surrounding_text[:1400]
        fixed_budget = len(safe_question) + len(safe_selected) + len(safe_context) + 900
        evidence_budget = max(0, min(4800, 8200 - fixed_budget))
        evidence = format_evidence(list(sources), max_chars=evidence_budget)
        prompt_parts = [
            f"Reader question: {safe_question}",
            f"Selected text: {safe_selected}",
            f"Immediate context: {safe_context}",
            f"Document: {context.document_title} ({context.location})",
        ]
        if evidence:
            prompt_parts.extend([
                "Relevant passages retrieved from the full document:",
                evidence,
                "Use these passages as evidence when relevant. Cite claims using their exact labels, such as [R1] or [R3]. Do not invent references. If the document does not contain enough information, say so.",
            ])
        else:
            prompt_parts.append("No relevant passages were found elsewhere in the document. Be transparent if the supplied context is insufficient.")
        prompt_parts.append("Answer clearly and concisely. Ground document-specific claims in the supplied evidence. Do not invent medical facts or treatment advice.")
        response = ask("\n\n".join(prompt_parts))
        return Answer(
            text=response.text,
            context=context,
            is_error=not response.ok,
            sources=tuple(sources),
        )

    def generate_revision_notes(self, context: ReadingContext) -> ServiceResponse:
        selected = context.selected_text[:2400]
        surrounding = context.surrounding_text[:3200]
        prompt = "\n\n".join(
            (
                "Create concise, editable revision notes from the supplied passage.",
                "Use 3 to 6 short bullet points. Preserve important relationships and "
                "qualifications. Do not add facts that are not supported by the passage. "
                "If the passage is insufficient, say so rather than guessing.",
                "The source scope is only the selected passage and its immediate context. "
                "Do not claim to summarize the whole section, chapter, or document.",
                f"Document: {context.document_title} ({context.location})",
                f"Selected passage:\n{selected}",
                f"Immediate context:\n{surrounding}",
                "Return only the revision notes, without a preamble.",
            )
        )
        return ask(prompt)
