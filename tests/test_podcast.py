import asyncio
import io
import json
import subprocess
import wave
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

import paperlens.webapp as webapp
from paperlens.config import Settings
from paperlens.domain import Evidence
from paperlens.language_models.base import GenerationResult
from paperlens.podcast import (
    GeminiLivePodcastRenderer,
    GroqOrpheusPodcastRenderer,
    PiperPodcastRenderer,
    PodcastConfig,
    PodcastParticipant,
    PodcastPlanSegment,
    PodcastRenderedTurn,
    PodcastRenderRequest,
    PodcastScriptTurn,
    PodcastService,
    ResilientPodcastRenderer,
)
from paperlens.studio import (
    StudioArtifactStore,
    StudioGenerationRequest,
    StudioService,
    normalize_studio_options,
    studio_creation_catalog,
)
from paperlens.workspaces import WorkspaceStore


def test_audio_overview_catalog_exposes_safe_production_defaults() -> None:
    item = next(
        entry for entry in studio_creation_catalog() if entry["artifact_type"] == "audio_overview"
    )

    assert item["defaults"]["production_mode"] == "scripted"
    assert item["fields"]["model"]["choices"] == [
        "gemini-3.1-flash-live-preview",
        "gemini-2.5-flash-native-audio-preview-12-2025",
    ]
    assert item["fields"]["duration_minutes"]["minimum"] == 2
    assert item["fields"]["duration_minutes"]["maximum"] == 20
    assert item["fields"]["participants"]["minimum"] == 1
    assert item["fields"]["participants"]["maximum"] == 5
    assert item["fields"]["participants"]["thinking_levels"] == [
        "auto",
        "minimal",
        "low",
        "medium",
        "high",
    ]


def test_audio_overview_options_validate_participants_and_narration_mode() -> None:
    options = normalize_studio_options(
        "audio_overview",
        {
            "production_mode": "narrated",
            "duration_minutes": 12,
            "participants": [
                {
                    "participant_id": "host",
                    "name": "Niloofar",
                    "voice": "Kore",
                    "role": "Host",
                }
            ],
        },
    )

    assert options["duration_minutes"] == 12
    assert options["participants"][0]["voice"] == "Kore"

    with pytest.raises(ValueError, match="exactly one participant"):
        normalize_studio_options(
            "audio_overview",
            {
                "production_mode": "narrated",
                "participants": [
                    {"participant_id": "one", "name": "One", "voice": "Kore"},
                    {"participant_id": "two", "name": "Two", "voice": "Puck"},
                ],
            },
        )


def test_piper_settings_parse_language_voice_models(tmp_path) -> None:
    settings = Settings(
        _env_file=None,
        piper_command=tmp_path / "piper.exe",
        piper_en_voice_models=(
            f"{tmp_path / 'voices/joe.onnx'}, {tmp_path / 'voices/kathleen.onnx'}"
        ),
        piper_fa_voice_models=f"{tmp_path / 'voices/amir.onnx'};{tmp_path / 'voices/ganji.onnx'}",
    )

    assert settings.piper_voice_models("en") == [
        tmp_path / "voices/joe.onnx",
        tmp_path / "voices/kathleen.onnx",
    ]
    assert settings.piper_voice_models("fa") == [
        tmp_path / "voices/amir.onnx",
        tmp_path / "voices/ganji.onnx",
    ]


def test_podcast_duration_estimate_keeps_a_safety_margin_for_live_speech() -> None:
    config = PodcastConfig(duration_minutes=2, pace="natural")
    short_draft = {
        "script": [
            {
                "text": " ".join(f"word{index}" for index in range(279)),
            }
        ]
    }
    complete_draft = {
        "script": [
            {
                "text": " ".join(f"word{index}" for index in range(320)),
            }
        ]
    }
    long_draft = {
        "script": [
            {
                "text": " ".join(f"word{index}" for index in range(400)),
            }
        ]
    }

    short_metrics = PodcastService._draft_metrics(short_draft, config)
    complete_metrics = PodcastService._draft_metrics(complete_draft, config)
    long_metrics = PodcastService._draft_metrics(long_draft, config)

    assert short_metrics["target_word_count"] == 320
    assert short_metrics["minimum_word_count"] == 304
    assert short_metrics["maximum_word_count"] == 336
    assert short_metrics["too_short"] is True
    assert complete_metrics["outside_target"] is False
    assert long_metrics["too_long"] is True


def test_podcast_draft_accepts_json_preamble_and_literal_control_characters() -> None:
    payload = PodcastService._decode_draft(
        "Draft follows:\n"
        '{"title":"Episode","summary":"line one\nline two","turns":['
        '{"participant_id":"host","text":"Grounded opening.","citations":["S1"]},'
        '{"participant_id":"guest","text":"Grounded answer.","citations":["S1"]}]}'
        "\nEnd of draft.",
        PodcastConfig(),
    )

    assert payload["summary"] == "line one\nline two"
    assert len(payload["script"]) == 2


def test_podcast_draft_normalizes_common_dialogue_field_aliases() -> None:
    payload = PodcastService._decode_draft(
        '{"title":"Episode","dialogue":['
        '{"speaker":"Niloofar","section":"Opening","content":"Welcome.",'
        '"sources":"S1"},'
        '{"participant":"guest","line":"Grounded answer.","sources":["S1"]}]}'
        "\n",
        PodcastConfig(),
    )

    assert [turn["participant_id"] for turn in payload["script"]] == ["host", "guest"]
    assert payload["script"][0]["chapter"] == "Opening"
    assert payload["script"][0]["citations"] == ["S1"]


def test_podcast_draft_uses_quality_aware_model_fallback() -> None:
    good_text = (
        '{"title":"Episode","summary":"Grounded","turns":['
        '{"participant_id":"host","text":"Opening.","citations":["S1"]},'
        '{"participant_id":"guest","text":"Answer.","citations":["S1"]}]}'
    )

    class QualityAwareModel:
        validation_profile = ""
        attempts = 0

        def run(self, **kwargs):
            raise AssertionError("Unvalidated generation must not be used")

        def run_validated(self, validator, **kwargs):
            self.validation_profile = kwargs["validation_profile"]
            for text in ("not json", good_text):
                self.attempts += 1
                result = GenerationResult(text=text)
                if validator(result):
                    return result
            raise AssertionError("No candidate passed validation")

    service = object.__new__(PodcastService)
    service.language_model = QualityAwareModel()

    result = service._run_draft(
        PodcastConfig(),
        instructions="Create a draft",
        user_input="Evidence",
    )

    assert result.text == good_text
    assert service.language_model.attempts == 2
    assert service.language_model.validation_profile == "studio:audio_overview:draft"


