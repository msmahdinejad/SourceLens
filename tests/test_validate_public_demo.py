from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from zipfile import ZipFile

from scripts.validate_public_demo import CATALOG_TYPES, validate_public_demo


def test_bilingual_public_demo_accepts_complete_verified_outputs(tmp_path: Path) -> None:
    probe = _fake_ffprobe(tmp_path)
    for language in ("en", "fa"):
        _write_language_fixture(tmp_path, language)

    assert validate_public_demo(tmp_path, ffprobe=(sys.executable, str(probe))) == []


def test_bilingual_public_demo_rejects_incomplete_or_private_outputs(tmp_path: Path) -> None:
    probe = _fake_ffprobe(tmp_path)
    for language in ("en", "fa"):
        _write_language_fixture(tmp_path, language)
    english = _index(tmp_path, "en")
    english["artifacts"] = [
        item for item in english["artifacts"] if item["artifact_type"] != "research_gap"
    ]
    concept_map = next(
        item for item in english["artifacts"] if item["artifact_type"] == "concept_map"
    )
    concept_path = tmp_path / "en" / concept_map["path"]
    concept_path.write_text(json.dumps({"source": r"C:\Users\private\workspace.json"}))
    concept_map.update(_digest(concept_path))
    next(item for item in english["artifacts"] if item["artifact_type"] == "report")[
        "artifact_type"
    ] = []
    _save_index(tmp_path, "en", english)

    errors = validate_public_demo(tmp_path, ffprobe=(sys.executable, str(probe)))

    assert any("expected exactly 13 catalog artifacts" in error for error in errors)
    assert any("contains a private identifier or local path" in error for error in errors)


def test_bilingual_public_demo_rejects_bad_video_and_persian_font(tmp_path: Path) -> None:
    probe = _fake_ffprobe(tmp_path, duration=61, video_codec="vp9", audio_codec="mp3")
    for language in ("en", "fa"):
        _write_language_fixture(tmp_path, language)
    persian = _index(tmp_path, "fa")
    deck = next(item for item in persian["artifacts"] if item["artifact_type"] == "slide_deck")
    manifest = next(item for item in deck["assets"] if item["role"] == "manifest")
    manifest_path = tmp_path / "fa" / manifest["path"]
    manifest_path.write_text(json.dumps({"metadata": {"font_family": "Vazirmatn"}}))
    manifest.update(_digest(manifest_path))
    video = next(item for item in persian["artifacts"] if item["artifact_type"] == "video_overview")
    video["assets"] = [item for item in video["assets"] if item["role"] != "timeline"]
    _save_index(tmp_path, "fa", persian)

    errors = validate_public_demo(tmp_path, ffprobe=(sys.executable, str(probe)))

    assert any("font_family=Kalameh" in error for error in errors)
    assert any("missing required video assets ['timeline']" in error for error in errors)
    assert sum("video duration must be" in error for error in errors) == 2
    assert sum("video stream must be H264" in error for error in errors) == 2
    assert sum("audio stream must use AAC" in error for error in errors) == 2


