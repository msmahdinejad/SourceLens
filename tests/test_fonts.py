from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from paperlens import webapp
from paperlens.fonts import FontRegistry


def _system_font() -> Path:
    candidates = [
        Path("C:/Windows/Fonts/tahoma.ttf"),
        Path("C:/Windows/Fonts/arial.ttf"),
    ]
    return next(path for path in candidates if path.is_file())


def test_user_font_can_be_added_resolved_and_removed(tmp_path: Path):
    registry = FontRegistry(tmp_path / "fonts")
    source = _system_font()

    added = registry.add(source.name, source.read_bytes())

    assert added.font_id.startswith("font-")
    assert added.family
    assert registry.resolve(added.font_id).path.is_file()
    assert registry.list()[0].font_id == added.font_id

    registry.remove(added.font_id)

    assert registry.list() == []


def test_automatic_font_selection_requires_mixed_persian_and_latin_coverage(
    tmp_path: Path,
) -> None:
    registry = FontRegistry(tmp_path / "fonts")
    source = _system_font()
    added = registry.add(source.name, source.read_bytes())

    selected = registry.best_for_text("پژوهش قابل اعتماد with visible evidence")

    assert selected is not None
    assert selected.font_id == added.font_id


def test_user_font_rejects_non_font_content(tmp_path: Path):
    registry = FontRegistry(tmp_path / "fonts")

    with pytest.raises(ValueError, match="valid TTF or OTF"):
        registry.add("fake.ttf", b"not a font")


def test_public_vazirmatn_font_is_discoverable_and_read_only(tmp_path: Path):
    registry = FontRegistry(
        tmp_path / "fonts",
        bundled_root=FontRegistry.default_bundled_root(),
    )

    assets = {asset.family: asset for asset in registry.list()}

    assert "Vazirmatn" in assets
    assert not assets["Vazirmatn"].managed
    assert registry.best_for_text("پژوهش درباره یادگیری ماشین").family == "Vazirmatn"
    with pytest.raises(ValueError, match="read-only"):
        registry.remove(assets["Vazirmatn"].font_id)


def test_font_api_manages_the_same_registry_used_by_renderers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    registry = FontRegistry(tmp_path / "fonts")
    monkeypatch.setattr(webapp, "font_registry", lambda: registry)
    client = TestClient(webapp.app)
    source = _system_font()

    created = client.post(
        "/api/fonts",
        files={"file": (source.name, source.read_bytes(), "font/ttf")},
    )

    assert created.status_code == 201
    font_id = created.json()["font_id"]
    assert client.get("/api/fonts").json()["fonts"][0]["font_id"] == font_id
    assert client.get("/fonts/Vazirmatn-Regular.ttf").status_code == 200
    assert client.get("/fonts/IRANYekanXFaNum-Regular.woff2").status_code == 404
    assert client.delete(f"/api/fonts/{font_id}").status_code == 204
