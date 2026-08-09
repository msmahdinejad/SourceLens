from types import SimpleNamespace

import numpy as np
import pytest

from paperlens.config import Settings
from paperlens.corpus.index import FallbackEmbeddingIndex, GeminiEmbedder
from paperlens.language_models.base import GenerationResult, ModelCapabilityError
from paperlens.language_models.failover import FailoverLanguageModel, ModelFallbackChain
from paperlens.provider_failover import ProviderFailoverError, ProviderPool


class ProviderError(RuntimeError):
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        super().__init__(f"provider status {status_code}")


class FakeLanguageModel:
    def __init__(self, result: str | Exception) -> None:
        self.result = result
        self.calls = 0

    def run(self, **_kwargs) -> GenerationResult:
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return GenerationResult(text=self.result)


def test_language_model_rotates_after_rate_limit_and_stays_on_working_slot() -> None:
    limited = FakeLanguageModel(ProviderError(429))
    working = FakeLanguageModel("ok")
    model = FailoverLanguageModel([limited, working], provider="Gemini")

    first = model.run(instructions="x", user_input="y")
    second = model.run(instructions="x", user_input="y")

    assert first.text == second.text == "ok"
    assert limited.calls == 1
    assert working.calls == 2
    assert model.failover_status()["active_slot"] == 2
    assert model.failover_status()["credential_count"] == 2


def test_provider_pool_rotates_after_google_location_precondition() -> None:
    blocked = ProviderError(400)
    blocked.args = ("400 FAILED_PRECONDITION: User location is not supported for API use",)
    pool = ProviderPool(
        ["blocked-project", "supported-project"],
        provider="Google Gemini API",
    )

    result = pool.run(
        lambda resource, _index: (_ for _ in ()).throw(blocked)
        if resource == "blocked-project"
        else resource
    )

    assert result == "supported-project"
    assert pool.status()["active_slot"] == 2


def test_provider_pool_retries_first_slot_after_cooldown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = {"now": 0.0}
    monkeypatch.setattr(
        "paperlens.provider_failover.time.monotonic",
        lambda: clock["now"],
    )
    first_attempts = 0

    def operation(resource: str, _index: int) -> str:
        nonlocal first_attempts
        if resource == "first":
            first_attempts += 1
            if first_attempts == 1:
                raise ProviderError(429)
        return resource

    pool = ProviderPool(
        ["first", "second"],
        provider="test",
        cooldown_seconds=60,
    )

    assert pool.run(operation) == "second"
    clock["now"] = 30.0
    assert pool.run(operation) == "second"
    clock["now"] = 61.0
    assert pool.run(operation) == "first"


def test_media_pool_stays_on_healthy_slot_after_rate_limited_slot_expires() -> None:
    clock = {"now": 0.0}
    first_attempts = 0

    def operation(resource: str, _index: int) -> str:
        nonlocal first_attempts
        if resource == "limited":
            first_attempts += 1
            raise ProviderError(429)
        return resource

    pool = ProviderPool(
        ["limited", "healthy"],
        provider="vision",
        cooldown_seconds=60,
        restore_priority_after_cooldown=False,
        clock=lambda: clock["now"],
    )

    assert pool.run(operation) == "healthy"
    clock["now"] = 120
    assert pool.run(operation) == "healthy"
    assert first_attempts == 1


def test_provider_pool_proactively_paces_requests_without_hitting_provider() -> None:
    clock = {"now": 0.0}
    sleeps: list[float] = []

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock["now"] += seconds

    pool = ProviderPool(
        ["credential"],
        provider="vision",
        minimum_interval_seconds=2.0,
        global_minimum_interval_seconds=2.0,
        clock=lambda: clock["now"],
        sleeper=sleep,
    )

    assert pool.run(lambda resource, _index: resource) == "credential"
    assert pool.run(lambda resource, _index: resource) == "credential"

    assert sleeps == [2.0]
    assert pool.status()["total_rate_wait_ms"] == 2000


def test_provider_pool_honors_retry_after_and_recovers_single_slot() -> None:
    clock = {"now": 0.0}
    sleeps: list[float] = []
    calls = 0

    class Limited(ProviderError):
        def __init__(self) -> None:
            super().__init__(429)
            self.response = SimpleNamespace(
                status_code=429,
                headers={"retry-after": "5"},
            )

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock["now"] += seconds

    def operation(resource: str, _index: int) -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise Limited()
        return resource

    pool = ProviderPool(
        ["credential"],
        provider="vision",
        cooldown_seconds=1,
        retry_rounds=2,
        wait_for_cooldown=True,
        max_wait_seconds=10,
        clock=lambda: clock["now"],
        sleeper=sleep,
    )

    assert pool.run(operation) == "credential"
    assert sleeps == [5.0]
    assert pool.status()["last_failover"] is True