def test_podcast_draft_quality_fallback_can_validate_duration_shape() -> None:
    base = (
        '"summary":"Grounded","turns":['
        '{"participant_id":"host","text":"Opening.","citations":["S1"]},'
        '{"participant_id":"guest","text":"Answer.","citations":["S1"]}]}'
    )
    candidates = [
        GenerationResult(text='{"title":"Wrong length",' + base),
        GenerationResult(text='{"title":"Target length",' + base),
    ]

    class QualityAwareModel:
        def run_validated(self, validator, **kwargs):
            return next(result for result in candidates if validator(result))

    service = object.__new__(PodcastService)
    service.language_model = QualityAwareModel()

    result = service._run_draft(
        PodcastConfig(),
        instructions="Fit the duration",
        user_input="Evidence",
        payload_validator=lambda payload: payload["title"] == "Target length",
    )

    assert "Target length" in result.text


def test_podcast_config_rejects_unsupported_models_and_duplicate_participants() -> None:
    with pytest.raises(ValidationError):
        PodcastConfig(
            model="retired-native-audio-model",
            participants=[PodcastParticipant(participant_id="host", name="Host")],
        )


def test_podcast_context_compacts_long_evidence_without_losing_citations() -> None:
    evidence = [
        Evidence(
            chunk_id="chunk-1",
            paper_id="doc-1",
            paper_title="Long source",
            section="Results",
            section_type="results",
            page_start=7,
            page_end=7,
            text="evidence " * 400,
            similarity=0.91,
            workspace_id="workspace-1",
            document_id="doc-1",
            filename="long-source.pdf",
        )
    ]

    citations, context = PodcastService._context(evidence, max_text_chars=1_000)

    assert citations[0].label == "S1"
    assert citations[0].filename == "long-source.pdf"
    assert "[S1] long-source.pdf | pages 7 | Results" in context
    assert context.endswith("…")
    assert len(context) < 1_100


def test_groq_orpheus_renderer_preserves_reviewed_text_and_chunks_requests() -> None:
    wav_buffer = io.BytesIO()
    with wave.open(wav_buffer, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(24_000)
        output.writeframes(b"\x01\x00" * 240)

    class Response:
        content = wav_buffer.getvalue()

        @staticmethod
        def raise_for_status() -> None:
            return None

    class Client:
        def __init__(self) -> None:
            self.requests = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args) -> None:
            return None

        async def post(self, url, **kwargs):
            self.requests.append((url, kwargs))
            return Response()

    client = Client()
    renderer = GroqOrpheusPodcastRenderer(
        ["groq-key"], client_factory=lambda: client
    )
    text = "Natural reviewed narration " * 15
    request = PodcastRenderRequest(
        artifact_id="artifact-1",
        title="Test",
        language="en",
        config=PodcastConfig(
            production_mode="narrated",
            participants=[PodcastParticipant(participant_id="host", name="Host")],
        ),
        script=(PodcastScriptTurn(participant_id="host", text=text),),
        plan=(),
        source_context="",
        prior_turns=(),
        api_keys=(),
    )
    turns = []

    async def collect_turn(turn) -> None:
        turns.append(turn)

    asyncio.run(renderer.render(request, collect_turn, lambda: False))

    assert turns[0].transcript == text.strip()
    assert turns[0].reviewed_text == text.strip()
    assert all(len(item[1]["json"]["input"]) <= 190 for item in client.requests)
    assert turns[0].attempts[-1]["provider"] == "Groq Orpheus TTS"


def test_resilient_renderer_falls_back_before_any_partial_audio() -> None:
    class FailedPrimary:
        async def render(self, request, checkpoint, should_cancel) -> None:
            raise RuntimeError("provider unavailable")

    class WorkingFallback:
        async def render(self, request, checkpoint, should_cancel) -> None:
            await checkpoint("fallback-turn")

    renderer = ResilientPodcastRenderer(FailedPrimary(), WorkingFallback())
    completed = []
    request = SimpleNamespace(language="en", event_callback=None)

    async def collect_turn(turn) -> None:
        completed.append(turn)

    asyncio.run(renderer.render(request, collect_turn, lambda: False))

    assert completed == ["fallback-turn"]


def test_resilient_renderer_reaches_local_fallback_for_persian() -> None:
    class FailedProvider:
        provider_name = "Unavailable provider"

        async def render(self, request, checkpoint, should_cancel) -> None:
            raise RuntimeError("provider unavailable")

    class LocalProvider:
        provider_name = "Piper local TTS"

        async def render(self, request, checkpoint, should_cancel) -> None:
            await checkpoint("local-turn")

    events = []
    request = SimpleNamespace(
        language="fa",
        event_callback=lambda **event: events.append(event),
    )
    renderer = ResilientPodcastRenderer(
        FailedProvider(),
        FailedProvider(),
        LocalProvider(),
    )
    completed = []

    async def collect(turn) -> None:
        completed.append(turn)

    asyncio.run(renderer.render(request, collect, lambda: False))

    assert completed == ["local-turn"]
    assert events[-1]["details"]["next_provider"] == "Piper local TTS"


