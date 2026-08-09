from __future__ import annotations

import re
from collections.abc import Collection, Sequence

from paperlens.domain import Evidence
from paperlens.language_quality import is_clean_persian

_CITATION = re.compile(r"\[E(\d+)]")


def has_grounded_report_citations(
    text: str,
    evidence: Sequence[Evidence],
    *,
    required_papers: Collection[str],
    language: str | None = None,
) -> bool:
    """Reject missing, out-of-range, and paper-incomplete report citations."""
    numbers = {int(value) for value in _CITATION.findall(text)}
    if not numbers or any(number < 1 or number > len(evidence) for number in numbers):
        return False
    cited_papers = {evidence[number - 1].paper_id for number in numbers}
    if not set(required_papers).issubset(cited_papers):
        return False
    if language == "fa":
        return is_clean_persian(text, minimum_characters=20)
    return True
