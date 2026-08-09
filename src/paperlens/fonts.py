from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

from fontTools.ttLib import TTFont


@dataclass(frozen=True)
class FontAsset:
    font_id: str
    family: str
    filename: str
    path: Path
    managed: bool = True
    source: str = "user"

    def payload(self) -> dict[str, Any]:
        value = asdict(self)
        value.pop("path")
        return value


class FontRegistry:
    """Expose bundled, user-managed, and system fonts behind one small interface."""

    maximum_bytes = 20 * 1024 * 1024

    def __init__(self, root: Path, bundled_root: Path | None = None) -> None:
        self.root = root.resolve()
        self.manifest = self.root / "fonts.json"
        self.bundled_root = bundled_root.resolve() if bundled_root is not None else None

    @staticmethod
    def default_bundled_root() -> Path:
        return Path(__file__).resolve().parent / "assets" / "fonts"

    def list(self) -> list[FontAsset]:
        records = self._records()
        assets = []
        for record in records:
            path = self.root / str(record["filename"])
            if path.is_file():
                assets.append(
                    FontAsset(
                        font_id=str(record["font_id"]),
                        family=str(record["family"]),
                        filename=path.name,
                        path=path,
                    )
                )
        assets.extend(self._bundled_assets())
        return sorted(assets, key=lambda item: (item.source != "bundled", item.family.casefold()))

    def add(self, filename: str, content: bytes) -> FontAsset:
        suffix = Path(filename).suffix.casefold()
        if suffix not in {".ttf", ".otf"} or not content or len(content) > self.maximum_bytes:
            raise ValueError("Upload a valid TTF or OTF font up to 20 MB")
        try:
            font = TTFont(BytesIO(content), lazy=True)
            family = self._family_name(font)
            font.close()
        except Exception as error:
            raise ValueError("Upload a valid TTF or OTF font up to 20 MB") from error
        digest = hashlib.sha256(content).hexdigest()
        font_id = f"font-{digest[:16]}"
        stored_name = f"{font_id}{suffix}"
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / stored_name
        if not path.exists():
            path.write_bytes(content)
        records = [item for item in self._records() if item.get("font_id") != font_id]
        records.append({"font_id": font_id, "family": family, "filename": stored_name})
        self._write(records)
        return FontAsset(font_id=font_id, family=family, filename=stored_name, path=path)

    def remove(self, font_id: str) -> None:
        asset = self.resolve(font_id)
        if not asset.managed:
            raise ValueError("Bundled fonts are read-only")
        asset.path.unlink(missing_ok=True)
        self._write([item for item in self._records() if item.get("font_id") != font_id])

    def resolve(self, selection: str) -> FontAsset:
        requested = str(selection or "").strip().casefold()
        for asset in self.list():
            if requested in {asset.font_id.casefold(), asset.family.casefold()}:
                return asset
        raise KeyError(selection)

    def best_for_text(self, text: str) -> FontAsset | None:
        """Return the strongest registered or local-system font for the requested text."""

        required = {ord(character) for character in text if character.isalpha()}
        if not required:
            required = {ord(character) for character in "SourceLens پژوهش" if character.isalpha()}
        registered = self.list()
        candidates = [*registered, *self._system_fonts()]
        ranked: list[tuple[float, int, int, FontAsset]] = []
        source_bonus = {"user": 2, "bundled": 1, "system": 0}
        family_priority = {"vazirmatn": 3, "iranyekanx": 2, "kalameh": 1}
        for asset in candidates:
            try:
                font = TTFont(asset.path, lazy=True)
                supported = set((font.getBestCmap() or {}).keys())
                font.close()
            except Exception:
                continue
            score = len(required & supported) / len(required)
            ranked.append(
                (
                    score,
                    source_bonus.get(asset.source, 0),
                    family_priority.get(asset.family.casefold(), 0),
                    asset,
                )
            )
        if not ranked:
            return None
        score, _, _, asset = max(ranked, key=lambda item: (item[0], item[1], item[2]))
        return asset if score >= 0.85 else None

    def _bundled_assets(self) -> list[FontAsset]:
        if self.bundled_root is None or not self.bundled_root.is_dir():
            return []
        assets: list[FontAsset] = []
        for family_dir in sorted(self.bundled_root.iterdir()):
            if not family_dir.is_dir():
                continue
            regulars = sorted(family_dir.glob("*Regular.ttf")) + sorted(
                family_dir.glob("*Regular.otf")
            )
            candidates = regulars or sorted(family_dir.glob("*.ttf")) + sorted(
                family_dir.glob("*.otf")
            )
            if not candidates:
                continue
            path = candidates[0].resolve()
            try:
                font = TTFont(path, lazy=True)
                family = self._family_name(font)
                font.close()
            except Exception:
                continue
            slug = "".join(character for character in family.casefold() if character.isalnum())
            assets.append(
                FontAsset(
                    font_id=f"bundled-{slug or family_dir.name.casefold()}",
                    family=family,
                    filename=path.name,
                    path=path,
                    managed=False,
                    source="bundled",
                )
            )
        return assets

    def _records(self) -> list[dict[str, str]]:
        if not self.manifest.is_file():
            return []
        try:
            payload = json.loads(self.manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        return (
            [item for item in payload if isinstance(item, dict)]
            if isinstance(payload, list)
            else []
        )

    def _write(self, records: list[dict[str, str]]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        temporary = self.manifest.with_suffix(".tmp")
        temporary.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.manifest)

    @staticmethod
    def _family_name(font: TTFont) -> str:
        table = font["name"]
        for name_id in (16, 1):
            for record in table.names:
                if record.nameID == name_id:
                    value = record.toUnicode().strip()
                    if value:
                        return value[:120]
        raise ValueError("Font has no family name")

    @classmethod
    def _system_fonts(cls) -> list[FontAsset]:
        candidates = (
            Path("C:/Windows/Fonts/tahoma.ttf"),
            Path("C:/Windows/Fonts/segoeui.ttf"),
            Path("C:/Windows/Fonts/arial.ttf"),
            Path("/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf"),
            Path("/usr/share/fonts/truetype/noto/NotoNaskhArabic-Regular.ttf"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
            Path("/usr/share/fonts/truetype/freefont/FreeSans.ttf"),
            Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf"),
            Path("/Library/Fonts/Arial Unicode.ttf"),
        )
        assets: list[FontAsset] = []
        for path in candidates:
            if not path.is_file():
                continue
            try:
                font = TTFont(path, lazy=True)
                family = cls._family_name(font)
                font.close()
            except Exception:
                continue
            digest = hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()[:16]
            assets.append(
                FontAsset(
                    font_id=f"system-{digest}",
                    family=family,
                    filename=path.name,
                    path=path.resolve(),
                    managed=False,
                    source="system",
                )
            )
        return assets
