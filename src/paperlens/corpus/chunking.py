from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable

from paperlens.domain import ExtractedPaper, PaperChunk, SectionFragment

_MARKDOWN_HEADING = re.compile(r"^\s*#{1,6}\s+(.+?)\s*#*\s*$")
_NUMBERED_HEADING = re.compile(r"^\s*(?:\d+(?:\.\d+)*\.?|[A-Z]\.)\s+([^\n]{2,100})\s*$")
_TOKEN = re.compile(r"\w+(?:[-'][\w]+)*|[^\w\s]", re.UNICODE)
_PARAGRAPH_SPLIT = re.compile(r"\n\s*\n")

_SECTION_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("abstract", ("abstract", "چکیده")),
    ("introduction", ("introduction", "background", "مقدمه", "پیشینه")),
    (
        "related_work",
        ("related work", "prior work", "literature review", "کارهای مرتبط", "مرور ادبیات"),
    ),
    (
        "methodology",
        (
            "method",
            "methodology",
            "approach",
            "architecture",
            "model",
            "روش",
            "روش شناسی",
            "معماری",
            "مدل",
        ),
    ),
    (
        "experiments",
        (
            "experiment",
            "evaluation",
            "implementation detail",
            "benchmark classification",
            "results",
            "آزمایش",
            "ارزیابی",
            "نتایج",
        ),
    ),
    ("limitations", ("limitation", "failure case", "محدودیت")),
    (
        "conclusion",
        ("conclusion", "discussion", "future work", "نتیجه گیری", "بحث", "کارهای آینده"),
    ),
    ("references", ("reference", "bibliography", "منابع")),
    ("appendix", ("appendix", "supplement", "پیوست")),
)


def estimate_tokens(text: str) -> int:
    return len(_TOKEN.findall(text))


def canonical_section(title: str) -> str:
    normalized = re.sub(r"[^\w]+", " ", title.casefold(), flags=re.UNICODE).strip()
    for section_type, aliases in _SECTION_ALIASES:
        if any(alias in normalized for alias in aliases):
            return section_type
    return "other"


class SectionParser:
    """Track section headings across pages and emit page-bound fragments."""

    def parse(self, paper: ExtractedPaper) -> list[SectionFragment]:
        current_section = "Front Matter"
        current_type = "front_matter"
        fragments: list[SectionFragment] = []

        for page in paper.pages:
            buffer: list[str] = []

            for line in page.markdown.splitlines():
                heading = self._heading_title(line)
                if heading:
                    self._append_fragment(
                        fragments,
                        current_section,
                        current_type,
                        page.page_number,
                        buffer,
                    )
                    current_section = heading
                    current_type = canonical_section(heading)
                else:
                    buffer.append(line)
            self._append_fragment(
                fragments,
                current_section,
                current_type,
                page.page_number,
                buffer,
            )

        return fragments

    @staticmethod
    def _append_fragment(
        fragments: list[SectionFragment],
        section: str,
        section_type: str,
        page_number: int,
        buffer: list[str],
    ) -> None:
        text = "\n".join(buffer).strip()
        if text:
            fragments.append(
                SectionFragment(
                    section=section,
                    section_type=section_type,
                    page_number=page_number,
                    text=text,
                )
            )
        buffer.clear()

    @staticmethod
    def _heading_title(line: str) -> str | None:
        markdown_match = _MARKDOWN_HEADING.match(line)
        if markdown_match:
            return markdown_match.group(1).strip()

        numbered_match = _NUMBERED_HEADING.match(line)
        if numbered_match and len(line.split()) <= 14:
            return numbered_match.group(1).strip()

        stripped = line.strip().strip("*")
        if stripped.casefold() in {
            "abstract",
            "introduction",
            "related work",
            "experiments",
            "results",
            "conclusion",
            "conclusions",
            "references",
            "چکیده",
            "مقدمه",
            "پیشینه",
            "کارهای مرتبط",
            "روش",
            "روش‌شناسی",
            "آزمایش‌ها",
            "ارزیابی",
            "نتایج",
            "محدودیت‌ها",
            "بحث",
            "نتیجه‌گیری",
            "منابع",
            "پیوست",
        }:
            return stripped
        return None


