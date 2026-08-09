from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import paperlens.visual_engine_bridge as bridge_module
from paperlens import webapp
from paperlens.studio import StudioArtifact, StudioArtifactStore, normalize_studio_options
from paperlens.visual_artifacts import (
    PresentationRenderer,
    VideoOverviewRenderer,
    VisualArtifactService,
)
from paperlens.visual_engine_bridge import (
    VISUAL_ENGINE_CONTRACT_VERSION,
    VisualEngineBridge,
    VisualEngineUnavailable,
)
from paperlens.workspaces import WorkspaceRecord, WorkspaceStore


def test_default_renderer_paths_use_capability_names_and_keep_env_aliases(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    bridge = VisualEngineBridge()
    assert bridge.entrypoints["slide_deck"].parent.name == "deck_engine"
    assert bridge.entrypoints["video_overview"].parent.name == "video_engine"

    legacy = tmp_path / "legacy-entrypoint.mjs"
    monkeypatch.setenv("SOURCELENS_SLIDE_ENGINE_ENTRYPOINT", str(legacy))
    assert VisualEngineBridge().entrypoints["slide_deck"] == legacy


def _write_engine(path: Path) -> None:
    path.write_text(
        """
import fs from "node:fs";
import path from "node:path";

const value = (name) => process.argv[process.argv.indexOf(name) + 1];
const request = JSON.parse(fs.readFileSync(value("--request"), "utf8"));
const output = value("--output");
const result = value("--result");
fs.mkdirSync(path.join(output, "previews"), { recursive: true });
fs.mkdirSync(path.join(output, "diagnostics"), { recursive: true });
fs.writeFileSync(path.join(output, "presentation.pptx"), "pptx");
fs.writeFileSync(path.join(output, "previews", "slide-01.png"), "png");
fs.writeFileSync(
  path.join(output, "diagnostics", "render.json"),
  JSON.stringify({ requestContract: request.contract_version }),
);
console.log(JSON.stringify({
  type: "progress", stage: "rendering", progress: 63, message: "Rendering slides",
}));
fs.writeFileSync(result, JSON.stringify({
  contract_version: request.contract_version,
  status: "ready",
  engine: { name: "sourcelens-deck-renderer", version: "5.0.0" },
  outputs: { pptx_filename: "presentation.pptx" },
  previews: ["previews/slide-01.png"],
  diagnostics: ["diagnostics/render.json"],
  metadata: { slide_count: 1, editable: true },
  warnings: [],
}));
""".strip(),
        encoding="utf-8",
    )


def test_bridge_invokes_versioned_engine_and_atomically_publishes_assets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    engine = tmp_path / "engine.mjs"
    _write_engine(engine)
    events = []
    output = tmp_path / "artifact"
    bridge = VisualEngineBridge(slide_entrypoint=engine)
    processes = []
    real_popen = bridge_module.subprocess.Popen

    def capturing_popen(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(bridge_module.subprocess, "Popen", capturing_popen)

    result = bridge.render(
        artifact_type="slide_deck",
        artifact_id="artifact-123456abcdef",
        workspace_id="workspace",
        language="fa",
        plan={"title": "پژوهش", "slides": [{"title": "آغاز"}]},
        options={"slide_count": 1},
        output_dir=output,
        on_event=events.append,
    )

    assert result.contract_version == VISUAL_ENGINE_CONTRACT_VERSION
    assert result.outputs == {"pptx_filename": "presentation.pptx"}
    assert (output / "presentation.pptx").read_text(encoding="utf-8") == "pptx"
    assert (output / "previews" / "slide-01.png").is_file()
    assert (output / "diagnostics" / "render.json").is_file()
    assert result.manifest_path == output / "visual-engine-manifest.json"
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["contract_version"] == VISUAL_ENGINE_CONTRACT_VERSION
    assert manifest["engine"] == {"name": "sourcelens-deck-renderer", "version": "5.0.0"}
    assert {item["path"] for item in manifest["assets"]} == {
        "presentation.pptx",
        "previews/slide-01.png",
        "diagnostics/render.json",
    }
    assert all(len(item["sha256"]) == 64 for item in manifest["assets"])
    assert [(item.stage, item.progress) for item in events] == [("rendering", 63)]
    assert all(process.stdout is None or process.stdout.closed for process in processes)
    assert not any(path.name.startswith(".visual-engine-") for path in output.iterdir())


def test_bridge_reports_unavailable_renderer_prerequisites(tmp_path: Path):
    bridge = VisualEngineBridge(
        slide_entrypoint=tmp_path / "missing.mjs",
        node_command=tmp_path / "missing-node",
    )

    assert bridge.available("slide_deck").available is False
    with pytest.raises(VisualEngineUnavailable):
        bridge.render(
            artifact_type="slide_deck",
            artifact_id="artifact-123456abcdef",
            workspace_id="workspace",
            language="en",
            plan={"title": "Deck"},
            options={},
            output_dir=tmp_path / "artifact",
        )


def test_visual_service_fails_clearly_instead_of_switching_renderers(tmp_path: Path) -> None:
    store = StudioArtifactStore(tmp_path / "workspaces")
    artifact = store.save(
        StudioArtifact(
            artifact_id="artifact-deadbeef1234",
            workspace_id="workspace",
            artifact_type="slide_deck",
            title="Deck",
            status="ready",
            document_ids=["document"],
            options=normalize_studio_options("slide_deck", {"slide_count": 5}),
            data={
                "title": "Deck",
                "slides": [
                    {"layout": "big_statement", "title": "Start"},
                    {"layout": "closing_actions", "title": "Finish"},
                ],
            },
        )
    )
    bridge = VisualEngineBridge(
        slide_entrypoint=tmp_path / "missing.mjs",
        node_command=tmp_path / "missing-node",
    )
    service = VisualArtifactService(
        artifacts=store,
        presentations=PresentationRenderer(),
        videos=VideoOverviewRenderer(),
        bridge=bridge,
    )

    result = service.materialize(artifact)

    assert result.status == "failed"
    assert "entrypoint is missing" in result.error.lower()
    assert not store.asset_path(
        "workspace", artifact.artifact_id, "presentation.pptx"
    ).exists()


def test_visual_artifact_service_uses_the_versioned_engine_in_production(tmp_path: Path):
    engine = tmp_path / "engine.mjs"
    _write_engine(engine)
    store = StudioArtifactStore(tmp_path / "workspaces")
    artifact = store.save(
        StudioArtifact(
            artifact_id="artifact-123456abcdef",
            workspace_id="workspace",
            artifact_type="slide_deck",
            title="Deck",
            status="ready",
            document_ids=["document"],
            options=normalize_studio_options(
                "slide_deck",
                    {"slide_count": 5, "image_strategy": "none", "aspect_ratio": "wide"},
            ),
            data={
                "title": "Deck",
                "slides": [
                    {"layout": "cover_cinematic", "title": "Start"},
                    {"layout": "closing_actions", "title": "Finish"},
                ],
            },
        )
    )
    service = VisualArtifactService(
        artifacts=store,
        presentations=PresentationRenderer(),
        videos=VideoOverviewRenderer(),
        bridge=VisualEngineBridge(slide_entrypoint=engine),
    )

    result = service.materialize(artifact)

    assert result.status == "ready", result.error
    assert result.data["visual_engine"]["name"] == "sourcelens-deck-renderer"
    assert result.data["visual_engine_contract"] == VISUAL_ENGINE_CONTRACT_VERSION
    assert result.data["manifest_filename"] == "visual-engine-manifest.json"
    output = store.asset_path("workspace", artifact.artifact_id, "presentation.pptx")
    assert output.read_text(encoding="utf-8") == "pptx"


def test_visual_engine_sidecars_are_exposed_by_the_studio_api(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    store = StudioArtifactStore(tmp_path / "workspaces")
    workspaces = WorkspaceStore(tmp_path / "workspaces")
    workspaces.save(WorkspaceRecord(workspace_id="workspace", name="Workspace"))
    artifact = StudioArtifact(
        artifact_id="artifact-123456abcdef",
        workspace_id="workspace",
        artifact_type="slide_deck",
        title="Deck",
        status="ready",
        document_ids=["document"],
        data={
            "pptx_filename": "presentation.pptx",
            "manifest_filename": "visual-engine-manifest.json",
            "preview_filenames": ["previews/slide-01.png"],
            "diagnostic_filenames": ["diagnostics/render.json"],
        },
    )
    artifact = store.save(artifact)
    base = store.asset_path(artifact.workspace_id, artifact.artifact_id, "placeholder").parent
    (base / "previews").mkdir(parents=True)
    (base / "diagnostics").mkdir(parents=True)
    (base / "presentation.pptx").write_bytes(b"pptx")
    (base / "visual-engine-manifest.json").write_text(
        json.dumps(
            {
                "artifact_id": artifact.artifact_id,
                "artifact_type": "slide_deck",
                "metadata": {
                    "font_family": "Kalameh",
                    "direction": {"seed": artifact.artifact_id},
                },
            }
        ),
        encoding="utf-8",
    )
    (base / "previews" / "slide-01.png").write_bytes(b"png")
    (base / "diagnostics" / "render.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(webapp, "studio_artifact_store", lambda: store)
    monkeypatch.setattr(webapp, "workspace_store", lambda: workspaces)
    client = TestClient(webapp.app)

    payload = client.get(
        "/api/workspaces/workspace/studio/artifacts/artifact-123456abcdef"
    ).json()

    assert payload["manifest_url"].endswith("/manifest")
    assert payload["preview_urls"][0].endswith("/previews/0")
    assert payload["diagnostic_urls"][0].endswith("/diagnostics/0")
    manifest = client.get(payload["manifest_url"]).json()
    assert "artifact_id" not in manifest
    assert manifest["artifact_type"] == "slide_deck"
    assert manifest["metadata"]["font_family"] == "Kalameh"
    assert manifest["metadata"]["direction"]["seed"] == "public"
    assert client.get(payload["preview_urls"][0]).content == b"png"
    assert client.get(payload["diagnostic_urls"][0]).json() == {}


def test_video_sidecars_accept_safe_unicode_filenames(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    store = StudioArtifactStore(tmp_path / "workspaces")
    workspaces = WorkspaceStore(tmp_path / "workspaces")
    workspaces.save(WorkspaceRecord(workspace_id="workspace", name="Workspace"))
    artifact = store.save(
        StudioArtifact(
            artifact_id="artifact-123456abcdef",
            workspace_id="workspace",
            artifact_type="video_overview",
            title="Video",
            status="ready",
            document_ids=["document"],
            data={
                "captions_filename": "زیرنویس.srt",
                "timeline_filename": "خط-زمانی.json",
            },
        )
    )
    base = store.asset_path(
        artifact.workspace_id, artifact.artifact_id, "placeholder"
    ).parent
    (base / "زیرنویس.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nسلام\n", encoding="utf-8")
    (base / "خط-زمانی.json").write_text(
        json.dumps({"duration": 1, "direction": {"seed": artifact.artifact_id}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(webapp, "studio_artifact_store", lambda: store)
    monkeypatch.setattr(webapp, "workspace_store", lambda: workspaces)
    client = TestClient(webapp.app)

    captions = client.get(
        "/api/workspaces/workspace/studio/artifacts/artifact-123456abcdef/captions"
    )
    timeline = client.get(
        "/api/workspaces/workspace/studio/artifacts/artifact-123456abcdef/timeline"
    )

    assert captions.status_code == 200
    assert "سلام" in captions.text
    assert timeline.status_code == 200
    assert timeline.json() == {"duration": 1, "direction": {"seed": "public"}}


def test_visual_sidecar_endpoint_rejects_paths_outside_the_artifact(tmp_path: Path):
    store = StudioArtifactStore(tmp_path / "workspaces")
    artifact = StudioArtifact(
        artifact_id="artifact-123456abcdef",
        workspace_id="workspace",
        artifact_type="slide_deck",
        title="Deck",
        status="ready",
        document_ids=["document"],
        data={"preview_filenames": ["../secret.txt"]},
    )
    store.save(artifact)
    outside = tmp_path / "workspaces" / "workspace" / "artifacts" / "secret.txt"
    outside.write_text("secret", encoding="utf-8")

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(webapp, "studio_artifact_store", lambda: store)
        response = TestClient(webapp.app).get(
            "/api/workspaces/workspace/studio/artifacts/artifact-123456abcdef/previews/0"
        )

    assert response.status_code == 404
    assert b"secret" not in response.content


def test_bridge_cancellation_terminates_a_genuinely_running_engine(tmp_path: Path) -> None:
    engine = tmp_path / "long-engine.mjs"
    engine.write_text(
        """
import fs from "node:fs";
const value = (name) => process.argv[process.argv.indexOf(name) + 1];
fs.writeFileSync(value("--output") + "/running.pid", String(process.pid));
console.log(JSON.stringify({type:"progress",stage:"rendering",progress:10,message:"running"}));
setInterval(() => {}, 1000);
""".strip(),
        encoding="utf-8",
    )
    cancelled = threading.Event()
    output = tmp_path / "artifact"
    bridge = VisualEngineBridge(slide_entrypoint=engine, timeout_seconds=30)
    failure: list[BaseException] = []

    def run() -> None:
        try:
            bridge.render(
                artifact_type="slide_deck",
                artifact_id="artifact-123456abcdef",
                workspace_id="workspace",
                language="en",
                plan={"title": "Deck"},
                options={},
                output_dir=output,
                should_cancel=cancelled.is_set,
            )
        except BaseException as error:  # noqa: BLE001 - assertion captures worker failure
            failure.append(error)

    worker = threading.Thread(target=run)
    worker.start()
    deadline = time.monotonic() + 5
    while not list(output.rglob("running.pid")) and time.monotonic() < deadline:
        time.sleep(0.02)
    cancelled.set()
    worker.join(timeout=5)

    assert not worker.is_alive()
    assert failure and "cancel" in str(failure[0]).casefold()
