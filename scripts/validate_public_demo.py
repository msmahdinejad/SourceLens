from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import subprocess
from collections.abc import Sequence
from pathlib import Path
from zipfile import BadZipFile, ZipFile, is_zipfile

from paperlens.language_quality import is_clean_persian

CATALOG_TYPES = (
    "audio_overview",
    "report",
    "concept_map",
    "flashcards",
    "quiz",
    "infographic",
    "data_table",
    "chart",
    "timeline",
    "comparison",
    "research_gap",
    "slide_deck",
    "video_overview",
)
_MARKDOWN_TYPES = {"report", "comparison", "research_gap"}
_JSON_TYPES = {
    "concept_map",
    "flashcards",
    "quiz",
    "infographic",
    "data_table",
    "chart",
    "timeline",
}
_PRIVATE_REFERENCE = re.compile(
    r"(?:(?<![A-Za-z])\b[A-Za-z]:[\\/]|/(?:Users|home)/|data[\\/]workspaces|"
    r"(?:ws|doc|artifact|search)-[a-f0-9]{12})",
    re.IGNORECASE,
)
_SHA256 = re.compile(r"[a-f0-9]{64}")


def validate_public_demo(
    root: Path,
    *,
    ffprobe: Sequence[str] = ("ffprobe",),
) -> list[str]:
    errors: list[str] = []
    for language in ("en", "fa"):
        _validate_language(root / language, language, ffprobe, errors)
    return sorted(errors)


def _validate_language(
    directory: Path,
    language: str,
    ffprobe: Sequence[str],
    errors: list[str],
) -> None:
    index_path = directory / "index.json"
    index = _read_json(index_path, f"{language}/index.json", errors)
    if not isinstance(index, dict):
        return
    if index.get("language") != language:
        errors.append(f"{language}/index.json: language must be {language!r}")
    artifacts = index.get("artifacts")
    if not isinstance(artifacts, list):
        errors.append(f"{language}/index.json: artifacts must be a list")
        return

    types = [
        item.get("artifact_type") if isinstance(item.get("artifact_type"), str) else "<invalid>"
        for item in artifacts
        if isinstance(item, dict)
    ]
    missing = sorted(set(CATALOG_TYPES) - set(types))
    extra = sorted(set(types) - set(CATALOG_TYPES), key=str)
    duplicates = sorted({item for item in types if types.count(item) > 1}, key=str)
    if len(artifacts) != len(CATALOG_TYPES) or missing or extra or duplicates:
        errors.append(
            f"{language}/index.json: expected exactly 13 catalog artifacts; "
            f"missing={missing}, extra={extra}, duplicates={duplicates}"
        )

    primary_paths: set[str] = set()
    for position, entry in enumerate(artifacts):
        label = f"{language}/index.json artifact {position + 1}"
        if not isinstance(entry, dict):
            errors.append(f"{label}: entry must be an object")
            continue
        artifact_type = entry.get("artifact_type")
        if artifact_type not in CATALOG_TYPES:
            continue
        primary = _verify_asset(directory, entry, label, errors)
        if primary is None:
            continue
        if primary.name in primary_paths:
            errors.append(f"{label}: duplicate primary path {primary.name!r}")
        primary_paths.add(primary.name)
        _validate_primary(primary, artifact_type, label, errors)

        raw_assets = entry.get("assets", [])
        if not isinstance(raw_assets, list):
            errors.append(f"{label}: assets must be a list")
            raw_assets = []
        sidecars: dict[str, Path] = {}
        for asset_position, asset in enumerate(raw_assets):
            asset_label = f"{label} asset {asset_position + 1}"
            if not isinstance(asset, dict):
                errors.append(f"{asset_label}: entry must be an object")
                continue
            role = str(asset.get("role") or "").strip()
            if not role:
                errors.append(f"{asset_label}: role is required")
                continue
            if role in sidecars:
                errors.append(f"{label}: duplicate asset role {role!r}")
                continue
            path = _verify_asset(directory, asset, asset_label, errors)
            if path is not None:
                sidecars[role] = path
                _validate_sidecar(path, role, asset_label, errors)

        if artifact_type in {"slide_deck", "video_overview"}:
            manifest = sidecars.get("manifest")
            if manifest is None:
                errors.append(f"{label}: manifest asset is required")
            elif language == "fa" and not _manifest_uses_kalameh(manifest):
                errors.append(f"{label}: Persian visual manifest must use font_family=Kalameh")
        if artifact_type == "video_overview":
            missing_roles = sorted({"captions", "timeline", "music_credits"} - set(sidecars))
            if missing_roles:
                errors.append(f"{label}: missing required video assets {missing_roles}")
            music_credits = sidecars.get("music_credits")
            if music_credits is not None:
                credit_text = music_credits.read_text(encoding="utf-8")
                required_credit_fields = (
                    "Provider: Jamendo",
                    "Source: https://www.jamendo.com/track/",
                    "License: https://creativecommons.org/licenses/",
                    "non-commercial",
                )
                if not all(field in credit_text for field in required_credit_fields):
                    errors.append(
                        f"{label}: music credits must include provider, source, license, "
                        "and non-commercial-use notice"
                    )
            _validate_video(primary, ffprobe, label, errors)
        if artifact_type == "audio_overview" and "transcript" not in sidecars:
            errors.append(f"{label}: transcript asset is required")
        language_text = _language_text(primary, artifact_type, sidecars)
        _validate_output_language(language_text, language, label, errors)


