from __future__ import annotations

import argparse
import re
import subprocess
import tarfile
import zipfile
from pathlib import Path, PurePosixPath

PROJECT_ROOT = Path(__file__).resolve().parents[1]

TRACKED_ROOT_FILES = (
    ".editorconfig",
    ".env.example",
    ".gitignore",
    "AGENTS.md",
    "CONTEXT.md",
    "CONTRIBUTING.md",
    "LICENSE",
    "README.md",
    "SECURITY.md",
    "THIRD_PARTY_LICENSES.md",
    "providers.example.json",
    "pyproject.toml",
    "uv.lock",
)
TRACKED_ROOT_DIRECTORIES = (
    ".github",
    "docs",
    "fixtures",
    "plans",
    "scripts",
    "src",
    "tests",
)
TRACKED_DATA_FILES: tuple[str, ...] = ()
TRACKED_PAPER_FILES = ("papers/README.md", "papers/manifest.json")
TRACKED_OUTPUT_FILES = ("outputs/.gitkeep",)
PUBLIC_DEMO_OUTPUTS = "docs/demo/outputs"

PRIVATE_PATHS = (
    ".env",
    ".venv",
    ".agents",
    "data/cache",
    "data/chroma",
    "data/fonts",
    "data/traces",
    "data/workspaces",
    "dist",
    "models",
    "outputs",
    "release",
    "tmp",
)
LOCAL_FONT_PATHS = (
    "src/paperlens/assets/fonts/Kalameh",
    "src/paperlens/assets/fonts/IRANYekanX",
    "src/paperlens/web/fonts",
)
FORBIDDEN_PARTS = {
    ".git",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "node_modules",
}
TEXT_SUFFIXES = {
    "",
    ".css",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".md",
    ".mjs",
    ".py",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}