def test_piper_renderer_resamples_native_wav_to_24khz_mono(tmp_path, monkeypatch) -> None:
    command = tmp_path / "piper.exe"
    model = tmp_path / "fa_IR-amir-medium.onnx"
    command.write_bytes(b"fake executable")
    model.write_bytes(b"fake model")

    def fake_run(args, *, input, **kwargs):
        output = args[args.index("--output_file") + 1]
        with wave.open(output, "wb") as target:
            target.setnchannels(1)
            target.setsampwidth(2)
            target.setframerate(22_050)
            target.writeframes(b"\x00\x10" * 22_050)
        return subprocess.CompletedProcess(args, 0, b"", b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    renderer = PiperPodcastRenderer(
        command,
        en_voice_models=(),
        fa_voice_models=(model,),
    )
    request = PodcastRenderRequest(
        artifact_id="artifact-fa",
        title="آزمون",
        language="fa",
        config=PodcastConfig(
            production_mode="narrated",
            participants=[PodcastParticipant(participant_id="host", name="Host")],
        ),
        script=(PodcastScriptTurn(participant_id="host", text="یک روایت طبیعی."),),
        plan=(),
        source_context="",
        prior_turns=(),
        api_keys=(),
    )
    turns = []

    async def collect(turn) -> None:
        turns.append(turn)

    asyncio.run(renderer.render(request, collect, lambda: False))

    assert len(turns[0].pcm) == 24_000 * 2
    assert turns[0].attempts[-1]["provider"] == "Piper local TTS"
    assert turns[0].attempts[-1]["sample_rate"] == 24_000
    assert turns[0].reviewed_text == "یک روایت طبیعی."

    with pytest.raises(ValidationError, match="participant IDs"):
        PodcastConfig(
            participants=[
                PodcastParticipant(participant_id="host", name="Host"),
                PodcastParticipant(participant_id="host", name="Guest"),
            ]
        )


class PodcastKnowledgeBase:
    def __init__(self, document_id: str) -> None:
        self.document_id = document_id

    def search_balanced(self, workspace_id, query, document_ids, **kwargs):
        assert document_ids == [self.document_id]
        return [
            Evidence(
                chunk_id="chunk-1",
                paper_id=self.document_id,
                paper_title="Selected source",
                section="Findings",
                section_type="results",
                page_start=4,
                page_end=4,
                text="Retrieval quality improves when dense and lexical evidence are fused.",
                similarity=0.93,
                workspace_id=workspace_id,
                document_id=self.document_id,
                filename="research.pdf",
            )
        ]


class PodcastLanguageModel:
    def __init__(self) -> None:
        self.requests: list[str] = []

    def run(self, *, instructions, user_input, **kwargs):
        self.requests.append(f"{instructions}\n{user_input}")
        return GenerationResult(
            text=(
                '{"title":"Retrieval roundtable","summary":"A grounded discussion",'
                '"chapters":[{"title":"Why fusion matters","citations":["S1"]}],'
                '"turns":['
                '{"participant_id":"host","chapter":"Why fusion matters",'
                '"text":"Why combine lexical and semantic retrieval?","citations":["S1"]},'
                '{"participant_id":"guest","chapter":"Why fusion matters",'
                '"text":"Because their evidence signals complement each other.",'
                '"citations":["S1"]}]}'
            )
        )

    def failover_status(self):
        return {"provider": "test", "active_model": "planner-test"}


class TruncatedPodcastLanguageModel:
    def __init__(self) -> None:
        complete = (
            '{"title":"Agentic retrieval roundtable",'
            '"summary":"A grounded discussion of hybrid retrieval.",'
            '"chapters":[{"title":"Retrieval","citations":["S1"]}],'
            '"segments":[{"title":"Open discussion",'
            '"objective":"Explain how dense and lexical evidence complement each other.",'
            '"participant_ids":["host","guest"],"citations":["S1"]}]}'
        )
        cut_at = complete.index("lexical") + 4
        self.responses = [complete[:cut_at], complete[cut_at:]]
        self.requests: list[str] = []

    def run(self, *, instructions, user_input, **kwargs):
        self.requests.append(f"{instructions}\n{user_input}")
        index = min(len(self.requests) - 1, len(self.responses) - 1)
        return GenerationResult(
            text=self.responses[index],
            finish_reason="length" if index == 0 else "stop",
        )

    def failover_status(self):
        return {"provider": "test", "active_model": "planner-test"}


class ChapterOnlyAgenticModel:
    def __init__(self) -> None:
        self.requests: list[str] = []

    def run(self, *, instructions, user_input, **kwargs):
        self.requests.append(f"{instructions}\n{user_input}")
        return GenerationResult(
            text=(
                '{"title":"Retrieval discussion","summary":"A grounded plan.",'
                '"chapters":[{"title":"Why fusion matters",'
                '"summary":"Discuss complementary dense and lexical signals.",'
                '"citations":["S1"]}]}'
            ),
            finish_reason="stop",
        )

    def failover_status(self):
        return {"provider": "test", "active_model": "planner-test"}


def test_scripted_audio_overview_prepares_an_editable_source_grounded_draft(tmp_path) -> None:
    workspaces = WorkspaceStore(tmp_path / "workspaces")
    workspace = workspaces.create_workspace("Podcast")
    document = workspaces.add_document(
        workspace.workspace_id,
        b"Grounded retrieval source",
        "research.txt",
        "text/plain",
    )
    workspaces.update_document(workspace.workspace_id, document.document_id, status="ready")
    artifacts = StudioArtifactStore(workspaces.root)
    model = PodcastLanguageModel()
    knowledge_base = PodcastKnowledgeBase(document.document_id)
    studio = StudioService(
        workspaces=workspaces,
        knowledge_base=knowledge_base,
        language_model=model,
        artifacts=artifacts,
    )
    queued = studio.create(
        StudioGenerationRequest(
            workspace_id=workspace.workspace_id,
            artifact_type="audio_overview",
            document_ids=[document.document_id],
            language="en",
            options={"duration_minutes": 2},
        )
    )
    podcasts = PodcastService(
        workspaces=workspaces,
        knowledge_base=knowledge_base,
        language_model=model,
        artifacts=artifacts,
    )

    prepared = podcasts.prepare(workspace.workspace_id, queued.artifact_id)

    assert prepared.status == "script_ready"
    assert prepared.stage == "script_review"
    assert prepared.data["script"][0]["participant_id"] == "host"
    assert prepared.data["script"][1]["text"].startswith("Because")
    assert prepared.citations[0].filename == "research.pdf"
    assert prepared.provider == "test"
    assert prepared.model == "planner-test"
    assert prepared.data["events"][-1]["kind"] == "draft_ready"
    assert "research.pdf" in model.requests[0]
    assert len(model.requests) == 2


def test_agentic_draft_continues_truncated_json_instead_of_regenerating_it(tmp_path) -> None:
    workspaces = WorkspaceStore(tmp_path / "workspaces")
    workspace = workspaces.create_workspace("Podcast")
    document = workspaces.add_document(
        workspace.workspace_id,
        b"Grounded retrieval source",
        "research.txt",
        "text/plain",
    )
    workspaces.update_document(workspace.workspace_id, document.document_id, status="ready")
    artifacts = StudioArtifactStore(workspaces.root)
    model = TruncatedPodcastLanguageModel()
    knowledge_base = PodcastKnowledgeBase(document.document_id)
    studio = StudioService(
        workspaces=workspaces,
        knowledge_base=knowledge_base,
        language_model=model,
        artifacts=artifacts,
    )
    queued = studio.create(
        StudioGenerationRequest(
            workspace_id=workspace.workspace_id,
            artifact_type="audio_overview",
            document_ids=[document.document_id],
            language="en",
            options={"duration_minutes": 2, "production_mode": "autonomous"},
        )
    )
    podcasts = PodcastService(
        workspaces=workspaces,
        knowledge_base=knowledge_base,
        language_model=model,
        artifacts=artifacts,
    )

    prepared = podcasts.prepare(workspace.workspace_id, queued.artifact_id)

    assert prepared.status == "plan_ready"
    assert prepared.stage == "plan_ready"
    assert prepared.data["plan"][0]["objective"].endswith("complement each other.")
    assert len(model.requests) == 2
    assert "missing continuation" in model.requests[1].casefold()


def test_agentic_draft_adapts_a_valid_chapter_plan_without_segments(tmp_path) -> None:
    workspaces = WorkspaceStore(tmp_path / "workspaces")
    workspace = workspaces.create_workspace("Podcast")
    document = workspaces.add_document(
        workspace.workspace_id,
        b"Grounded retrieval source",
        "research.txt",
        "text/plain",
    )
    workspaces.update_document(workspace.workspace_id, document.document_id, status="ready")
    artifacts = StudioArtifactStore(workspaces.root)
    model = ChapterOnlyAgenticModel()
    knowledge_base = PodcastKnowledgeBase(document.document_id)
    studio = StudioService(
        workspaces=workspaces,
        knowledge_base=knowledge_base,
        language_model=model,
        artifacts=artifacts,
    )
    queued = studio.create(
        StudioGenerationRequest(
            workspace_id=workspace.workspace_id,
            artifact_type="audio_overview",
            document_ids=[document.document_id],
            language="en",
            options={"duration_minutes": 2, "production_mode": "autonomous"},
        )
    )
    podcasts = PodcastService(
        workspaces=workspaces,
        knowledge_base=knowledge_base,
        language_model=model,
        artifacts=artifacts,
    )

    prepared = podcasts.prepare(workspace.workspace_id, queued.artifact_id)

    assert prepared.status == "plan_ready"
    assert prepared.data["plan"][0]["title"] == "Why fusion matters"
    assert prepared.data["plan"][0]["participant_ids"] == ["host", "guest"]
    assert prepared.data["plan"][0]["citations"] == ["S1"]
    assert len(model.requests) == 1
    with pytest.raises(ValueError, match="Only a scripted podcast"):
        podcasts.update_script(
            workspace.workspace_id,
            prepared.artifact_id,
            [{"participant_id": "host", "text": "This must not be editable."}],
        )


def test_user_can_edit_a_script_draft_before_audio_generation(tmp_path) -> None:
    workspaces = WorkspaceStore(tmp_path / "workspaces")
    workspace = workspaces.create_workspace("Podcast")
    document = workspaces.add_document(
        workspace.workspace_id, b"Evidence", "source.txt", "text/plain"
    )
    workspaces.update_document(workspace.workspace_id, document.document_id, status="ready")
    artifacts = StudioArtifactStore(workspaces.root)
    model = PodcastLanguageModel()
    knowledge_base = PodcastKnowledgeBase(document.document_id)
    studio = StudioService(
        workspaces=workspaces,
        knowledge_base=knowledge_base,
        language_model=model,
        artifacts=artifacts,
    )
    queued = studio.create(
        StudioGenerationRequest(
            workspace_id=workspace.workspace_id,
            artifact_type="audio_overview",
            document_ids=[document.document_id],
            options={"duration_minutes": 2},
        )
    )
    podcasts = PodcastService(
        workspaces=workspaces,
        knowledge_base=knowledge_base,
        language_model=model,
        artifacts=artifacts,
    )
    prepared = podcasts.prepare(workspace.workspace_id, queued.artifact_id)

    edited = podcasts.update_script(
        workspace.workspace_id,
        prepared.artifact_id,
        [
            {
                "turn_id": prepared.data["script"][0]["turn_id"],
                "participant_id": "host",
                "chapter": "Opening",
                "text": "This is the reviewed opening.",
                "citations": ["S1"],
            }
        ],
    )

    assert edited.data["script"][0]["text"] == "This is the reviewed opening."
    assert edited.data["events"][-1]["kind"] == "script_updated"

    with pytest.raises(ValueError, match="unknown participant"):
        podcasts.update_script(
            workspace.workspace_id,
            prepared.artifact_id,
            [{"participant_id": "intruder", "text": "Not allowed"}],
        )


class RecordingAudioRenderer:
    def __init__(self) -> None:
        self.requests = []

    async def render(self, request, checkpoint, should_cancel) -> None:
        self.requests.append(request)
        assert not should_cancel()
        for index, item in enumerate(request.script):
            await checkpoint(
                PodcastRenderedTurn(
                    turn_id=item.turn_id,
                    participant_id=item.participant_id,
                    transcript=item.text,
                    pcm=b"\x01\x00" * 24_000,
                    provider_slot=index + 1,
                    latency_ms=120,
                    attempts=(
                        {
                            "attempt": 1,
                            "status": "completed",
                            "provider_slot": index + 1,
                            "wait_ms": 0,
                            "latency_ms": 120,
                        },
                    ),
                )
            )


def prepared_podcast(tmp_path, *, participants=None):
    workspaces = WorkspaceStore(tmp_path / "workspaces")
    workspace = workspaces.create_workspace("Podcast")
    document = workspaces.add_document(
        workspace.workspace_id, b"Evidence", "source.txt", "text/plain"
    )
    workspaces.update_document(workspace.workspace_id, document.document_id, status="ready")
    artifacts = StudioArtifactStore(workspaces.root)
    model = PodcastLanguageModel()
    knowledge_base = PodcastKnowledgeBase(document.document_id)
    studio = StudioService(
        workspaces=workspaces,
        knowledge_base=knowledge_base,
        language_model=model,
        artifacts=artifacts,
    )
    options = {"duration_minutes": 2}
    if participants is not None:
        options["participants"] = participants
    queued = studio.create(
        StudioGenerationRequest(
            workspace_id=workspace.workspace_id,
            artifact_type="audio_overview",
            document_ids=[document.document_id],
            options=options,
        )
    )
    return (
        workspaces,
        workspace,
        artifacts,
        knowledge_base,
        model,
        queued,
    )


def test_audio_generation_requires_one_distinct_credential_per_participant(tmp_path) -> None:
    workspaces, workspace, artifacts, knowledge_base, model, queued = prepared_podcast(tmp_path)
    renderer = RecordingAudioRenderer()
    podcasts = PodcastService(
        workspaces=workspaces,
        knowledge_base=knowledge_base,
        language_model=model,
        artifacts=artifacts,
        audio_renderer=renderer,
    )
    prepared = podcasts.prepare(workspace.workspace_id, queued.artifact_id)

    with pytest.raises(ValueError, match="2 distinct Gemini API credentials"):
        podcasts.generate_audio(workspace.workspace_id, prepared.artifact_id, ["only-one-key"])

    unchanged = artifacts.get(workspace.workspace_id, prepared.artifact_id)
    assert unchanged.status == "script_ready"
    assert renderer.requests == []


def test_audio_queue_is_atomic_and_rejects_duplicate_generation(tmp_path) -> None:
    workspaces, workspace, artifacts, knowledge_base, model, queued = prepared_podcast(tmp_path)
    podcasts = PodcastService(
        workspaces=workspaces,
        knowledge_base=knowledge_base,
        language_model=model,
        artifacts=artifacts,
        audio_renderer=RecordingAudioRenderer(),
    )
    prepared = podcasts.prepare(workspace.workspace_id, queued.artifact_id)

    accepted = podcasts.queue_audio(workspace.workspace_id, prepared.artifact_id, 2)

    assert accepted.status == "queued"
    assert accepted.stage == "audio_queued"
    with pytest.raises(ValueError, match="not ready"):
        podcasts.queue_audio(workspace.workspace_id, prepared.artifact_id, 2)


def test_local_podcast_renderer_queues_and_generates_without_gemini_keys(tmp_path) -> None:
    class LocalRecordingRenderer(RecordingAudioRenderer):
        minimum_api_keys = 0

        async def render(self, request, checkpoint, should_cancel) -> None:
            self.requests.append(request)
            for item in request.script:
                await checkpoint(
                    PodcastRenderedTurn(
                        turn_id=item.turn_id,
                        participant_id=item.participant_id,
                        transcript=item.text,
                        reviewed_text=item.text,
                        pcm=b"\x01\x00" * 24_000,
                        provider_slot=1,
                        latency_ms=10,
                        attempts=(
                            {
                                "status": "completed",
                                "provider": "Piper local TTS",
                                "model": "local-voice.onnx",
                            },
                        ),
                    )
                )

    workspaces, workspace, artifacts, knowledge_base, model, queued = prepared_podcast(tmp_path)
    renderer = LocalRecordingRenderer()
    podcasts = PodcastService(
        workspaces=workspaces,
        knowledge_base=knowledge_base,
        language_model=model,
        artifacts=artifacts,
        audio_renderer=renderer,
    )
    prepared = podcasts.prepare(workspace.workspace_id, queued.artifact_id)

    queued_audio = podcasts.queue_audio(workspace.workspace_id, prepared.artifact_id, 0)
    completed = podcasts.generate_audio(workspace.workspace_id, queued_audio.artifact_id, [])

    assert completed.status == "ready"
    assert completed.provider == "Piper local TTS"
    assert completed.model == "local-voice.onnx"


def test_live_scheduler_applies_the_project_safe_gap_across_credential_slots() -> None:
    waits: list[float] = []

    async def record_wait(seconds: float) -> None:
        waits.append(seconds)

    renderer = GeminiLivePodcastRenderer(
        minimum_key_gap_seconds=8,
        sleeper=record_wait,
    )

    async def exercise() -> None:
        await renderer.rate_gate.wait(1)
        await renderer.rate_gate.wait(2)

    asyncio.run(exercise())

    assert waits and waits[0] == pytest.approx(8, abs=0.1)


def test_audio_generation_checkpoints_turns_and_produces_a_playable_wav(tmp_path) -> None:
    workspaces, workspace, artifacts, knowledge_base, model, queued = prepared_podcast(tmp_path)
    renderer = RecordingAudioRenderer()
    podcasts = PodcastService(
        workspaces=workspaces,
        knowledge_base=knowledge_base,
        language_model=model,
        artifacts=artifacts,
        audio_renderer=renderer,
    )
    prepared = podcasts.prepare(workspace.workspace_id, queued.artifact_id)

    completed = podcasts.generate_audio(
        workspace.workspace_id,
        prepared.artifact_id,
        ["first-project-key", "second-project-key"],
    )

    assert completed.status == "ready"
    assert completed.progress == 100
    assert completed.data["duration_seconds"] == pytest.approx(2.26, abs=0.01)
    assert len(completed.data["generated_turns"]) == 2
    assert completed.data["events"][-1]["kind"] == "audio_ready"
    assert "first-project-key" not in completed.model_dump_json()
    wav_path = artifacts.asset_path(
        workspace.workspace_id, prepared.artifact_id, completed.data["wav_filename"]
    )
    with wave.open(str(wav_path), "rb") as audio:
        assert audio.getframerate() == 24_000
        assert audio.getnchannels() == 1
        assert audio.getsampwidth() == 2

    transcript = artifacts.asset_path(
        workspace.workspace_id, prepared.artifact_id, completed.data["transcript_filename"]
    ).read_text(encoding="utf-8")
    assert "Niloofar:" in transcript
    assert "Arash:" in transcript


class FakeLiveSession:
    def __init__(self, transcript: str, *, fail_receive: bool = False) -> None:
        self.transcript = transcript
        self.fail_receive = fail_receive
        self.sent = []

    async def send_client_content(self, **payload):
        self.sent.append(("client", payload))

    async def send_realtime_input(self, **payload):
        self.sent.append(("realtime", payload))

    async def receive(self):
        if self.fail_receive:
            raise TimeoutError("temporary live timeout")
        yield SimpleNamespace(
            data=b"\x02\x00" * 24_000,
            session_resumption_update=SimpleNamespace(
                resumable=True, new_handle="temporary-handle"
            ),
            go_away=None,
            server_content=SimpleNamespace(
                model_turn=None,
                output_transcription=SimpleNamespace(text=self.transcript),
                interrupted=False,
                generation_complete=True,
                turn_complete=True,
            ),
        )


class GenerationCompleteLiveSession:
    """Keep the websocket open after Gemini marks generation as complete."""

    async def receive(self):
        yield SimpleNamespace(
            data=b"\x02\x00" * 24_000,
            session_resumption_update=None,
            go_away=None,
            server_content=SimpleNamespace(
                model_turn=None,
                output_transcription=SimpleNamespace(text="Diagnostic audio."),
                interrupted=False,
                generation_complete=True,
                turn_complete=False,
            ),
        )
        await asyncio.Event().wait()


class FakeConnection:
    def __init__(self, session) -> None:
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *args):
        return None


