from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from paperlens import webapp
from paperlens.studio import StudioArtifact, StudioArtifactStore
from paperlens.workspaces import WorkspaceRecord, WorkspaceStore


def _artifact(
    artifact_id: str,
    artifact_type: str = "report",
    *,
    status: str = "queued",
    stage: str = "queued",
    progress: int = 0,
) -> StudioArtifact:
    return StudioArtifact(
        artifact_id=artifact_id,
        workspace_id="workspace",
        artifact_type=artifact_type,
        title=f"{artifact_type} test",
        status=status,
        stage=stage,
        progress=progress,
        document_ids=["document"],
    )


def test_sqlite_store_migrates_json_without_removing_the_reversible_source(tmp_path: Path) -> None:
    root = tmp_path / "workspaces"
    legacy = root / "workspace" / "artifacts" / "artifact-111111111111.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(
        _artifact("artifact-111111111111", status="ready", stage="ready", progress=100)
        .model_copy(
            update={
                "data": {
                    "events": [
                        {
                            "event_id": "legacy-1",
                            "kind": "audio_ready",
                            "message": "Episode ready",
                            "progress": 100,
                            "created_at": "2026-01-02T03:04:05+00:00",
                        }
                    ]
                }
            }
        )
        .model_dump_json(indent=2),
        encoding="utf-8",
    )

    store = StudioArtifactStore(root)

    assert store.get("workspace", "artifact-111111111111").status == "ready"
    assert legacy.is_file()
    assert store.database_path.is_file()
    with sqlite3.connect(store.database_path) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert {"artifacts", "jobs", "attempts", "events"} <= tables
    [event] = store.events("workspace", "artifact-111111111111")
    assert event["stage"] == "audio_ready"
    assert event["status"] == "completed"
    assert event["timestamp"] == "2026-01-02T03:04:05+00:00"
    assert len(StudioArtifactStore(root).events("workspace", "artifact-111111111111")) == 1


def test_cancel_is_idempotent_and_stale_progress_cannot_overwrite_it(tmp_path: Path) -> None:
    store = StudioArtifactStore(tmp_path / "workspaces")
    queued = store.save(_artifact("artifact-222222222222"))
    stale = store.get("workspace", queued.artifact_id)

    cancelled = store.request_cancel("workspace", queued.artifact_id)
    overwritten = store.save(
        stale.model_copy(
            update={"status": "ready", "stage": "ready", "progress": 100}
        )
    )

    assert cancelled.status == overwritten.status == "cancelled"
    assert overwritten.revision == cancelled.revision
    assert store.request_cancel("workspace", queued.artifact_id).revision == cancelled.revision


def test_retry_queue_resets_failed_progress_for_live_updates(tmp_path: Path) -> None:
    store = StudioArtifactStore(tmp_path / "workspaces")
    failed = store.save(
        _artifact(
            "artifact-292929292929",
            "video_overview",
            status="failed",
            stage="render_failed",
            progress=82,
        )
    )

    retried = store.save(
        failed.model_copy(
            update={"status": "queued", "stage": "retry_queued", "progress": 0}
        )
    )

    assert retried.progress == 0


def test_stale_terminal_save_cannot_overwrite_a_newer_ready_revision(tmp_path: Path) -> None:
    store = StudioArtifactStore(tmp_path / "workspaces")
    original = store.save(
        _artifact(
            "artifact-232323232323",
            "video_overview",
            status="generating",
            stage="rendering",
            progress=80,
        )
    )
    ready = store.save(
        original.model_copy(
            update={"status": "ready", "stage": "ready", "progress": 100}
        )
    )

    stale_failure = store.save(
        original.model_copy(
            update={"status": "failed", "stage": "failed", "progress": 80}
        )
    )

    assert stale_failure.status == "ready"
    assert stale_failure.revision == ready.revision
    assert store.get("workspace", original.artifact_id).status == "ready"


def test_stale_prior_attempt_cannot_overwrite_a_newer_retry_revision(tmp_path: Path) -> None:
    store = StudioArtifactStore(tmp_path / "workspaces")
    original = store.save(
        _artifact(
            "artifact-242424242424",
            "video_overview",
            status="generating",
            stage="rendering",
            progress=80,
        )
    )
    failed = store.save(
        original.model_copy(
            update={"status": "failed", "stage": "failed", "progress": 82}
        )
    )
    retried = store.save(
        failed.model_copy(
            update={"status": "queued", "stage": "retry_queued", "progress": 0}
        )
    )

    delayed = store.save(
        failed.model_copy(
            update={"status": "failed", "stage": "failed", "progress": 82}
        )
    )

    assert delayed.status == "queued"
    assert delayed.stage == "retry_queued"
    assert delayed.revision == retried.revision
    assert store.get("workspace", original.artifact_id).status == "queued"