def test_bilingual_public_demo_rejects_cross_script_persian_output(tmp_path: Path) -> None:
    probe = _fake_ffprobe(tmp_path)
    for language in ("en", "fa"):
        _write_language_fixture(tmp_path, language)
    persian = _index(tmp_path, "fa")
    infographic = next(
        item for item in persian["artifacts"] if item["artifact_type"] == "infographic"
    )
    path = tmp_path / "fa" / infographic["path"]
    path.write_text(
        json.dumps(
            {"title": "این خروجی فارسی است اما وال‌مارت宣佈 واژه آلوده دارد."},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    infographic.update(_digest(path))
    _save_index(tmp_path, "fa", persian)

    errors = validate_public_demo(tmp_path, ffprobe=(sys.executable, str(probe)))

    assert any("Persian output failed the language-integrity gate" in error for error in errors)


def _write_language_fixture(root: Path, language: str) -> None:
    directory = root / language
    directory.mkdir()
    entries = []
    prose = (
        "این خروجی فارسی است و برای ارزیابی دقیق سامانه آماده شده است. "
        "شواهد برای کاربر به صورت روشن و منظم توضیح داده می‌شود."
        if language == "fa"
        else "This English output presents clear, verified evidence for a complete evaluation."
    )
    for artifact_type in CATALOG_TYPES:
        if artifact_type in {"report", "comparison", "research_gap"}:
            path = directory / f"{artifact_type}.md"
            path.write_text(f"# {prose}\n\n{prose}\n", encoding="utf-8")
        elif artifact_type == "slide_deck":
            path = directory / "slide-deck.pptx"
            with ZipFile(path, "w") as package:
                package.writestr("[Content_Types].xml", "<Types />")
                package.writestr("ppt/presentation.xml", "<p:presentation />")
                package.writestr(
                    "ppt/slides/slide1.xml",
                    f'<p:sld xmlns:a="a"><a:t>{prose}</a:t></p:sld>',
                )
        elif artifact_type == "audio_overview":
            path = directory / "audio-overview.mp3"
            path.write_bytes(b"ID3\x04\x00\x00\x00\x00\x00\x00audio")
        elif artifact_type == "video_overview":
            path = directory / "video-overview.mp4"
            path.write_bytes(b"\x00\x00\x00\x18ftypisomvideo")
        else:
            path = directory / f"{artifact_type}.json"
            path.write_text(json.dumps({"title": prose}, ensure_ascii=False), encoding="utf-8")

        entry = {"artifact_type": artifact_type, **_digest(path), "assets": []}
        if artifact_type in {"slide_deck", "video_overview"}:
            manifest = directory / f"{artifact_type}-manifest.json"
            manifest.write_text(
                json.dumps(
                    {"metadata": {"font_family": "Kalameh" if language == "fa" else "Aptos"}}
                ),
                encoding="utf-8",
            )
            entry["assets"] = [{"role": "manifest", **_digest(manifest)}]
        if artifact_type == "video_overview":
            captions = directory / "video-overview.srt"
            captions.write_text(
                f"1\n00:00:00,000 --> 00:00:02,000\n{prose}\n", encoding="utf-8"
            )
            timeline = directory / "video-overview-timeline.json"
            timeline.write_text(
                json.dumps({"scenes": [{"duration_seconds": 60}]}), encoding="utf-8"
            )
            music_credits = directory / "audio-credits.txt"
            music_credits.write_text(
                "Track: Demo\nArtist: Example\nProvider: Jamendo\n"
                "Source: https://www.jamendo.com/track/1\n"
                "License: https://creativecommons.org/licenses/by-nc/3.0/\n"
                "Usage note: non-commercial use only.\n",
                encoding="utf-8",
            )
            entry["assets"].extend(
                [
                    {"role": "captions", **_digest(captions)},
                    {"role": "timeline", **_digest(timeline)},
                    {"role": "music_credits", **_digest(music_credits)},
                ]
            )
        if artifact_type == "audio_overview":
            transcript = directory / "audio-overview-transcript.txt"
            transcript.write_text(prose, encoding="utf-8")
            entry["assets"].append({"role": "transcript", **_digest(transcript)})
        entries.append(entry)
    (directory / "index.json").write_text(
        json.dumps({"language": language, "artifacts": entries}), encoding="utf-8"
    )


def _digest(path: Path) -> dict[str, str | int]:
    content = path.read_bytes()
    return {
        "path": path.name,
        "size": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }


def _index(root: Path, language: str) -> dict:
    return json.loads((root / language / "index.json").read_text(encoding="utf-8"))


def _save_index(root: Path, language: str, value: dict) -> None:
    (root / language / "index.json").write_text(json.dumps(value), encoding="utf-8")


def _fake_ffprobe(
    root: Path,
    *,
    duration: float = 60,
    video_codec: str = "h264",
    audio_codec: str = "aac",
) -> Path:
    script = root / "fake_ffprobe.py"
    script.write_text(
        "import json\n"
        f"print(json.dumps({{'format': {{'duration': '{duration}'}}, 'streams': ["
        f"{{'codec_type': 'video', 'codec_name': '{video_codec}', 'width': 1920, 'height': 1080}},"
        f"{{'codec_type': 'audio', 'codec_name': '{audio_codec}'}}]}}))\n",
        encoding="utf-8",
    )
    return script