def _verify_asset(
    directory: Path,
    record: dict,
    label: str,
    errors: list[str],
) -> Path | None:
    raw_path = str(record.get("path") or "")
    if not raw_path or Path(raw_path).name != raw_path or _PRIVATE_REFERENCE.search(raw_path):
        errors.append(f"{label}: path must be a public relative basename")
        return None
    path = directory / raw_path
    if not path.is_file():
        errors.append(f"{label}: referenced file is missing: {raw_path}")
        return None
    expected_size = record.get("size")
    if type(expected_size) is not int or expected_size <= 0:
        errors.append(f"{label}: size must be a positive integer")
    elif path.stat().st_size != expected_size:
        errors.append(f"{label}: size mismatch for {raw_path}")
    expected_hash = str(record.get("sha256") or "").casefold()
    if not _SHA256.fullmatch(expected_hash):
        errors.append(f"{label}: sha256 must contain 64 hexadecimal characters")
    elif _sha256(path) != expected_hash:
        errors.append(f"{label}: sha256 mismatch for {raw_path}")
    return path


def _validate_primary(path: Path, artifact_type: str, label: str, errors: list[str]) -> None:
    if artifact_type in _MARKDOWN_TYPES:
        _nonempty_text(path, label, errors)
    elif artifact_type in _JSON_TYPES:
        _read_json(path, f"{label} output", errors, require_nonempty=True)
    elif artifact_type == "slide_deck":
        if not is_zipfile(path):
            errors.append(f"{label}: PowerPoint is not a ZIP package")
            return
        try:
            with ZipFile(path) as package:
                required = {"[Content_Types].xml", "ppt/presentation.xml"}
                if not required.issubset(package.namelist()):
                    errors.append(f"{label}: PowerPoint package is incomplete")
        except BadZipFile:
            errors.append(f"{label}: PowerPoint ZIP package is corrupt")
    elif artifact_type == "audio_overview":
        header = path.read_bytes()[:10]
        frame_sync = len(header) >= 2 and header[0] == 0xFF and header[1] & 0xE0 == 0xE0
        if not (header.startswith(b"ID3") or frame_sync):
            errors.append(f"{label}: audio output does not have MP3 magic")
    elif artifact_type == "video_overview":
        header = path.read_bytes()[:12]
        if len(header) < 8 or header[4:8] != b"ftyp":
            errors.append(f"{label}: video output does not have MP4 magic")


def _validate_sidecar(path: Path, role: str, label: str, errors: list[str]) -> None:
    if role in {"manifest", "timeline"}:
        _read_json(path, label, errors, require_nonempty=True)
    elif role in {"captions", "transcript", "music_credits"}:
        _nonempty_text(path, label, errors)


