from __future__ import annotations

from pathlib import Path

import pytest

from paperlens.config import Settings
from paperlens.language_models.failover import FailoverLanguageModel, ModelFallbackChain
from paperlens.ocr import PooledOCR
from paperlens.runtime import (
    create_assistant,
    create_knowledge_base,
    create_language_model,
    create_workspace_store,
    seed_demo_workspace,
)
from paperlens.vlm import PooledVLM


def _settings(**overrides) -> Settings:
    values = {
        "_env_file": None,
        "llm_provider": "openai",
        "llm_model": "gemini-3.1-flash-lite",
        "llm_base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "llm_api_key": "google-primary",
        "gemini_api_keys": "google-secondary",
        "google_api_keys": "",
        "groq_chat_api_keys": "groq-one,groq-two",
        "mistral_api_keys": "mistral-one",
        "nvidia_api_keys": "nvidia-one",
        "embedding_provider": "gemini",
        "embedding_model": "gemini-embedding-2",
        "embedding_dimensions": 768,
        "embedding_api_key": "embedding-one",
        "groq_api_key": "",
    }
    values.update(overrides)
    return Settings(**values)


def test_multi_provider_runtime_builds_the_expected_capability_route() -> None:
    model = create_language_model(_settings())

    assert isinstance(model, ModelFallbackChain)
    status = model.failover_status()
    assert status["active_model"] == "Gemini 3.6 Flash"
    assert [route["label"] for route in status["routes"]] == [
        "Gemini 3.6 Flash",
        "Gemini 3.5 Flash",
        "Gemini 3 Flash · Preview",
        "Gemini 2.5 Flash",
        "Gemma 4 31B · Google",
        "Gemma 4 26B · Google",
        "Gemini 3.5 Flash Lite",
        "Gemini 3.1 Flash Lite",
        "GPT-OSS 120B · Groq",
        "Mistral Large 3 · 2512",
        "Mistral Medium 3.5",
        "Nemotron 3 Ultra 550B · NVIDIA",
        "Mistral Small 4 · 2603",
        "Mistral Small 3.2 · 2506",
        "Qwen 3.6 27B · Groq",
        "GPT-OSS 20B · Groq",
        "Llama 3.1 8B Instant · Groq",
        "Ministral 14B · 2512",
        "Ministral 8B · 2512",
        "Inkling · NVIDIA",
        "Llama 3.3 70B · Groq (retiring)",
    ]


def test_single_google_provider_runtime_keeps_key_and_model_failover() -> None:
    model = create_language_model(
        _settings(
            llm_routing_enabled=False,
            llm_fallback_model="gemini-fallback",
            groq_chat_api_keys="",
            mistral_api_keys="",
            nvidia_api_keys="",
        )
    )

    assert isinstance(model, FailoverLanguageModel)
    status = model.failover_status()
    assert status["credential_count"] == 2
    assert status["final_fallback_label"] == "gemini-fallback"


def test_language_model_runtime_fails_fast_without_model_or_credentials() -> None:
    current = _settings(
        llm_routing_enabled=False,
        llm_model="",
        llm_api_key="",
        gemini_api_keys="",
        google_api_keys="",
        groq_chat_api_keys="",
        mistral_api_keys="",
        nvidia_api_keys="",
        llm_base_url="https://api.openai.com/v1",
    )

    with pytest.raises(ValueError, match="LLM_MODEL"):
        create_language_model(current)


def test_assistant_runtime_uses_configured_retrieval_parameters(tmp_path: Path) -> None:
    current = _settings(
        chroma_path=tmp_path / "chroma",
        papers_path=Path("papers"),
        outputs_path=Path("outputs"),
        retrieval_min_similarity=0.67,
        retrieval_top_k=9,
    )

    assistant = create_assistant(current, Path(__file__).resolve().parents[1])

    assert assistant.minimum_similarity == 0.67
    assert assistant.top_k == 9
    assert assistant.corpus.workspace_id == "default"
    assert assistant.corpus.knowledge_base.embedder.model_name == "gemini-embedding-2"


def test_media_runtime_uses_all_configured_ocr_and_vlm_credentials(
    tmp_path: Path,
) -> None:
    current = _settings(
        workspaces_path=tmp_path / "workspaces",
        chroma_path=tmp_path / "chroma",
        papers_path=Path(__file__).resolve().parents[1] / "papers",
        ocr_space_api_key="ocr-one",
        ocr_space_api_keys="ocr-two",
        local_ocr_enabled=False,
    )

    knowledge_base = create_knowledge_base(current, Path(__file__).resolve().parents[1])

    assert isinstance(knowledge_base.pipeline.ocr, PooledOCR)
    assert knowledge_base.pipeline.ocr.status()["credential_count"] == 2
    assert knowledge_base.pipeline.ocr._pool._resources[0].retries == current.ocr_retries
    assert isinstance(knowledge_base.pipeline.vlm, PooledVLM)
    assert [resource.label for resource in knowledge_base.pipeline.vlm._resources[:2]] == [
        "Gemini 3.6 Flash · Google Gemini API",
        "Gemini 3.6 Flash · Google Gemini API",
    ]
    vlm_status = knowledge_base.pipeline.vlm.status()
    assert vlm_status["credential_count"] == 25
    assert [route["label"] for route in vlm_status["routes"][:6]] == [
        "Gemini 3.6 Flash · Google Gemini API",
        "Gemini 3.5 Flash · Google Gemini API",
        "Gemini 3 Flash · Preview · Google Gemini API",
        "Gemini 2.5 Flash · Google Gemini API",
        "Gemma 4 31B · Google · Google Gemini API",
        "Gemma 4 26B · Google · Google Gemini API",
    ]


def test_fresh_runtime_creates_only_an_empty_default_workspace(tmp_path: Path) -> None:
    current = _settings(
        workspaces_path=tmp_path / "workspaces",
        papers_path=Path(__file__).resolve().parents[1] / "papers",
        seed_demo_workspace=False,
    )

    store = create_workspace_store(current, Path(__file__).resolve().parents[1])

    workspaces = store.list()
    assert [workspace.workspace_id for workspace in workspaces] == ["default"]
    assert workspaces[0].documents == []
    assert workspaces[0].is_demo is False


def test_demo_install_is_explicit_and_preserves_the_empty_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from paperlens.corpus import demo_assets
    from paperlens.corpus.manifest import load_paper_manifest

    root = Path(__file__).resolve().parents[1]
    papers_path = tmp_path / "papers"
    papers_path.mkdir()
    (papers_path / "manifest.json").write_bytes((root / "papers/manifest.json").read_bytes())
    for record in load_paper_manifest(papers_path / "manifest.json"):
        record.pdf_path(papers_path).write_bytes(b"%PDF-1.4\n% generated test fixture\n")
    monkeypatch.setattr(demo_assets, "ensure_demo_papers", lambda _path: [])
    current = _settings(
        workspaces_path=tmp_path / "workspaces",
        papers_path=papers_path,
        seed_demo_workspace=False,
    )
    store = create_workspace_store(current, root)

    seed_demo_workspace(current, root, store)

    assert {workspace.workspace_id for workspace in store.list()} == {"default", "demo"}
    assert store.get("default").documents == []
    demo = store.get("demo")
    assert demo.name == "Charge While You Shop"
    assert len(demo.documents) == 2