def test_provider_pool_can_cancel_a_cooldown_wait() -> None:
    clock = {"now": 0.0}
    checks = 0

    def cancelled() -> bool:
        nonlocal checks
        checks += 1
        return checks >= 3

    pool = ProviderPool(
        ["credential"],
        provider="media",
        minimum_interval_seconds=10,
        clock=lambda: clock["now"],
        sleeper=lambda seconds: clock.__setitem__("now", clock["now"] + seconds),
    )
    assert pool.run(lambda resource, _index: resource) == "credential"

    with pytest.raises(ProviderFailoverError, match="cancelled"):
        pool.run(lambda resource, _index: resource, should_cancel=cancelled)


def test_provider_pool_rotates_when_model_access_differs_between_api_keys() -> None:
    def operation(resource: str, _index: int) -> str:
        if resource == "new-user-project":
            raise ProviderError(404)
        return "available"

    pool = ProviderPool(
        ["new-user-project", "existing-project"],
        provider="Google Gemini API",
    )

    assert pool.run(operation) == "available"
    assert pool.status()["active_slot"] == 2


def test_language_model_uses_final_model_tier_after_all_keys_fail() -> None:
    fallback = FakeLanguageModel("fallback model answer")
    model = FailoverLanguageModel(
        [FakeLanguageModel(ProviderError(429)), FakeLanguageModel(ProviderError(429))],
        provider="Gemini primary",
        fallback=fallback,
        fallback_label="gemini-3.1-flash-lite-preview",
    )

    result = model.run(instructions="x", user_input="y")

    assert result.text == "fallback model answer"
    assert fallback.calls == 1
    assert model.failover_status()["final_fallback_used"] is True
    assert model.failover_status()["final_fallback_label"] == "gemini-3.1-flash-lite-preview"


def test_single_provider_uses_fallback_model_after_capability_failure() -> None:
    fallback = FakeLanguageModel("fallback model answer")
    model = FailoverLanguageModel(
        [FakeLanguageModel(ModelCapabilityError("tool calling unsupported"))],
        provider="primary model",
        fallback=fallback,
        fallback_label="fallback-model",
    )

    result = model.run(instructions="use a tool", user_input="question")

    assert result.text == "fallback model answer"
    assert model.failover_status()["final_fallback_used"] is True


def test_multi_provider_chain_moves_to_next_capability_tier() -> None:
    unavailable = FailoverLanguageModel(
        [FakeLanguageModel(ProviderError(429))],
        provider="Provider A",
    )
    working = FailoverLanguageModel(
        [FakeLanguageModel("analyzed answer")],
        provider="Provider B",
    )
    model = ModelFallbackChain([("strong-model", unavailable), ("reliable-model", working)])

    result = model.run(instructions="x", user_input="y")

    assert result.text == "analyzed answer"
    status = model.failover_status()
    assert status["active_model"] == "reliable-model"
    assert status["last_failover"] is True
    assert [route["label"] for route in status["routes"]] == [
        "strong-model",
        "reliable-model",
    ]
    assert status["routes"][0]["last_attempt_count"] == 1
    assert status["routes"][0]["last_attempts"][0]["status"] == "failed"
    assert status["routes"][1]["last_attempts"][0]["status"] == "completed"


def test_multi_provider_chain_bounds_credentials_per_model_tier() -> None:
    unavailable_slots = [FakeLanguageModel(ProviderError(429)) for _ in range(3)]
    unavailable = FailoverLanguageModel(unavailable_slots, provider="Provider A")
    working = FailoverLanguageModel(
        [FakeLanguageModel("analyzed answer")],
        provider="Provider B",
    )
    model = ModelFallbackChain([("strong-model", unavailable), ("next-model", working)])

    result = model.run(instructions="x", user_input="y")

    assert result.text == "analyzed answer"
    assert [slot.calls for slot in unavailable_slots] == [1, 1, 0]
    assert model.failover_status()["active_model"] == "next-model"


@pytest.mark.parametrize("status_code", [400, 404, 413, 422])
def test_model_chain_skips_provider_capability_failures(status_code: int) -> None:
    incompatible = FakeLanguageModel(ProviderError(status_code))
    working = FakeLanguageModel("compatible answer")
    model = ModelFallbackChain(
        [("incompatible-model", incompatible), ("compatible-model", working)]
    )

    result = model.run(instructions="use tools", user_input="question")

    assert result.text == "compatible answer"
    assert incompatible.calls == 1
    assert working.calls == 1
    assert model.failover_status()["active_model"] == "compatible-model"