class FakeLiveClient:
    def __init__(self, sessions) -> None:
        self.sessions = sessions
        self.configs = []
        self.aio = SimpleNamespace(live=SimpleNamespace(connect=self.connect))

    def connect(self, *, model, config):
        self.configs.append((model, config))
        return FakeConnection(self.sessions.pop(0))


def live_request(*, participants=None, api_keys=("first-key", "second-key")):
    config = PodcastConfig(
        duration_minutes=2,
        participants=participants
        or [
            PodcastParticipant(participant_id="host", name="Niloofar", voice="Kore"),
            PodcastParticipant(participant_id="guest", name="Arash", voice="Puck"),
        ],
    )
    script = (
        PodcastScriptTurn(participant_id="host", text="Welcome to the episode."),
        PodcastScriptTurn(participant_id="guest", text="Let us examine the evidence."),
    )
    return PodcastRenderRequest(
        artifact_id="artifact-123456789abc",
        title="Episode",
        language="en",
        config=config,
        script=script[: len(config.participants)],
        plan=(),
        source_context="[S1] Grounded evidence",
        prior_turns=(),
        api_keys=api_keys,
    )


def test_live_renderer_uses_voice_compression_resumption_and_distinct_sessions() -> None:
    clients = [
        FakeLiveClient([FakeLiveSession("Welcome to the episode.")]),
        FakeLiveClient([FakeLiveSession("Let us examine the evidence.")]),
    ]
    rendered = []
    renderer = GeminiLivePodcastRenderer(
        client_factory=lambda key: clients[0] if key == "first-key" else clients[1],
        minimum_key_gap_seconds=0,
    )

    async def checkpoint(turn):
        rendered.append(turn)

    import asyncio

    asyncio.run(renderer.render(live_request(), checkpoint, lambda: False))

    assert [item.participant_id for item in rendered] == ["host", "guest"]
    assert [item.reviewed_text for item in rendered] == [
        "Welcome to the episode.",
        "Let us examine the evidence.",
    ]
    for client, voice in zip(clients, ("Kore", "Puck"), strict=True):
        model, config = client.configs[0]
        assert model == "gemini-3.1-flash-live-preview"
        assert config.context_window_compression.sliding_window.target_tokens == 8_000
        assert config.history_config.initial_history_in_client_content is True
        assert config.session_resumption is not None
        assert config.output_audio_transcription is not None
        assert config.speech_config.voice_config.prebuilt_voice_config.voice_name == voice


