from __future__ import annotations

import shutil
from pathlib import Path

if __package__:
    from .repository_policy import PROJECT_ROOT, audit_public_tree, release_candidates
else:
    from repository_policy import PROJECT_ROOT, audit_public_tree, release_candidates

RELEASE_ROOT = PROJECT_ROOT / "release"
TARGET = RELEASE_ROOT / "SourceLens"
def _validated_target(target: Path) -> Path:
    target = target.resolve()
    release_root = RELEASE_ROOT.resolve()
    if target == PROJECT_ROOT.resolve() or target.parent != release_root:
        raise ValueError(f"Refusing unsafe release target: {target}")
    return target


def build() -> Path:
    target = _validated_target(TARGET)
    RELEASE_ROOT.mkdir(exist_ok=True)
    if target.exists():
        shutil.rmtree(target)
    target.mkdir()
    for source in release_candidates():
        destination = target / source.relative_to(PROJECT_ROOT)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)

    files = sorted(
        path.relative_to(target).as_posix() for path in target.rglob("*") if path.is_file()
    )
    (target / "RELEASE_MANIFEST.txt").write_text(
        "SourceLens clean source release\n"
        "Only repository-policy allowlisted files are included. Credentials, runtime data, "
        "uploaded documents, private generated artifacts, local fonts, caches, build output, "
        "and downloaded demo papers are excluded. Curated public demo outputs are included.\n\n"
        + "\n".join(files)
        + "\n",
        encoding="utf-8",
    )
    violations = audit_public_tree(target)
    if violations:
        raise RuntimeError("Release audit failed:\n" + "\n".join(violations))
    archive = RELEASE_ROOT / "SourceLens-final.zip"
    archive.unlink(missing_ok=True)
    shutil.make_archive(str(archive.with_suffix("")), "zip", RELEASE_ROOT, target.name)
    return archive


def audit(target: Path = TARGET) -> list[str]:
    return audit_public_tree(target)


if __name__ == "__main__":
    print(build())
