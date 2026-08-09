from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from zipfile import ZipFile

import pytest

from scripts.build_release import _validated_target, audit
from scripts.cleanup import clean, validate_target
from scripts.repository_policy import (
    audit_archive,
    audit_public_tree,
    release_candidates,
    scan_secrets,
    tracked_candidates,
)


def test_release_audit_rejects_private_runtime_paths(tmp_path: Path) -> None:
    safe = tmp_path / "src" / "paperlens" / "app.py"
    private = tmp_path / "data" / "workspaces" / "private.pdf"
    safe.parent.mkdir(parents=True)
    private.parent.mkdir(parents=True)
    safe.write_text("print('safe')", encoding="utf-8")
    private.write_bytes(b"private")

    assert audit(tmp_path) == ["data/workspaces/private.pdf"]


def test_release_audit_accepts_clean_public_tree(tmp_path: Path) -> None:
    readme = tmp_path / "README.md"
    readme.write_text("# Public", encoding="utf-8")

    assert audit(tmp_path) == []


def test_repository_inventory_excludes_private_assets_and_keeps_public_assets() -> None:
    candidates = {path.relative_to(Path.cwd()).as_posix() for path in tracked_candidates()}

    assert ".env" not in candidates
    assert "data/workspaces" not in "\n".join(candidates)
    assert not any("node_modules" in path for path in candidates)
    assert not any(
        path.endswith(".pdf") and not path.startswith("docs/demo/outputs/")
        for path in candidates
    )
    assert not any("/Kalameh/" in path or "/IRANYekanX/" in path for path in candidates)
    assert "src/paperlens/assets/fonts/Vazirmatn/OFL.txt" in candidates
    assert "papers/manifest.json" in candidates
    assert "docs/demo_questions.md" in candidates
    assert not any(path.startswith("data/") for path in candidates)


def test_release_candidates_exclude_output_placeholder() -> None:
    candidates = {path.relative_to(Path.cwd()).as_posix() for path in release_candidates()}

    assert "outputs/.gitkeep" not in candidates


def test_repository_inventory_excludes_untracked_files_in_valid_git_repo(
    tmp_path: Path,
) -> None:
    tracked = tmp_path / "docs" / "tracked.md"
    untracked = tmp_path / "docs" / "private-draft.md"
    tracked.parent.mkdir(parents=True)
    tracked.write_text("public", encoding="utf-8")
    untracked.write_text("private", encoding="utf-8")
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "add", "docs/tracked.md"], check=True)

    candidates = {path.relative_to(tmp_path).as_posix() for path in tracked_candidates(tmp_path)}

    assert "docs/tracked.md" in candidates
    assert "docs/private-draft.md" not in candidates


def test_archive_audit_rejects_private_paths_and_requires_public_font(tmp_path: Path) -> None:
    archive = tmp_path / "release.zip"
    with ZipFile(archive, "w") as package:
        package.writestr("SourceLens/src/paperlens/assets/fonts/Vazirmatn/Vazirmatn.ttf", b"font")
        package.writestr("SourceLens/src/paperlens/assets/fonts/Vazirmatn/OFL.txt", "OFL")
        package.writestr("SourceLens/outputs/.gitkeep", "")

    assert audit_archive(archive) == ["SourceLens/outputs/.gitkeep"]


def test_archive_audit_allows_curated_demo_outputs(tmp_path: Path) -> None:
    archive = tmp_path / "release.zip"
    with ZipFile(archive, "w") as package:
        package.writestr("SourceLens/src/paperlens/assets/fonts/Vazirmatn/font.ttf", b"font")
        package.writestr("SourceLens/src/paperlens/assets/fonts/Vazirmatn/OFL.txt", "OFL")
        package.writestr("SourceLens/docs/demo/outputs/evaluation.pdf", b"%PDF")
        package.writestr("SourceLens/docs/demo/outputs/presentation.pptx", b"pptx")

    assert audit_archive(archive) == []


def test_public_tree_audit_rejects_local_fonts_and_downloaded_pdfs(tmp_path: Path) -> None:
    font = tmp_path / "src/paperlens/assets/fonts/Kalameh/local.ttf"
    paper = tmp_path / "papers/paper.pdf"
    font.parent.mkdir(parents=True)
    paper.parent.mkdir(parents=True)
    font.write_bytes(b"font")
    paper.write_bytes(b"pdf")

    assert audit_public_tree(tmp_path) == [
        "papers/paper.pdf",
        "src/paperlens/assets/fonts/Kalameh/local.ttf",
    ]


def test_public_tree_audit_allows_demo_evaluation_pdf(tmp_path: Path) -> None:
    report = tmp_path / "docs/demo/outputs/evaluation.pdf"
    report.parent.mkdir(parents=True)
    report.write_bytes(b"%PDF-1.7\n")

    assert audit_public_tree(tmp_path) == []


def test_secret_scan_does_not_flag_hyphenated_research_slug(tmp_path: Path) -> None:
    source = tmp_path / "source.md"
    source.write_text("https://example.org/coastal-risk-reduction-credits", encoding="utf-8")

    assert scan_secrets([source], tmp_path) == []


def test_public_demo_indexes_reference_published_assets() -> None:
    output_root = Path("docs/demo/outputs")
    for language in ("en", "fa"):
        directory = output_root / language
        index = json.loads((directory / "index.json").read_text(encoding="utf-8"))
        assert len(index["artifacts"]) == 13
        for artifact in index["artifacts"]:
            for asset in (artifact, *artifact.get("assets", [])):
                published = directory / asset["path"]
                assert published.is_file(), asset["path"]
                assert published.stat().st_size == asset["size"]
                assert hashlib.sha256(published.read_bytes()).hexdigest() == asset["sha256"]


def test_published_captions_are_byte_stable_across_git_checkouts() -> None:
    attributes = Path(".gitattributes").read_text(encoding="utf-8").splitlines()

    assert "*.srt binary" in attributes


def test_public_example_uses_region_independent_embedding_default() -> None:
    example = Path(".env.example").read_text(encoding="utf-8").splitlines()

    assert "EMBEDDING_PROVIDER=local" in example
    assert "EMBEDDING_MODEL=intfloat/multilingual-e5-small" in example


def test_cleanup_is_dry_run_and_protects_workspace(tmp_path: Path) -> None:
    disposable = tmp_path / "tmp" / "scratch.txt"
    private = tmp_path / "data" / "workspaces" / "private.pdf"
    disposable.parent.mkdir(parents=True)
    private.parent.mkdir(parents=True)
    disposable.write_text("scratch", encoding="utf-8")
    private.write_bytes(b"private")

    candidates = clean(tmp_path)

    assert disposable.parent.resolve() in candidates
    assert disposable.is_file()
    assert private.is_file()
    with pytest.raises(ValueError, match="broad"):
        validate_target(tmp_path, tmp_path)
    with pytest.raises(ValueError, match="protected"):
        validate_target(tmp_path, private)


def test_release_target_must_be_direct_child_of_release_directory(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unsafe"):
        _validated_target(tmp_path)