@pytest.mark.parametrize("artifact_type", ["audio_overview", "slide_deck", "video_overview"])
def test_live_events_are_append_only_and_visible_for_every_studio_media_type(
    tmp_path: Path, artifact_type: str
) -> None:
    store = StudioArtifactStore(tmp_path / "workspaces")
    artifact = store.save(_artifact("artifact-333333333333", artifact_type))

    first = store.record_event(
        artifact.workspace_id,
        artifact.artifact_id,
        stage="planning",
        status="running",
        message="Production plan accepted",
        current=1,
        total=3,
        details={"provider": "fake"},
    )
    second = store.record_event(
        artifact.workspace_id,
        artifact.artifact_id,
        stage="render",
        status="running",
        message="Rendered measurable unit",
        current=2,
        total=3,
    )

    replay = store.events("workspace", artifact.artifact_id, after=first["sequence"])
    assert [event["sequence"] for event in replay] == [second["sequence"]]
    assert replay[0]["details"]["artifact_progress"] == artifact.progress
    assert replay[0]["details"]["artifact_revision"] == artifact.revision
    assert "secret" not in json.dumps(replay).casefold()


def test_startup_reconciliation_resumes_only_checkpoint_safe_jobs(tmp_path: Path) -> None:
    store = StudioArtifactStore(tmp_path / "workspaces")
    resumable = store.save(
        _artifact(
            "artifact-444444444444",
            "audio_overview",
            status="generating",
            stage="rendering_audio",
            progress=72,
        )
    )
    unsafe = store.save(
        _artifact(
            "artifact-555555555555",
            "video_overview",
            status="generating",
            stage="encoding",
            progress=82,
        )
    )
    store.acquire_lease(
        "workspace", resumable.artifact_id, owner="dead-worker", checkpoint_safe=True
    )
    store.acquire_lease(
        "workspace", unsafe.artifact_id, owner="dead-worker", checkpoint_safe=False
    )

    result = StudioArtifactStore(store.root).reconcile_startup()

    assert result["resume"] == [resumable.artifact_id]
    interrupted = store.get("workspace", unsafe.artifact_id)
    assert interrupted.status == "interrupted"
    assert interrupted.data["retryable"] is True
    assert interrupted.progress == unsafe.progress


def test_studio_list_is_lightweight_paginated_and_sse_replays_cursor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "workspaces"
    workspaces = WorkspaceStore(root)
    workspaces.save(WorkspaceRecord(workspace_id="workspace", name="Workspace"))
    store = StudioArtifactStore(root)
    artifact = store.save(
        _artifact("artifact-666666666666").model_copy(
            update={"data": {"large": "x" * 100_000}, "content_markdown": "y" * 100_000}
        )
    )
    event = store.record_event(
        "workspace",
        artifact.artifact_id,
        stage="queued",
        status="queued",
        message="Job queued",
        current=0,
        total=1,
    )
    monkeypatch.setattr(webapp, "workspace_store", lambda: workspaces)
    monkeypatch.setattr(webapp, "studio_artifact_store", lambda: store)

    with TestClient(webapp.app) as client:
        listed = client.get("/api/workspaces/workspace/studio/artifacts?limit=20")
        detail = client.get(
            f"/api/workspaces/workspace/studio/artifacts/{artifact.artifact_id}"
        )
        replay = client.get(
            f"/api/workspaces/workspace/studio/artifacts/{artifact.artifact_id}/events"
            "?follow=false",
            headers={"Last-Event-ID": "0"},
        )

    payload = listed.json()
    assert set(payload) == {"items", "next_cursor"}
    assert "data" not in payload["items"][0]
    assert len(listed.content) < 4_000
    assert detail.json()["live_events"][-1]["sequence"] == event["sequence"]
    assert replay.headers["content-type"].startswith("text/event-stream")
    assert f"id: {event['sequence']}" in replay.text
    assert "event: studio" in replay.text


