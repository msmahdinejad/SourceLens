from __future__ import annotations

import hashlib
from pathlib import Path

import httpx

from paperlens.corpus.manifest import load_paper_manifest


def ensure_demo_papers(papers_path: Path) -> list[Path]:
    """Download missing public demo papers and verify their pinned checksums."""

    manifest = papers_path / "manifest.json"
    records = load_paper_manifest(manifest)
    papers_path.mkdir(parents=True, exist_ok=True)
    ready: list[Path] = []
    for record in records:
        if len(record.sha256) != 64:
            raise ValueError(f"Missing SHA-256 for demo paper: {record.paper_id}")
        destination = record.pdf_path(papers_path)
        if destination.is_file():
            actual = hashlib.sha256(destination.read_bytes()).hexdigest()
            if actual != record.sha256:
                raise ValueError(
                    f"Checksum mismatch for existing {destination.name}; refusing to overwrite it"
                )
            ready.append(destination)
            continue
        temporary = destination.with_suffix(".pdf.tmp")
        try:
            response = httpx.get(
                record.source_url,
                headers={"User-Agent": "Mozilla/5.0 SourceLens/2.1", "Accept": "application/pdf"},
                follow_redirects=True,
                timeout=90,
            )
            response.raise_for_status()
            temporary.write_bytes(response.content)
            actual = hashlib.sha256(temporary.read_bytes()).hexdigest()
            if actual != record.sha256:
                raise ValueError(
                    f"Checksum mismatch for downloaded {destination.name}: {actual}"
                )
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
        ready.append(destination)
    return ready
