from __future__ import annotations

import json
from pathlib import Path

from pydantic import TypeAdapter

from paperlens.domain import PaperRecord


def load_paper_manifest(path: Path) -> list[PaperRecord]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return TypeAdapter(list[PaperRecord]).validate_python(raw)