def test_live_renderer_sends_the_initial_gemini_31_prompt_as_realtime_input() -> None:
    participant = [PodcastParticipant(participant_id="host", name="Host", voice="Kore")]
    session = FakeLiveSession("Diagnostic audio.")
    client = FakeLiveClient([session])
    renderer = GeminiLivePodcastRenderer(
        client_factory=lambda key: client,
        minimum_key_gap_seconds=0,
    )

    asyncio.run(
        renderer.render(
            live_request(participants=participant, api_keys=("first-key",)),
            lambda turn: asyncio.sleep(0),
            lambda: False,
        )
    )

    assert session.sent[0][0] == "realtime"


def test_live_renderer_reconnects_after_a_transient_session_failure() -> None:
    participant = [PodcastParticipant(participant_id="host", name="Host", voice="Kore")]
    client = FakeLiveClient(
        [
            FakeLiveSession("", fail_receive=True),
            FakeLiveSession("Welcome to the episode."),
        ]
    )
    renderer = GeminiLivePodcastRenderer(
        client_factory=lambda key: client,
        minimum_key_gap_seconds=0,
        retry_delays=(0,),
    )
    rendered = []

    async def checkpoint(turn):
        rendered.append(turn)

    import asyncio

    asyncio.run(
        renderer.render(
            live_request(participants=participant, api_keys=("first-key",)),
            checkpoint,
            lambda: False,
        )
    )

    assert len(client.configs) == 2
    assert all(config.session_resumption.handle is None for _, config in client.configs)
    assert rendered[0].transcript == "Welcome to the episode."
    assert [attempt["status"] for attempt in rendered[0].attempts] == ["failed", "completed"]


