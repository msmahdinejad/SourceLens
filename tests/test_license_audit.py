from __future__ import annotations

from email.message import Message
from pathlib import Path

from scripts.license_audit import _python_license, audit_environment, is_strong_copyleft

ROOT = Path(__file__).parents[1]


class _Distribution:
    def __init__(self, name: str, version: str) -> None:
        self.metadata = Message()
        self.metadata["Name"] = name
        self.version = version


def test_strong_copyleft_classifier_respects_dual_license_choice() -> None:
    assert is_strong_copyleft("AGPL-3.0-only") is True
    assert is_strong_copyleft("GNU General Public License v3") is True
    assert is_strong_copyleft("MIT OR GPL-3.0-or-later") is False
    assert is_strong_copyleft("MPL-2.0") is False


def test_verified_metadata_gap_is_exactly_version_scoped() -> None:
    assert _python_license(_Distribution("cuda-toolkit", "13.0.3.0")) == (
        "NVIDIA CUDA Toolkit EULA"
    )
    assert _python_license(_Distribution("cuda-toolkit", "13.0.4.0")) == ""
    assert _python_license(_Distribution("unverified-package", "13.0.3.0")) == ""


def test_resolved_python_and_node_environment_has_auditable_licenses() -> None:
    report = audit_environment(ROOT)

    assert report.violations == []
    assert report.python_packages >= 100
    assert report.node_packages >= 20
    assert report.pdfium_notice_files >= 10