SECRET_PATTERNS = (
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"AIza[0-9A-Za-z_-]{35}"),
    re.compile(r"gh[pousr]_[0-9A-Za-z]{36,}"),
    re.compile(r"(?<![0-9A-Za-z])sk-[0-9A-Za-z_-]{20,}"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _under(relative: str, parent: str) -> bool:
    return relative == parent or relative.startswith(f"{parent}/")


def _is_public_demo_pdf(relative: str) -> bool:
    return _under(relative, PUBLIC_DEMO_OUTPUTS) and relative.casefold().endswith(".pdf")


def is_public_candidate(relative: str) -> bool:
    if any(part in FORBIDDEN_PARTS for part in Path(relative).parts):
        return False
    if any(_under(relative, path) for path in LOCAL_FONT_PATHS):
        return False
    if relative.casefold().endswith((".log", ".pyc", ".pyo", ".zip")):
        return False
    return not relative.casefold().endswith(".pdf") or _is_public_demo_pdf(relative)


def tracked_candidates(root: Path = PROJECT_ROOT) -> list[Path]:
    root = root.resolve()
    candidates: list[Path] = []
    explicit = (
        *TRACKED_ROOT_FILES,
        *TRACKED_DATA_FILES,
        *TRACKED_PAPER_FILES,
        *TRACKED_OUTPUT_FILES,
    )
    for relative in explicit:
        path = root / relative
        if path.is_file() and is_public_candidate(relative):
            candidates.append(path)
    for directory in TRACKED_ROOT_DIRECTORIES:
        base = root / directory
        if not base.is_dir():
            continue
        candidates.extend(
            path
            for path in base.rglob("*")
            if path.is_file() and is_public_candidate(_relative(path, root))
        )
    tracked = _git_tracked_paths(root)
    if tracked is not None:
        candidates = [path for path in candidates if _relative(path, root) in tracked]
    return sorted(set(candidates), key=lambda path: _relative(path, root).casefold())


def _git_tracked_paths(root: Path) -> set[str] | None:
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"],
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        return None
    return {
        item.decode("utf-8", errors="surrogateescape").replace("\\", "/")
        for item in result.stdout.split(b"\0")
        if item
    }


def release_candidates(root: Path = PROJECT_ROOT) -> list[Path]:
    root = root.resolve()
    placeholders = set(TRACKED_OUTPUT_FILES)
    return [path for path in tracked_candidates(root) if _relative(path, root) not in placeholders]


def scan_secrets(paths: list[Path], root: Path = PROJECT_ROOT) -> list[str]:
    root = root.resolve()
    violations: list[str] = []
    for path in paths:
        if path.suffix.casefold() not in TEXT_SUFFIXES or path.stat().st_size > 2_000_000:
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for pattern in SECRET_PATTERNS:
            if pattern.search(content):
                violations.append(f"{_relative(path, root)}: potential secret ({pattern.pattern})")
    return violations


def audit_candidates(root: Path = PROJECT_ROOT) -> list[str]:
    root = root.resolve()
    candidates = tracked_candidates(root)
    violations = scan_secrets(candidates, root)
    required = (
        root / "src/paperlens/assets/fonts/Vazirmatn/OFL.txt",
        root / "THIRD_PARTY_LICENSES.md",
    )
    violations.extend(
        f"missing required public-license file: {_relative(path, root)}"
        for path in required
        if not path.is_file()
    )
    return sorted(violations)


def audit_public_tree(root: Path) -> list[str]:
    root = root.resolve()
    violations: list[str] = []
    files = [path for path in root.rglob("*") if path.is_file()]
    for path in files:
        relative = _relative(path, root)
        parts = set(Path(relative).parts)
        if parts & FORBIDDEN_PARTS:
            violations.append(relative)
        if any(_under(relative, local_font) for local_font in LOCAL_FONT_PATHS):
            violations.append(relative)
        if relative == ".env" or (relative.startswith(".env.") and relative != ".env.example"):
            violations.append(relative)
        if relative.casefold().endswith((".log", ".pyc", ".pyo", ".zip")) or (
            relative.casefold().endswith(".pdf") and not _is_public_demo_pdf(relative)
        ):
            violations.append(relative)
        if (
            any(_under(relative, private) for private in PRIVATE_PATHS)
            and relative != "outputs/.gitkeep"
        ):
            violations.append(relative)
    violations.extend(scan_secrets(files, root))
    if (root / "pyproject.toml").is_file():
        for relative in (
            "THIRD_PARTY_LICENSES.md",
            "src/paperlens/assets/fonts/Vazirmatn/OFL.txt",
        ):
            if not (root / relative).is_file():
                violations.append(f"missing required public-license file: {relative}")
    return sorted(set(violations))


def audit_archive(path: Path) -> list[str]:
    if path.name.casefold().endswith((".whl", ".zip")):
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
    else:
        with tarfile.open(path, "r:*") as archive:
            names = archive.getnames()

    violations: list[str] = []
    files = [name.replace("\\", "/") for name in names if not name.endswith("/")]
    for name in files:
        parts = PurePosixPath(name).parts
        lowered = tuple(part.casefold() for part in parts)
        normalized = "/".join(lowered)
        public_demo_asset = "/docs/demo/outputs/" in f"/{normalized}"
        if name.startswith("/") or ".." in parts:
            violations.append(name)
            continue
        if set(lowered) & {".git", ".venv", "__pycache__", "node_modules", "tmp"} or (
            "outputs" in lowered and not public_demo_asset
        ):
            violations.append(name)
        if set(lowered) & {"kalameh", "iranyekanx"}:
            violations.append(name)
        if ".env" in lowered or "providers.json" in lowered:
            violations.append(name)
        public_demo_pdf = public_demo_asset and name.casefold().endswith(".pdf")
        if name.casefold().endswith((".log", ".pyc", ".pyo")) or (
            name.casefold().endswith(".pdf") and not public_demo_pdf
        ):
            violations.append(name)
        for parent in ("cache", "chroma", "fonts", "traces", "workspaces"):
            if any(
                left == "data" and right == parent
                for left, right in zip(lowered, lowered[1:], strict=False)
            ):
                violations.append(name)
    if not any(name.casefold().endswith("vazirmatn/ofl.txt") for name in files):
        violations.append("missing required public-license file: Vazirmatn/OFL.txt")
    if not any(
        "vazirmatn" in (part.casefold() for part in PurePosixPath(name).parts)
        and name.casefold().endswith(".ttf")
        for name in files
    ):
        violations.append("missing required public font: Vazirmatn TTF")
    return sorted(set(violations))


def _archive_paths(inputs: list[Path]) -> list[Path]:
    paths: list[Path] = []
    for item in inputs:
        if item.is_dir():
            paths.extend(
                path
                for path in item.rglob("*")
                if path.is_file()
                and path.name.casefold().endswith((".tar.gz", ".whl", ".zip"))
            )
        else:
            paths.append(item)
    return sorted(set(paths))


def main() -> int:
    parser = argparse.ArgumentParser(description="Inventory and audit SourceLens public files.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("inventory")
    subparsers.add_parser("audit")
    tree = subparsers.add_parser("audit-tree")
    tree.add_argument("path", type=Path)
    archives = subparsers.add_parser("audit-archive")
    archives.add_argument("paths", nargs="+", type=Path)
    args = parser.parse_args()

    if args.command == "inventory":
        for path in tracked_candidates():
            print(_relative(path, PROJECT_ROOT))
        return 0
    if args.command == "audit-archive":
        paths = _archive_paths(args.paths)
        violations = [
            f"{path}: {violation}" for path in paths for violation in audit_archive(path)
        ]
        if violations:
            print("Package archive audit failed:")
            print("\n".join(violations))
            return 1
        print(f"Package archive audit passed: {len(paths)} archive(s).")
        return 0
    violations = audit_candidates() if args.command == "audit" else audit_public_tree(args.path)
    if violations:
        print("Public repository audit failed:")
        print("\n".join(violations))
        return 1
    print("Public repository audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