def test_live_renderer_does_not_rotate_keys_for_a_permanent_location_block() -> None:
    class LocationBlockedSession:
        async def receive(self):
            raise RuntimeError("User location is not supported for the API use.")
            yield

    participant = [PodcastParticipant(participant_id="host", name="Host", voice="Kore")]
    primary = FakeLiveClient([LocationBlockedSession()])
    reserve = FakeLiveClient([FakeLiveSession("This must not run.")])
    renderer = GeminiLivePodcastRenderer(
        client_factory=lambda key: primary if key == "primary-key" else reserve,
        minimum_key_gap_seconds=0,
        retry_delays=(0,),
    )

    with pytest.raises(RuntimeError, match="failed after 1 attempt"):
        asyncio.run(
            renderer.render(
                live_request(
                    participants=participant,
                    api_keys=("primary-key", "reserve-key"),
                ),
                lambda turn: asyncio.sleep(0),
                lambda: False,
            )
        )

    assert len(primary.configs) == 1
    assert reserve.configs == []


def test_scripted_live_renderer_refreshes_same_credential_between_successful_turns() -> None:
    participant = [
        PodcastParticipant(
            participant_id="host",
            name="Host",
            voice="Kore",
            thinking_level="high",
            temperature=0.45,
        )
    ]
    client = FakeLiveClient(
        [
            FakeLiveSession("First bounded turn."),
            FakeLiveSession("Second bounded turn."),
        ]
    )
    renderer = GeminiLivePodcastRenderer(
        client_factory=lambda key: client,
        minimum_key_gap_seconds=0,
    )
    request = live_request(participants=participant, api_keys=("only-key",))
    request = PodcastRenderRequest(
        **{
            **request.__dict__,
            "script": (
                PodcastScriptTurn(participant_id="host", text="First bounded turn."),
                PodcastScriptTurn(participant_id="host", text="Second bounded turn."),
            ),
        }
    )
    rendered = []

    async def checkpoint(turn):
        rendered.append(turn)

    asyncio.run(renderer.render(request, checkpoint, lambda: False))

    assert [turn.transcript for turn in rendered] == [
        "First bounded turn.",
        "Second bounded turn.",
    ]
    assert [turn.provider_slot for turn in rendered] == [1, 1]
    assert len(client.configs) == 2
    assert all(config.session_resumption.handle is None for _, config in client.configs)
    assert all(
        str(config.thinking_config.thinking_level).casefold().endswith("high")
        for _, config in client.configs
    )
    assert all(config.temperature == pytest.approx(0.45) for _, config in client.configs)


def test_scripted_live_renderer_discards_a_repeated_take() -> None:
    participant = [PodcastParticipant(participant_id="host", name="Host", voice="Kore")]
    client = FakeLiveClient(
        [
            FakeLiveSession("One clear sentence. One clear sentence."),
            FakeLiveSession("One clear sentence."),
        ]
    )
    renderer = GeminiLivePodcastRenderer(
        client_factory=lambda key: client,
        minimum_key_gap_seconds=0,
    )
    request = live_request(participants=participant, api_keys=("only-key",))
    request = PodcastRenderRequest(
        **{
            **request.__dict__,
            "script": (
                PodcastScriptTurn(participant_id="host", text="One clear sentence."),
            ),
        }
    )
    rendered = []

    async def checkpoint(turn):
        rendered.append(turn)

    asyncio.run(renderer.render(request, checkpoint, lambda: False))

    assert [turn.transcript for turn in rendered] == ["One clear sentence."]
    assert len(client.configs) == 2
    assert "repeated first take" in " ".join(rendered[0].warnings)


