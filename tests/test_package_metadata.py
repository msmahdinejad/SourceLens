from importlib.metadata import version

import paperlens
from paperlens.web.application import ApplicationContainer, create_application


def test_runtime_version_matches_installed_package() -> None:
    assert paperlens.__version__ == version("paperlens")


def test_api_uses_installed_package_version(tmp_path) -> None:
    container = ApplicationContainer(
        project_root=tmp_path,
        web_root=tmp_path,
        outputs_root=tmp_path / "outputs",
        startup=lambda: None,
    )

    app = create_application(container)

    assert app.version == paperlens.__version__
    assert app.title == "SourceLens"
    assert "PaperLens API" not in str(app.openapi())