@pytest.mark.parametrize("status_code", [400, 404, 413, 422])
def test_validated_chain_skips_provider_capability_failures(status_code: int) -> None:
    model = ModelFallbackChain(
        [
            ("incompatible-model", FakeLanguageModel(ProviderError(status_code))),
            ("compatible-model", FakeLanguageModel("valid answer")),
        ]
    )

    result = model.run_validated(
        lambda candidate: candidate.text == "valid answer",
        instructions="validate",
        user_input="question",
    )

    assert result.text == "valid answer"
    assert model.failover_status()["active_model"] == "compatible-model"


def test_response_validation_can_trigger_quality_model_fallback() -> None:
    verbose = FakeLanguageModel("raw json")
    grounded = FakeLanguageModel("verified Persian analysis")
    model = ModelFallbackChain(
        [
            ("verbose-model", verbose),
            ("grounded-model", grounded),
        ]
    )

    result = model.run_validated(
        lambda candidate: candidate.text.startswith("verified"),
        instructions="analyze",
        user_input="results",
    )

    assert result.text == "verified Persian analysis"
    status = model.failover_status()
    assert status["active_model"] == "grounded-model"
    assert status["quality_fallback_used"] is True

    second = model.run_validated(
        lambda candidate: candidate.text.startswith("verified"),
        instructions="analyze again",
        user_input="results",
    )
    assert second.text == "verified Persian analysis"
    assert verbose.calls == 1
    assert grounded.calls == 2


def test_bounded_validation_does_not_scan_the_entire_model_chain() -> None:
    tiers = [(f"model-{index}", FakeLanguageModel("invalid")) for index in range(1, 9)]
    model = ModelFallbackChain(tiers)

    result = model.run_validated_bounded(
        lambda candidate: candidate.text == "valid",
        instructions="compose deck",
        user_input="evidence",
        max_attempts=4,
    )

    assert result.text == "invalid"
    assert sum(tier.calls for _label, tier in tiers) == 4
    assert all(tier.calls == 0 for _label, tier in tiers[4:])


def test_bounded_json_validation_falls_back_to_plain_provider_output() -> None:
    class JsonUnsupportedTier:
        def __init__(self) -> None:
            self.json_calls = 0
            self.plain_calls = 0

        def run_bounded_json(self, **_kwargs) -> GenerationResult:
            self.json_calls += 1
            raise ModelCapabilityError("JSON response mode unsupported")

        def run_bounded_credentials(self, **_kwargs) -> GenerationResult:
            self.plain_calls += 1
            return GenerationResult(text='{"title":"valid"}')

    tier = JsonUnsupportedTier()
    model = ModelFallbackChain([("cross-provider-model", tier)])  # type: ignore[list-item]

    result = model.run_validated_bounded(
        lambda candidate: candidate.text == '{"title":"valid"}',
        instructions="Return JSON",
        user_input="evidence",
    )

    assert result.text == '{"title":"valid"}'
    assert tier.json_calls == 1
    assert tier.plain_calls == 1


def test_bounded_json_timeout_advances_without_repeating_plain_request() -> None:
    class TimedOutTier:
        def __init__(self) -> None:
            self.json_calls = 0
            self.plain_calls = 0

        def run_bounded_json(self, **_kwargs) -> GenerationResult:
            self.json_calls += 1
            raise TimeoutError("structured response exceeded the provider deadline")

        def run_bounded_credentials(self, **_kwargs) -> GenerationResult:
            self.plain_calls += 1
            return GenerationResult(text='{"title":"unexpected duplicate"}')

    class WorkingTier:
        def __init__(self) -> None:
            self.max_output_tokens = None

        def run_bounded_json(self, **kwargs) -> GenerationResult:
            self.max_output_tokens = kwargs.get("max_output_tokens")
            return GenerationResult(text='{"title":"valid"}')

    timed_out = TimedOutTier()
    working = WorkingTier()
    model = ModelFallbackChain(
        [("slow-model", timed_out), ("fast-model", working)]  # type: ignore[list-item]
    )

    result = model.run_validated_bounded(
        lambda candidate: candidate.text == '{"title":"valid"}',
        instructions="Return JSON",
        user_input="evidence",
        max_output_tokens=3_000,
    )

    assert result.text == '{"title":"valid"}'
    assert timed_out.json_calls == 1
    assert timed_out.plain_calls == 0
    assert working.max_output_tokens == 3_000


def test_quality_fallback_retries_strongest_model_after_cooldown() -> None:
    clock = {"now": 0.0}
    primary = FakeLanguageModel("invalid")
    fallback = FakeLanguageModel("valid fallback")
    model = ModelFallbackChain(
        [("primary", primary), ("fallback", fallback)],
        quality_cooldown_seconds=60,
        clock=lambda: clock["now"],
    )

    def validator(candidate: GenerationResult) -> bool:
        return candidate.text.startswith("valid")

    assert (
        model.run_validated(
            validator,
            validation_profile="persian_rag",
            instructions="x",
            user_input="y",
        ).text
        == "valid fallback"
    )

    clock["now"] = 30.0
    assert (
        model.run_validated(
            validator,
            validation_profile="persian_rag",
            instructions="x",
            user_input="y",
        ).text
        == "valid fallback"
    )
    assert primary.calls == 1

    primary.result = "valid primary"
    clock["now"] = 61.0
    assert (
        model.run_validated(
            validator,
            validation_profile="persian_rag",
            instructions="x",
            user_input="y",
        ).text
        == "valid primary"
    )
    assert primary.calls == 2