def test_scripted_live_renderer_discards_prior_turn_replay() -> None:
    participant = [PodcastParticipant(participant_id="host", name="Host", voice="Kore")]
    client = FakeLiveClient(
        [
            FakeLiveSession("First bounded turn."),
            FakeLiveSession("First bounded turn. Second bounded turn."),
            FakeLiveSession("Second bounded turn."),
        ]
    )
    renderer = GeminiLivePodcastRenderer(
        client_factory=lambda key: client,
        minimum_key_gap_seconds=0,
    )
    request = live_request(participants=participant, api_keys=("only-key",))
    request = PodcastRenderRequest(
        **{
            **request.__dict__,
            "script": (
                PodcastScriptTurn(participant_id="host", text="First bounded turn."),
                PodcastScriptTurn(participant_id="host", text="Second bounded turn."),
            ),
        }
    )
    rendered = []

    async def checkpoint(turn):
        rendered.append(turn)

    asyncio.run(renderer.render(request, checkpoint, lambda: False))

    assert [turn.transcript for turn in rendered] == [
        "First bounded turn.",
        "Second bounded turn.",
    ]
    assert len(client.configs) == 3
    assert "repeated first take" in " ".join(rendered[1].warnings)


def test_live_receiver_finishes_when_generation_completes_before_the_turn() -> None:
    renderer = GeminiLivePodcastRenderer(turn_timeout_seconds=0.05)
    runtime = SimpleNamespace(
        session=GenerationCompleteLiveSession(),
        provider_slot=1,
        resume_handles={},
    )

    pcm, transcript = asyncio.run(renderer._receive(runtime))

    assert pcm == b"\x02\x00" * 24_000
    assert transcript == "Diagnostic audio."


def test_live_renderer_moves_to_a_reserved_credential_after_primary_failure() -> None:
    participant = [PodcastParticipant(participant_id="host", name="Host", voice="Kore")]
    primary = FakeLiveClient(
        [
            FakeLiveSession("", fail_receive=True),
            FakeLiveSession("", fail_receive=True),
        ]
    )
    reserve = FakeLiveClient([FakeLiveSession("Recovered with the reserve route.")])
    renderer = GeminiLivePodcastRenderer(
        client_factory=lambda key: primary if key == "primary-key" else reserve,
        minimum_key_gap_seconds=0,
        retry_delays=(0,),
    )
    rendered = []

    async def checkpoint(turn):
        rendered.append(turn)

    asyncio.run(
        renderer.render(
            live_request(
                participants=participant,
                api_keys=("primary-key", "reserve-key"),
            ),
            checkpoint,
            lambda: False,
        )
    )

    assert rendered[0].provider_slot == 2
    assert [attempt["provider_slot"] for attempt in rendered[0].attempts] == [1, 1, 2]
    assert [attempt["status"] for attempt in rendered[0].attempts] == [
        "failed",
        "failed",
        "completed",
    ]


def test_autonomous_renderer_balances_speakers_without_a_fixed_dialogue() -> None:
    clients = [
        FakeLiveClient([FakeLiveSession("A source-grounded perspective.")]),
        FakeLiveClient([FakeLiveSession("A complementary perspective.")]),
    ]
    config = PodcastConfig(production_mode="autonomous", duration_minutes=2)
    request = PodcastRenderRequest(
        artifact_id="artifact-123456789abc",
        title="Roundtable",
        language="en",
        config=config,
        script=(),
        plan=(
            PodcastPlanSegment(
                title="Open discussion",
                objective="Explore the source evidence and choose useful directions.",
                participant_ids=["host", "guest"],
                citations=["S1"],
            ),
        ),
        source_context="[S1] Grounded evidence",
        prior_turns=(),
        api_keys=("first-key", "second-key"),
    )
    renderer = GeminiLivePodcastRenderer(
        client_factory=lambda key: clients[0] if key == "first-key" else clients[1],
        minimum_key_gap_seconds=0,
    )
    rendered = []

    async def checkpoint(turn):
        rendered.append(turn)

    import asyncio

    asyncio.run(renderer.render(request, checkpoint, lambda: False))

    assert len(rendered) == 6
    assert {item.participant_id for item in rendered} == {"host", "guest"}
    assert (
        abs(
            sum(item.participant_id == "host" for item in rendered)
            - sum(item.participant_id == "guest" for item in rendered)
        )
        <= 1
    )


class ImmediateExecutor:
    def submit(self, function, *args, **kwargs):
        function(*args, **kwargs)


def test_agentic_audio_overview_skips_review_and_renders_automatically(
    tmp_path, monkeypatch
) -> None:
    workspaces = WorkspaceStore(tmp_path / "workspaces")
    workspace = workspaces.create_workspace("Podcast")
    document = workspaces.add_document(
        workspace.workspace_id,
        b"Grounded retrieval source",
        "research.txt",
        "text/plain",
    )
    workspaces.update_document(workspace.workspace_id, document.document_id, status="ready")
    artifacts = StudioArtifactStore(workspaces.root)
    model = ChapterOnlyAgenticModel()
    knowledge_base = PodcastKnowledgeBase(document.document_id)
    renderer = RecordingAudioRenderer()
    studio = StudioService(
        workspaces=workspaces,
        knowledge_base=knowledge_base,
        language_model=model,
        artifacts=artifacts,
    )
    podcasts = PodcastService(
        workspaces=workspaces,
        knowledge_base=knowledge_base,
        language_model=model,
        artifacts=artifacts,
        audio_renderer=renderer,
    )
    monkeypatch.setattr(webapp, "workspace_store", lambda: workspaces)
    monkeypatch.setattr(webapp, "studio_artifact_store", lambda: artifacts)
    monkeypatch.setattr(webapp, "studio_service", lambda: studio)
    monkeypatch.setattr(webapp, "podcast_service", lambda: podcasts)
    monkeypatch.setattr(webapp, "studio_executor", lambda: ImmediateExecutor())
    monkeypatch.setattr(webapp, "podcast_executor", lambda: ImmediateExecutor())
    monkeypatch.setattr(
        webapp,
        "settings",
        lambda: Settings(gemini_api_keys="first-project-key,second-project-key"),
    )

    with TestClient(webapp.app, raise_server_exceptions=False) as client:
        created = client.post(
            f"/api/workspaces/{workspace.workspace_id}/studio/artifacts",
            json={
                "artifact_type": "audio_overview",
                "document_ids": [document.document_id],
                "language": "en",
                "options": {
                    "duration_minutes": 2,
                    "production_mode": "autonomous",
                },
            },
        )

        assert created.status_code == 202
        completed = client.get(
            f"/api/workspaces/{workspace.workspace_id}/studio/artifacts/"
            f"{created.json()['artifact_id']}"
        )
        assert completed.json()["status"] == "ready"
        assert renderer.requests


