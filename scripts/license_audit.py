from __future__ import annotations

import json
import re
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, distribution, distributions
from pathlib import Path

_STRONG_COPYLEFT = re.compile(r"(?:A?GPL|GNU (?:AFFERO )?GENERAL PUBLIC LICENSE)", re.I)
_PERMISSIVE_CHOICE = re.compile(r"\b(?:MIT|BSD|APACHE|ISC|PSF|ZLIB|MPL)-?", re.I)
_NODE_LOCKS = (
    "src/paperlens/renderers/deck_engine/package-lock.json",
    "src/paperlens/renderers/video_engine/package-lock.json",
)
_VERIFIED_LICENSE_METADATA_GAPS = {
    # NVIDIA publishes this 2,512-byte meta-package without License metadata or payload files.
    # The locked wheel (sha256:d693caaa261214ddd7dbb60d68e71cbed884e68c2be7509778f3051da0b91c3f)
    # only selects CUDA components, whose use remains subject to NVIDIA's CUDA Toolkit EULA.
    ("cuda-toolkit", "13.0.3.0"): "NVIDIA CUDA Toolkit EULA",
}


@dataclass(frozen=True, slots=True)
class LicenseAuditReport:
    python_packages: int
    node_packages: int
    pdfium_notice_files: int
    violations: list[str]


def is_strong_copyleft(license_text: str) -> bool:
    normalized = " ".join(license_text.upper().split())
    if not _STRONG_COPYLEFT.search(normalized):
        return False
    return not (" OR " in normalized and _PERMISSIVE_CHOICE.search(normalized))


def audit_environment(root: Path) -> LicenseAuditReport:
    violations: list[str] = []
    python_count = 0
    for package in sorted(distributions(), key=lambda item: _package_name(item).casefold()):
        name = _package_name(package)
        if name.casefold() == "paperlens":
            continue
        python_count += 1
        license_text = _python_license(package)
        if not license_text:
            violations.append(f"Python package has no license metadata: {name} {package.version}")
        elif is_strong_copyleft(license_text):
            violations.append(
                f"Strong-copyleft Python package: {name} {package.version} ({license_text})"
            )

    node_count = 0
    for relative in _NODE_LOCKS:
        lock_path = root / relative
        payload = json.loads(lock_path.read_text(encoding="utf-8"))
        for package_path, package in sorted(payload.get("packages", {}).items()):
            if not package_path:
                continue
            node_count += 1
            license_text = str(package.get("license") or "").strip()
            if package.get("link"):
                linked = lock_path.parent / str(package.get("resolved") or "") / "package.json"
                if linked.is_file():
                    license_text = str(
                        json.loads(linked.read_text(encoding="utf-8")).get("license") or ""
                    ).strip()
            label = f"{relative}:{package_path}"
            if not license_text:
                violations.append(f"Node package has no license metadata: {label}")
            elif is_strong_copyleft(license_text):
                violations.append(f"Strong-copyleft Node package: {label} ({license_text})")

    pdfium_notices = _pdfium_notice_count()
    if pdfium_notices < 10:
        violations.append("pypdfium2 wheel is missing bundled PDFium dependency notices")
    try:
        distribution("pymupdf")
    except PackageNotFoundError:
        pass
    else:
        violations.append("PyMuPDF remains installed")
    if re.search(r'(?m)^name = "pymupdf"$', (root / "uv.lock").read_text(encoding="utf-8")):
        violations.append("PyMuPDF remains locked")
    return LicenseAuditReport(
        python_packages=python_count,
        node_packages=node_count,
        pdfium_notice_files=pdfium_notices,
        violations=sorted(violations),
    )


def _package_name(package) -> str:
    return str(package.metadata.get("Name") or "unknown")


def _python_license(package) -> str:
    explicit = str(
        package.metadata.get("License-Expression") or package.metadata.get("License") or ""
    ).strip()
    if explicit:
        return explicit
    classifiers = [
        value.removeprefix("License :: ").strip()
        for value in package.metadata.get_all("Classifier", [])
        if value.startswith("License :: ")
    ]
    if classifiers:
        return " OR ".join(classifiers)
    for filename in package.metadata.get_all("License-File", []):
        for candidate in (f"licenses/{Path(filename).name}", filename):
            license_text = package.read_text(candidate)
            if license_text:
                return license_text
    return _VERIFIED_LICENSE_METADATA_GAPS.get(
        (_package_name(package).casefold(), str(package.version)), ""
    )


def _pdfium_notice_count() -> int:
    try:
        package = distribution("pypdfium2")
    except PackageNotFoundError:
        return 0
    return sum(
        "build_licenses" in str(path).replace("\\", "/").casefold()
        for path in package.files or []
    )


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    report = audit_environment(root)
    if report.violations:
        print("License audit failed:")
        print("\n".join(report.violations))
        return 1
    print(
        "License audit passed: "
        f"{report.python_packages} Python packages, "
        f"{report.node_packages} Node packages, "
        f"{report.pdfium_notice_files} PDFium notices."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
