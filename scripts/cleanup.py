from __future__ import annotations

import argparse
import shutil
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DISPOSABLE_DIRECTORIES = (
    "tmp",
    "release",
    "dist",
    ".pytest_cache",
    ".ruff_cache",
    "htmlcov",
)
DISPOSABLE_FILES = (".coverage",)
# `outputs/` may contain the user's current artifacts. Generated release/build
# trees are disposable, but output cleanup requires an explicit artifact policy.
GENERATED_OUTPUT_GLOBS: tuple[str, ...] = ()
CACHE_ROOTS = ("src", "tests", "scripts")
LOG_GLOBS = ("*.log", "data/*.log", "outputs/*.log", "outputs/**/*.log")
PROTECTED_PATHS = (
    ".env",
    "data/workspaces",
    "data/fonts",
    "src/paperlens/assets/fonts/Kalameh",
    "src/paperlens/assets/fonts/IRANYekanX",
    "src/paperlens/web/fonts",
)


def _within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def validate_target(root: Path, target: Path) -> Path:
    root = root.resolve()
    target = target.resolve()
    if target == root or not _within(target, root):
        raise ValueError(f"Refusing broad or external cleanup target: {target}")
    for relative in PROTECTED_PATHS:
        protected = (root / relative).resolve()
        if target == protected or _within(target, protected):
            raise ValueError(f"Refusing protected cleanup target: {target}")
    return target


def cleanup_candidates(root: Path = PROJECT_ROOT) -> list[Path]:
    root = root.resolve()
    candidates = [root / relative for relative in (*DISPOSABLE_DIRECTORIES, *DISPOSABLE_FILES)]
    for pattern in (*GENERATED_OUTPUT_GLOBS, *LOG_GLOBS):
        candidates.extend(root.glob(pattern))
    for base_name in CACHE_ROOTS:
        base = root / base_name
        if not base.is_dir():
            continue
        for pattern in ("**/__pycache__", "**/*.pyc", "**/*.pyo"):
            candidates.extend(base.glob(pattern))
    safe: list[Path] = []
    for path in sorted(set(candidates), key=lambda item: (len(item.parts), str(item))):
        if not path.exists():
            continue
        try:
            target = validate_target(root, path)
        except ValueError:
            continue
        if any(_within(target, parent) for parent in safe if parent.is_dir()):
            continue
        safe.append(target)
    return safe


def clean(root: Path = PROJECT_ROOT, *, apply: bool = False) -> list[Path]:
    candidates = cleanup_candidates(root)
    if not apply:
        return candidates
    removed = []
    for path in sorted(candidates, key=lambda item: len(item.parts), reverse=True):
        try:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink(missing_ok=True)
        except OSError:
            continue
        removed.append(path)
    return removed


def main() -> int:
    parser = argparse.ArgumentParser(description="Preview SourceLens disposable-file cleanup.")
    parser.add_argument("--apply", action="store_true", help="Delete listed paths.")
    args = parser.parse_args()
    candidates = clean(apply=args.apply)
    action = "removed" if args.apply else "would remove"
    for path in candidates:
        print(f"{action}: {path.relative_to(PROJECT_ROOT).as_posix()}")
    if not args.apply:
        print("Dry run only. Pass --apply to remove exactly these paths.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