def test_studio_detail_returns_the_latest_fifty_events(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "workspaces"
    workspaces = WorkspaceStore(root)
    workspaces.save(WorkspaceRecord(workspace_id="workspace", name="Workspace"))
    store = StudioArtifactStore(root)
    artifact = store.save(_artifact("artifact-656565656565"))
    for index in range(60):
        store.record_event(
            "workspace",
            artifact.artifact_id,
            stage="rendering",
            status="running",
            message=f"Event {index + 1}",
        )
    monkeypatch.setattr(webapp, "workspace_store", lambda: workspaces)
    monkeypatch.setattr(webapp, "studio_artifact_store", lambda: store)

    with TestClient(webapp.app) as client:
        detail = client.get(
            f"/api/workspaces/workspace/studio/artifacts/{artifact.artifact_id}"
        )

    events = detail.json()["live_events"]
    assert len(events) == 50
    assert [event["message"] for event in (events[0], events[-1])] == [
        "Event 11",
        "Event 60",
    ]


def test_tail_bootstrap_skips_stale_terminal_events_from_a_previous_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "workspaces"
    workspaces = WorkspaceStore(root)
    workspaces.save(WorkspaceRecord(workspace_id="workspace", name="Workspace"))
    store = StudioArtifactStore(root)
    artifact = store.save(_artifact("artifact-646464646464"))
    store.record_event(
        "workspace", artifact.artifact_id,
        stage="queue", status="queued", message="Initial attempt queued",
    )
    failed = store.save(
        artifact.model_copy(update={"status": "failed", "stage": "failed", "progress": 82})
    )
    store.record_event(
        "workspace", artifact.artifact_id,
        stage="failed", status="failed", message="Initial attempt failed",
    )
    retried = store.save(
        failed.model_copy(update={"status": "queued", "stage": "retry_queued", "progress": 0})
    )
    latest = store.record_event(
        "workspace", artifact.artifact_id,
        stage="queue", status="queued", message="Retry queued",
    )
    assert retried.progress == 0
    monkeypatch.setattr(webapp, "workspace_store", lambda: workspaces)
    monkeypatch.setattr(webapp, "studio_artifact_store", lambda: store)

    with TestClient(webapp.app) as client:
        replay = client.get(
            f"/api/workspaces/workspace/studio/artifacts/{artifact.artifact_id}/events"
            "?tail=true&follow=false"
        )
        resumed = client.get(
            f"/api/workspaces/workspace/studio/artifacts/{artifact.artifact_id}/events"
            "?tail=true&follow=false",
            headers={"Last-Event-ID": "0"},
        )

    assert f"id: {latest['sequence']}" in replay.text
    assert "Retry queued" in replay.text
    assert "Initial attempt failed" not in replay.text
    assert "Initial attempt failed" in resumed.text


@pytest.mark.parametrize("status", ["script_ready", "plan_ready"])
def test_review_ready_audio_states_are_terminal_for_studio_streams(status: str) -> None:
    assert webapp._studio_stream_is_terminal(status)
    assert not webapp._studio_stream_is_terminal("generating")


def test_terminal_sse_waits_for_the_final_event_after_artifact_save(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "workspaces"
    workspaces = WorkspaceStore(root)
    workspaces.save(WorkspaceRecord(workspace_id="workspace", name="Workspace"))

    class RacingStore(StudioArtifactStore):
        event_reads = 0
        final_event: threading.Timer | None = None

        def events(self, workspace_id, artifact_id, *, after=0, limit=500):
            self.event_reads += 1
            if self.event_reads == 2:
                current = self.get(workspace_id, artifact_id)
                self.save(
                    current.model_copy(
                        update={"status": "ready", "stage": "ready", "progress": 100}
                    )
                )
                self.final_event = threading.Timer(
                    0.05,
                    lambda: self.record_event(
                        workspace_id,
                        artifact_id,
                        stage="completion",
                        status="completed",
                        message="Artifact ready",
                        current=1,
                        total=1,
                    ),
                )
                self.final_event.start()
                return []
            return super().events(
                workspace_id, artifact_id, after=after, limit=limit
            )

    store = RacingStore(root)
    artifact = store.save(
        _artifact(
            "artifact-676767676767",
            "video_overview",
            status="generating",
            stage="rendering",
            progress=90,
        )
    )
    store.record_event(
        "workspace",
        artifact.artifact_id,
        stage="rendering",
        status="running",
        message="Rendering video",
        current=90,
        total=100,
    )
    monkeypatch.setattr(webapp, "workspace_store", lambda: workspaces)
    monkeypatch.setattr(webapp, "studio_artifact_store", lambda: store)

    with TestClient(webapp.app) as client:
        replay = client.get(
            f"/api/workspaces/workspace/studio/artifacts/{artifact.artifact_id}/events"
        )

    if store.final_event is not None:
        store.final_event.join(timeout=1)
    assert "Artifact ready" in replay.text


def test_heartbeat_and_attempts_survive_a_new_store_instance(tmp_path: Path) -> None:
    store = StudioArtifactStore(tmp_path / "workspaces")
    artifact = store.save(_artifact("artifact-777777777777"))
    store.acquire_lease("workspace", artifact.artifact_id, owner="worker-1")
    attempt = store.begin_attempt(
        "workspace", artifact.artifact_id, stage="provider", provider="fake", model="fake-v1"
    )
    store.heartbeat("workspace", artifact.artifact_id, owner="worker-1")
    store.finish_attempt(attempt, status="completed")

    job = StudioArtifactStore(store.root).job("workspace", artifact.artifact_id)

    assert job["lease_owner"] == "worker-1"
    assert job["heartbeat_at"]
    assert job["attempt_count"] == 1


def test_event_performance_metrics_survive_replay(tmp_path: Path) -> None:
    store = StudioArtifactStore(tmp_path / "workspaces")
    artifact = store.save(_artifact("artifact-888888888888", "video_overview"))

    store.record_event(
        "workspace",
        artifact.artifact_id,
        stage="media_download",
        status="completed",
        message="Licensed media prepared",
        current=4,
        total=4,
        duration_ms=812.5,
        cache_hit=True,
        byte_count=2_048_000,
        attempt=2,
    )

    [event] = StudioArtifactStore(store.root).events("workspace", artifact.artifact_id)
    assert event["duration_ms"] == pytest.approx(812.5)
    assert event["cache_hit"] is True
    assert event["byte_count"] == 2_048_000
    assert event["attempt"] == 2