def test_audio_overview_rest_lifecycle_keeps_source_context_private_and_serves_audio(
    tmp_path, monkeypatch
) -> None:
    workspaces, workspace, artifacts, knowledge_base, model, _ = prepared_podcast(tmp_path)
    renderer = RecordingAudioRenderer()
    studio = StudioService(
        workspaces=workspaces,
        knowledge_base=knowledge_base,
        language_model=model,
        artifacts=artifacts,
    )
    podcasts = PodcastService(
        workspaces=workspaces,
        knowledge_base=knowledge_base,
        language_model=model,
        artifacts=artifacts,
        audio_renderer=renderer,
    )
    monkeypatch.setattr(webapp, "workspace_store", lambda: workspaces)
    monkeypatch.setattr(webapp, "studio_artifact_store", lambda: artifacts)
    monkeypatch.setattr(webapp, "studio_service", lambda: studio)
    monkeypatch.setattr(webapp, "podcast_service", lambda: podcasts)
    monkeypatch.setattr(webapp, "studio_executor", lambda: ImmediateExecutor())
    monkeypatch.setattr(webapp, "podcast_executor", lambda: ImmediateExecutor())
    monkeypatch.setattr(
        webapp,
        "settings",
        lambda: Settings(gemini_api_keys="first-project-key,second-project-key"),
    )

    with TestClient(webapp.app, raise_server_exceptions=False) as client:
        created = client.post(
            f"/api/workspaces/{workspace.workspace_id}/studio/artifacts",
            json={
                "artifact_type": "audio_overview",
                "document_ids": [knowledge_base.document_id],
                "language": "en",
                "options": {"duration_minutes": 2},
            },
        )
        assert created.status_code == 202
        artifact_id = created.json()["artifact_id"]

        prepared = client.get(
            f"/api/workspaces/{workspace.workspace_id}/studio/artifacts/{artifact_id}"
        )
        assert prepared.status_code == 200
        assert prepared.json()["status"] == "script_ready"
        assert "_source_context" not in prepared.json()["data"]

        script = prepared.json()["data"]["script"]
        script[0]["text"] = "A user-reviewed opening."
        edited = client.put(
            f"/api/workspaces/{workspace.workspace_id}/studio/artifacts/{artifact_id}/script",
            json={"turns": script},
        )
        assert edited.status_code == 200
        assert edited.json()["data"]["script"][0]["text"] == "A user-reviewed opening."

        generation = client.post(
            f"/api/workspaces/{workspace.workspace_id}/studio/artifacts/{artifact_id}/generate-audio"
        )
        assert generation.status_code == 202
        trace = client.get(f"/api/traces/{generation.json()['trace_id']}")
        assert trace.status_code == 200
        render_stage = trace.json()["stages"][-1]
        assert render_stage["requests"][0]["status"] == "completed"
        assert render_stage["requests"][0]["slot"] == 1

        completed = client.get(
            f"/api/workspaces/{workspace.workspace_id}/studio/artifacts/{artifact_id}"
        )
        assert completed.json()["status"] == "ready"
        audio = client.get(
            f"/api/workspaces/{workspace.workspace_id}/studio/artifacts/{artifact_id}/audio"
        )
        assert audio.status_code == 200
        assert audio.headers["content-type"] in {"audio/mpeg", "audio/wav", "audio/x-wav"}
        assert audio.content


def test_scripted_podcast_repairs_required_intro_and_outro_roles() -> None:
    config = PodcastConfig(include_intro=True, include_outro=True)
    decoded = PodcastService._decode_draft(
        json.dumps(
            {
                "title": "Lifecycle",
                "turns": [
                    {"participant_id": "host", "text": "Welcome to the evidence."},
                    {"participant_id": "guest", "text": "Here is the core finding."},
                    {"participant_id": "host", "text": "Thanks for listening."},
                ],
            }
        ),
        config,
    )

    assert [turn["role"] for turn in decoded["script"]] == ["intro", "body", "outro"]


def test_agentic_plan_reserves_explicit_intro_and_outro_segments() -> None:
    config = PodcastConfig(
        production_mode="autonomous", include_intro=True, include_outro=True
    )
    decoded = PodcastService._decode_draft(
        json.dumps(
            {
                "segments": [
                    {"title": "Opening", "objective": "Orient the listener."},
                    {"title": "Evidence", "objective": "Discuss the core finding."},
                    {"title": "Close", "objective": "Synthesize the takeaway."},
                ]
            }
        ),
        config,
    )

    assert [segment["role"] for segment in decoded["plan"]] == ["intro", "body", "outro"]
    assert decoded["script"]


def test_agentic_draft_keeps_plan_and_source_grounded_fallback_dialogue() -> None:
    config = PodcastConfig(production_mode="guided")
    decoded = PodcastService._decode_draft(
        json.dumps(
            {
                "segments": [
                    {
                        "title": "Evidence",
                        "objective": "Discuss the measured result.",
                        "citations": ["S1"],
                    }
                ],
                "fallback_turns": [
                    {
                        "participant_id": "host",
                        "text": "The source reports a measured result.",
                        "citations": ["S1"],
                    }
                ],
            }
        ),
        config,
    )

    assert decoded["plan"][0]["objective"] == "Discuss the measured result."
    assert decoded["script"][0]["text"] == "The source reports a measured result."
    assert decoded["script"][0]["citations"] == ["S1"]


def test_provider_retry_wait_is_cooperatively_interruptible() -> None:
    cancelled = False
    renderer = GeminiLivePodcastRenderer(retry_delays=(30,), minimum_key_gap_seconds=0)

    async def exercise() -> bool:
        nonlocal cancelled
        task = asyncio.create_task(renderer.wait_for_retry(30, lambda: cancelled))
        await asyncio.sleep(0.05)
        cancelled = True
        return await asyncio.wait_for(task, timeout=0.5)

    started = __import__("time").monotonic()
    completed = asyncio.run(exercise())

    assert completed is False
    assert __import__("time").monotonic() - started < 0.6
