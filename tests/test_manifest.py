import hashlib
import json
from pathlib import Path

import httpx
import pytest

from paperlens.corpus import demo_assets
from paperlens.corpus.demo_assets import ensure_demo_papers
from paperlens.corpus.manifest import load_paper_manifest

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_manifest_has_checksum_pinned_charge_while_you_shop_papers() -> None:
    records = load_paper_manifest(PROJECT_ROOT / "papers" / "manifest.json")

    assert len(records) == 2
    assert len({record.paper_id for record in records}) == 2
    assert {record.paper_id for record in records} == {
        "ev-charging-consumer-preferences",
        "ev-charging-transatlantic-recommendations",
    }
    assert {record.sha256 for record in records} == {
        "5513d2a355ed952d33a1580d85eccf0d1733f07398f20ceb4eae5e61eafee154",
        "0bccb224fca1493c2f69d7cc90f482cf0e03e944085560d1e690c66fd2351999",
    }
    assert {record.source_url for record in records} == {
        "https://res.mdpi.com/d_attachment/energies/energies-14-04598/"
        "article_deploy/energies-14-04598-v2.pdf",
        "https://publications.jrc.ec.europa.eu/repository/bitstream/JRC133895/"
        "JRC133895_01.pdf",
    }
    assert {record.license_spdx for record in records} == {"CC-BY-4.0"}
    assert all(
        record.license_url == "https://creativecommons.org/licenses/by/4.0/"
        for record in records
    )
    assert all("electric vehicle charging" in record.title.casefold() for record in records)


def test_demo_download_verifies_checksum_before_installing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    content = b"public paper bytes"
    manifest = [{
        "paper_id": "paper",
        "file_name": "paper.pdf",
        "title": "Paper",
        "authors": ["Author"],
        "year": 2026,
        "venue": "Venue",
        "source_url": "https://example.test/paper.pdf",
        "sha256": hashlib.sha256(content).hexdigest(),
    }]
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    response = httpx.Response(
        200,
        content=content,
        request=httpx.Request("GET", manifest[0]["source_url"]),
    )
    monkeypatch.setattr(demo_assets.httpx, "get", lambda *_args, **_kwargs: response)

    assert ensure_demo_papers(tmp_path) == [tmp_path / "paper.pdf"]
    assert (tmp_path / "paper.pdf").read_bytes() == content


def test_demo_download_never_overwrites_a_mismatched_local_copy(tmp_path: Path) -> None:
    manifest = [{
        "paper_id": "paper",
        "file_name": "paper.pdf",
        "title": "Paper",
        "authors": ["Author"],
        "year": 2026,
        "venue": "Venue",
        "source_url": "https://example.test/paper.pdf",
        "sha256": hashlib.sha256(b"expected").hexdigest(),
    }]
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (tmp_path / "paper.pdf").write_bytes(b"user copy")

    with pytest.raises(ValueError, match="refusing to overwrite"):
        ensure_demo_papers(tmp_path)
    assert (tmp_path / "paper.pdf").read_bytes() == b"user copy"