def test_non_retryable_programming_error_is_not_hidden_by_failover() -> None:
    second_called = False

    def operation(resource: str, _index: int) -> str:
        nonlocal second_called
        if resource == "first":
            raise ValueError("invalid request schema")
        second_called = True
        return "ok"

    with pytest.raises(ValueError, match="invalid request schema"):
        ProviderPool(["first", "second"], provider="test").run(operation)
    assert second_called is False


def test_transient_ssl_connect_error_rotates_to_next_resource() -> None:
    class ConnectError(RuntimeError):
        pass

    def operation(resource: str, _index: int) -> str:
        if resource == "first":
            raise ConnectError("SSL unexpected EOF")
        return "ok"

    assert ProviderPool(["first", "second"], provider="test").run(operation) == "ok"


class FailingEmbeddingModels:
    def embed_content(self, **_kwargs):
        raise ProviderError(429)


class WorkingEmbeddingModels:
    def embed_content(self, **kwargs):
        return SimpleNamespace(
            embeddings=[SimpleNamespace(values=[1.0, 2.0, 0.0]) for _ in kwargs["contents"]]
        )


def test_embedding_rotates_credentials_and_normalizes_result() -> None:
    clients = [
        SimpleNamespace(models=FailingEmbeddingModels()),
        SimpleNamespace(models=WorkingEmbeddingModels()),
    ]
    embedder = GeminiEmbedder(
        "gemini-embedding-2",
        ["key-1", "key-2"],
        dimensions=3,
        requests_per_minute=1000,
        clients=clients,
    )

    vector = embedder.embed_query("query")

    assert np.isclose(np.linalg.norm(vector), 1.0)
    assert embedder.failover_status()["active_slot"] == 2


class FailingIndex:
    def search(self, *_args, **_kwargs):
        raise ProviderFailoverError("cloud unavailable")

    def count(self) -> int:
        return 0


class LocalIndex:
    def __init__(self) -> None:
        self.searches = 0

    def search(self, *_args, **_kwargs):
        self.searches += 1
        return ["local evidence"]


def test_embedding_index_uses_local_collection_after_key_exhaustion() -> None:
    local = LocalIndex()
    index = FallbackEmbeddingIndex(FailingIndex(), lambda: local)  # type: ignore[arg-type]

    result = index.search("question")

    assert result == ["local evidence"]
    assert local.searches == 1
    assert index.last_backend == "fallback"


def test_settings_deduplicate_primary_and_fallback_keys() -> None:
    settings = Settings(
        _env_file=None,
        llm_base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        llm_api_key="primary",
        gemini_api_keys="second, primary, third\nsecond",
        google_api_keys="",
        embedding_provider="gemini",
        embedding_api_key="embedding",
    )

    assert settings.llm_api_keys() == ["primary", "second", "third"]
    assert settings.embedding_api_keys() == [
        "embedding",
        "primary",
        "second",
        "third",
    ]


def test_openai_key_is_never_reused_for_google_or_gemini_embeddings() -> None:
    settings = Settings(
        _env_file=None,
        llm_provider="openai",
        llm_model="gpt-example",
        llm_base_url="https://api.openai.com/v1",
        llm_api_key="openai-secret",
        gemini_api_keys="google-one",
        google_api_keys="google-two",
        embedding_provider="gemini",
        embedding_api_key="embedding-google",
    )

    assert settings.llm_api_keys() == ["openai-secret"]
    assert settings.google_llm_keys() == ["google-one", "google-two"]
    assert settings.embedding_api_keys() == [
        "embedding-google",
        "google-one",
        "google-two",
    ]


def test_media_provider_keys_are_deduplicated_for_pooling() -> None:
    settings = Settings(
        _env_file=None,
        ocr_space_api_key="ocr-one",
        ocr_space_api_keys="ocr-two,ocr-one",
        groq_api_key="speech-one",
        groq_speech_api_keys="speech-two,speech-one",
        groq_chat_api_keys="speech-three",
    )

    assert settings.ocr_space_keys() == ["ocr-one", "ocr-two"]
    assert settings.groq_speech_keys() == [
        "speech-one",
        "speech-two",
        "speech-three",
    ]
    assert "ocr-one" not in repr(settings)
    assert "speech-one" not in repr(settings)
