from __future__ import annotations

import json
from pathlib import Path

from paperlens.corpus.manifest import load_paper_manifest
from paperlens.workspaces import WorkspaceRecord, WorkspaceStore


def install_demo_fixture(
    store: WorkspaceStore,
    papers_path: Path,
) -> WorkspaceRecord:
    """Install the optional Charge While You Shop sample as an ordinary workspace."""
    manifest = load_paper_manifest(papers_path / "manifest.json")
    try:
        current = store.get("demo")
    except KeyError:
        current = store.ensure_workspace("demo", "Charge While You Shop")

    expected_names = {item.file_name for item in manifest}
    if (
        not current.is_demo
        and {item.original_filename for item in current.documents} == expected_names
        and all(
            _inside_workspace(store, current.workspace_id, item.source_path)
            for item in current.documents
        )
    ):
        return current

    workspace_dir = store.root / current.workspace_id
    backup = workspace_dir / "legacy-workspace.json"
    if current.is_demo and not backup.exists():
        backup.write_text(
            json.dumps(current.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    imported = []
    for record in manifest:
        source = record.pdf_path(papers_path)
        if not source.is_file():
            raise FileNotFoundError(source)
        document = store.add_document(
            current.workspace_id,
            source.read_bytes(),
            record.file_name,
            "application/pdf",
        )
        imported.append(
            document.model_copy(
                update={
                    "title": record.title,
                    "origin_url": record.source_url,
                    "author": ", ".join(record.authors),
                    "published_at": str(record.year),
                }
            )
        )

    return store.save(
        current.model_copy(
            update={
                "name": "Charge While You Shop",
                "is_demo": False,
                "documents": imported,
                "embedding_model": None,
                "vector_collection_id": None,
            }
        )
    )


def _inside_workspace(store: WorkspaceStore, workspace_id: str, source_path: str) -> bool:
    path = Path(source_path).resolve()
    workspace_dir = (store.root / workspace_id).resolve()
    return workspace_dir in path.parents