def _validate_video(
    path: Path,
    ffprobe: Sequence[str],
    label: str,
    errors: list[str],
) -> None:
    try:
        result = subprocess.run(
            [
                *ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration:stream=codec_type,codec_name,width,height",
                "-of",
                "json",
                str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=15,
        )
        payload = json.loads(result.stdout)
        format_payload = payload.get("format") if isinstance(payload, dict) else None
        streams = payload.get("streams") if isinstance(payload, dict) else None
        duration_value = (
            format_payload.get("duration") if isinstance(format_payload, dict) else None
        )
        duration = float(duration_value)
        streams = streams if isinstance(streams, list) else []
    except (
        FileNotFoundError,
        subprocess.SubprocessError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
    ):
        errors.append(f"{label}: ffprobe could not inspect the video")
        return
    if not 30 <= duration <= 120:
        errors.append(f"{label}: video duration must be 30-120 seconds, received {duration:.3f}")
    video = next(
        (item for item in streams if isinstance(item, dict) and item.get("codec_type") == "video"),
        {},
    )
    audio = next(
        (item for item in streams if isinstance(item, dict) and item.get("codec_type") == "audio"),
        {},
    )
    if video.get("codec_name") != "h264" or (video.get("width"), video.get("height")) != (
        1920,
        1080,
    ):
        errors.append(f"{label}: video stream must be H264 at 1920x1080")
    if audio.get("codec_name") != "aac":
        errors.append(f"{label}: audio stream must use AAC")


def _manifest_uses_kalameh(path: Path) -> bool:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False

    def values(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key == "font_family":
                    yield child
                yield from values(child)
        elif isinstance(value, list):
            for child in value:
                yield from values(child)

    return any(str(value).casefold() == "kalameh" for value in values(payload))


def _language_text(
    primary: Path,
    artifact_type: str,
    sidecars: dict[str, Path],
) -> str:
    try:
        if artifact_type in _JSON_TYPES:
            payload = json.loads(primary.read_text(encoding="utf-8"))

            def strings(value):
                if isinstance(value, dict):
                    for child in value.values():
                        yield from strings(child)
                elif isinstance(value, list):
                    for child in value:
                        yield from strings(child)
                elif isinstance(value, str):
                    yield value

            return "\n".join(strings(payload))
        if artifact_type in _MARKDOWN_TYPES:
            return primary.read_text(encoding="utf-8")
        if artifact_type == "slide_deck":
            with ZipFile(primary) as package:
                values = []
                for name in package.namelist():
                    if re.fullmatch(r"ppt/slides/slide\d+\.xml", name):
                        xml = package.read(name).decode("utf-8", errors="ignore")
                        values.extend(
                            html.unescape(value)
                            for value in re.findall(r"<a:t>(.*?)</a:t>", xml, flags=re.S)
                        )
                return "\n".join(values)
        if artifact_type == "video_overview" and sidecars.get("captions"):
            return sidecars["captions"].read_text(encoding="utf-8")
        if artifact_type == "audio_overview" and sidecars.get("transcript"):
            return sidecars["transcript"].read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, BadZipFile):
        return ""
    return ""


def _validate_output_language(
    text: str,
    language: str,
    label: str,
    errors: list[str],
) -> None:
    if not text.strip():
        errors.append(f"{label}: no audience-facing text was available for language validation")
        return
    if language == "fa":
        if not is_clean_persian(text, minimum_characters=20):
            errors.append(f"{label}: Persian output failed the language-integrity gate")
        return
    latin = len(re.findall(r"[A-Za-z]", text))
    persian = len(re.findall(r"[\u0600-\u06ff]", text))
    if latin < 20 or persian > max(10, latin // 3):
        errors.append(f"{label}: English output failed the language-integrity gate")


def _read_json(
    path: Path,
    label: str,
    errors: list[str],
    *,
    require_nonempty: bool = False,
):
    try:
        text = path.read_text(encoding="utf-8")
        payload = json.loads(text)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        errors.append(f"{label}: expected valid UTF-8 JSON")
        return None
    if _PRIVATE_REFERENCE.search(text):
        errors.append(f"{label}: contains a private identifier or local path")
    if require_nonempty and payload in ({}, [], "", None):
        errors.append(f"{label}: JSON output must be nonempty")
    return payload


def _nonempty_text(path: Path, label: str, errors: list[str]) -> None:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        errors.append(f"{label}: expected UTF-8 text")
        return
    if not text.strip():
        errors.append(f"{label}: text output must be nonempty")
    if _PRIVATE_REFERENCE.search(text):
        errors.append(f"{label}: contains a private identifier or local path")


def _sha256(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate the bilingual public SourceLens demo")
    parser.add_argument("--root", type=Path, default=Path("docs/demo/outputs"))
    parser.add_argument("--ffprobe", default="ffprobe")
    args = parser.parse_args(argv)
    errors = validate_public_demo(args.root, ffprobe=(args.ffprobe,))
    if errors:
        print("Public demo validation failed:")
        print("\n".join(f"- {error}" for error in errors))
        return 1
    print("Public demo validation passed: 26 artifacts across English and Persian.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