class SectionAwareChunker:
    """Build paragraph-preserving chunks that never cross section boundaries."""

    def __init__(self, max_tokens: int = 350, overlap_tokens: int = 50) -> None:
        if max_tokens < 100:
            raise ValueError("max_tokens must be at least 100")
        if overlap_tokens < 0 or overlap_tokens >= max_tokens:
            raise ValueError("overlap_tokens must be non-negative and smaller than max_tokens")
        self.max_tokens = max_tokens
        self.overlap_tokens = overlap_tokens

    def chunk(self, paper: ExtractedPaper) -> list[PaperChunk]:
        fragments = SectionParser().parse(paper)
        chunks: list[PaperChunk] = []
        chunk_index = 0

        for group in self._group_by_section(fragments):
            units = self._paragraph_units(group)
            for text, page_start, page_end in self._pack(units):
                chunk_id = self._chunk_id(paper.record.paper_id, chunk_index, text)
                chunks.append(
                    PaperChunk(
                        chunk_id=chunk_id,
                        paper_id=paper.record.paper_id,
                        paper_title=paper.record.title,
                        section=group[0].section,
                        section_type=group[0].section_type,
                        page_start=page_start,
                        page_end=page_end,
                        chunk_index=chunk_index,
                        token_count=estimate_tokens(text),
                        text=text,
                        source_sha256=paper.sha256,
                    )
                )
                chunk_index += 1
        return chunks

    @staticmethod
    def _group_by_section(
        fragments: list[SectionFragment],
    ) -> list[list[SectionFragment]]:
        groups: list[list[SectionFragment]] = []
        for fragment in fragments:
            if (
                not groups
                or groups[-1][0].section != fragment.section
                or groups[-1][0].section_type != fragment.section_type
            ):
                groups.append([fragment])
            else:
                groups[-1].append(fragment)
        return groups

    def _paragraph_units(self, fragments: list[SectionFragment]) -> list[tuple[str, int]]:
        units: list[tuple[str, int]] = []
        for fragment in fragments:
            paragraphs = [
                paragraph.strip()
                for paragraph in _PARAGRAPH_SPLIT.split(fragment.text)
                if paragraph.strip()
            ]
            for paragraph in paragraphs:
                if estimate_tokens(paragraph) <= self.max_tokens:
                    units.append((paragraph, fragment.page_number))
                else:
                    units.extend(self._split_long_text(paragraph, fragment.page_number))
        return units

    def _split_long_text(self, text: str, page: int) -> list[tuple[str, int]]:
        matches = list(_TOKEN.finditer(text))
        if not matches:
            return []
        step = max(1, self.max_tokens - self.overlap_tokens)
        pieces = []
        for start in range(0, len(matches), step):
            end = min(start + self.max_tokens, len(matches))
            piece = text[matches[start].start() : matches[end - 1].end()].strip()
            if piece:
                pieces.append((piece, page))
            if end >= len(matches):
                break
        return pieces

    def _pack(self, units: list[tuple[str, int]]) -> Iterable[tuple[str, int, int]]:
        current: list[tuple[str, int]] = []
        current_tokens = 0

        for unit in units:
            unit_tokens = estimate_tokens(unit[0])
            if current and current_tokens + unit_tokens > self.max_tokens:
                yield self._render(current)
                current = self._overlap_tail(current)
                current_tokens = sum(estimate_tokens(text) for text, _ in current)
                while current and current_tokens + unit_tokens > self.max_tokens:
                    current.pop(0)
                    current_tokens = sum(estimate_tokens(text) for text, _ in current)

            current.append(unit)
            current_tokens += unit_tokens

        if current:
            yield self._render(current)

    def _overlap_tail(self, units: list[tuple[str, int]]) -> list[tuple[str, int]]:
        if self.overlap_tokens == 0:
            return []
        tail: list[tuple[str, int]] = []
        token_count = 0
        for unit in reversed(units):
            unit_tokens = estimate_tokens(unit[0])
            if tail and token_count + unit_tokens > self.overlap_tokens:
                break
            tail.append(unit)
            token_count += unit_tokens
            if token_count >= self.overlap_tokens:
                break
        return list(reversed(tail))

    @staticmethod
    def _render(units: list[tuple[str, int]]) -> tuple[str, int, int]:
        text = "\n\n".join(unit[0] for unit in units).strip()
        pages = [unit[1] for unit in units]
        return text, min(pages), max(pages)

    @staticmethod
    def _chunk_id(paper_id: str, chunk_index: int, text: str) -> str:
        payload = f"{paper_id}\0{chunk_index}\0{text}".encode()
        return f"{paper_id}-{hashlib.sha256(payload).hexdigest()[:16]}"
