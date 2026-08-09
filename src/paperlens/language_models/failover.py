from __future__ import annotations

import threading
import time
from collections.abc import Callable, Sequence

from paperlens.language_models.base import (
    GenerationResult,
    LanguageModel,
    ModelCapabilityError,
    ToolChoice,
    ToolExecutor,
    ToolSpec,
)
from paperlens.provider_failover import (
    ProviderFailoverError,
    ProviderPool,
    retryable_model_tier_error,
)


def _can_retry_without_json_mode(error: Exception) -> bool:
    """Return whether removing response_format can change this failure.

    Timeouts, quotas, and oversized requests are independent of JSON mode. Repeating
    those requests as plain text only doubles artifact latency before tier failover.
    """
    if isinstance(error, ModelCapabilityError):
        return True
    status = getattr(error, "status_code", None) or getattr(error, "code", None)
    response = getattr(error, "response", None)
    status = status or getattr(response, "status_code", None)
    try:
        return int(status) in {400, 405, 415, 422}
    except (TypeError, ValueError):
        return False


class FailoverLanguageModel:
    """Run the same request against an ordered pool of model credentials."""

    def __init__(
        self,
        models: Sequence[LanguageModel],
        *,
        provider: str,
        fallback: LanguageModel | None = None,
        fallback_label: str | None = None,
        cooldown_seconds: float = 60.0,
        retry_rounds: int = 1,
    ) -> None:
        self._pool = ProviderPool(
            models,
            provider=provider,
            cooldown_seconds=cooldown_seconds,
            retry_rounds=retry_rounds,
        )
        self._fallback = fallback
        self._fallback_label = fallback_label
        self._last_final_fallback = False
        self._lock = threading.Lock()

    def run(
        self,
        *,
        instructions: str,
        user_input: str,
        tools: Sequence[ToolSpec] = (),
        execute_tool: ToolExecutor | None = None,
        tool_choice: ToolChoice = "auto",
        return_after_tool: bool = False,
    ) -> GenerationResult:
        try:
            result = self._pool.run(
                lambda model, _index: model.run(
                    instructions=instructions,
                    user_input=user_input,
                    tools=tools,
                    execute_tool=execute_tool,
                    tool_choice=tool_choice,
                    return_after_tool=return_after_tool,
                )
            )
        except (ProviderFailoverError, ModelCapabilityError):
            if self._fallback is None:
                raise
            with self._lock:
                self._last_final_fallback = True
            return self._fallback.run(
                instructions=instructions,
                user_input=user_input,
                tools=tools,
                execute_tool=execute_tool,
                tool_choice=tool_choice,
                return_after_tool=return_after_tool,
            )
        with self._lock:
            self._last_final_fallback = False
        return result

    def run_bounded_credentials(
        self,
        *,
        instructions: str,
        user_input: str,
        max_attempts: int = 1,
        tools: Sequence[ToolSpec] = (),
        execute_tool: ToolExecutor | None = None,
        tool_choice: ToolChoice = "auto",
        return_after_tool: bool = False,
    ) -> GenerationResult:
        """Run a latency-sensitive artifact call against a bounded key sample."""
        result = self._pool.run(
            lambda model, _index: model.run(
                instructions=instructions,
                user_input=user_input,
                tools=tools,
                execute_tool=execute_tool,
                tool_choice=tool_choice,
                return_after_tool=return_after_tool,
            ),
            max_attempts=max_attempts,
        )
        with self._lock:
            self._last_final_fallback = False
        return result

    def run_bounded_json(
        self,
        *,
        instructions: str,
        user_input: str,
        max_attempts: int = 1,
        max_output_tokens: int | None = None,
        tools: Sequence[ToolSpec] = (),
        execute_tool: ToolExecutor | None = None,
        tool_choice: ToolChoice = "auto",
        return_after_tool: bool = False,
    ) -> GenerationResult:
        """Use provider JSON mode with a bounded credential sample."""

        def generate(model: LanguageModel, _index: int) -> GenerationResult:
            json_runner = getattr(model, "run_json", None)
            if not callable(json_runner):
                raise ModelCapabilityError("The model adapter does not support JSON mode")
            arguments = {
                "instructions": instructions,
                "user_input": user_input,
                "tools": tools,
                "execute_tool": execute_tool,
                "tool_choice": tool_choice,
                "return_after_tool": return_after_tool,
            }
            if max_output_tokens is not None:
                arguments["max_output_tokens"] = max_output_tokens
            return json_runner(**arguments)

        result = self._pool.run(generate, max_attempts=max_attempts)
        with self._lock:
            self._last_final_fallback = False
        return result

    def failover_status(self) -> dict[str, object]:
        status = self._pool.status()
        with self._lock:
            final_fallback_used = self._last_final_fallback
        return {
            **status,
            "final_fallback_configured": self._fallback is not None,
            "final_fallback_label": self._fallback_label,
            "final_fallback_used": final_fallback_used,
            "last_failover": bool(status["last_failover"] or final_fallback_used),
        }


class ModelFallbackChain:
    """Capability-validated model tiers spanning multiple API providers."""

    def __init__(
        self,
        tiers: Sequence[tuple[str, LanguageModel]],
        *,
        quality_cooldown_seconds: float = 60.0,
        pool_cooldown_seconds: float = 60.0,
        retry_rounds: int = 1,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not tiers:
            raise ValueError("At least one LLM tier is required")
        self._labels = tuple(label for label, _model in tiers)
        self._models = tuple(model for _label, model in tiers)
        self._pool = ProviderPool(
            self._models,
            provider="LLM model tier",
            retryable=retryable_model_tier_error,
            cooldown_seconds=pool_cooldown_seconds,
            retry_rounds=retry_rounds,
        )
        self._quality_cooldown_seconds = quality_cooldown_seconds
        self._clock = clock
        self._quality_active_index: int | None = None
        self._quality_profiles: dict[str, tuple[int, float]] = {}
        self._quality_last_failover = False
        self._quality_lock = threading.Lock()

    def run(
        self,
        *,
        instructions: str,
        user_input: str,
        tools: Sequence[ToolSpec] = (),
        execute_tool: ToolExecutor | None = None,
        tool_choice: ToolChoice = "auto",
        return_after_tool: bool = False,
    ) -> GenerationResult:
        def generate(model: LanguageModel, _index: int) -> GenerationResult:
            bounded_credentials = getattr(model, "run_bounded_credentials", None)
            if callable(bounded_credentials):
                return bounded_credentials(
                    instructions=instructions,
                    user_input=user_input,
                    max_attempts=2,
                    tools=tools,
                    execute_tool=execute_tool,
                    tool_choice=tool_choice,
                    return_after_tool=return_after_tool,
                )
            return model.run(
                instructions=instructions,
                user_input=user_input,
                tools=tools,
                execute_tool=execute_tool,
                tool_choice=tool_choice,
                return_after_tool=return_after_tool,
            )

        result = self._pool.run(
            generate
        )
        with self._quality_lock:
            self._quality_active_index = None
            self._quality_last_failover = False
        return result

    def run_validated(
        self,
        validator: Callable[[GenerationResult], bool],
        *,
        instructions: str,
        user_input: str,
        validation_profile: str = "default",
        tools: Sequence[ToolSpec] = (),
        execute_tool: ToolExecutor | None = None,
        tool_choice: ToolChoice = "auto",
        return_after_tool: bool = False,
        max_attempts: int | None = None,
        max_credential_attempts: int | None = None,
        json_mode: bool = False,
        max_output_tokens: int | None = None,
    ) -> GenerationResult:
        """Try successive model tiers until response-level quality checks pass."""
        now = self._clock()
        with self._quality_lock:
            previous_profile = self._quality_profiles.get(validation_profile)
            if (
                previous_profile is not None
                and now - previous_profile[1] >= self._quality_cooldown_seconds
            ):
                self._quality_profiles.pop(validation_profile, None)
                previous_profile = None
        previous_quality_index = previous_profile[0] if previous_profile is not None else None
        start = (
            previous_quality_index
            if previous_quality_index is not None
            else int(self._pool.status()["active_slot"]) - 1
        )
        order = list(range(start, len(self._models))) + list(range(0, start))
        if max_attempts is not None:
            order = order[: max(1, max_attempts)]
        attempted = 0
        last_result: GenerationResult | None = None
        last_index = start
        for index in order:
            attempted += 1
            try:
                model = self._models[index]
                bounded_credentials = getattr(
                    model,
                    "run_bounded_json" if json_mode else "run_bounded_credentials",
                    None,
                )
                if max_credential_attempts is not None and callable(bounded_credentials):
                    try:
                        arguments = {
                            "instructions": instructions,
                            "user_input": user_input,
                            "max_attempts": max_credential_attempts,
                            "tools": tools,
                            "execute_tool": execute_tool,
                            "tool_choice": tool_choice,
                            "return_after_tool": return_after_tool,
                        }
                        if json_mode and max_output_tokens is not None:
                            arguments["max_output_tokens"] = max_output_tokens
                        result = bounded_credentials(**arguments)
                    except Exception as error:
                        plain_runner = getattr(model, "run_bounded_credentials", None)
                        if (
                            not json_mode
                            or not _can_retry_without_json_mode(error)
                            or not callable(plain_runner)
                        ):
                            raise
                        result = plain_runner(
                            instructions=instructions,
                            user_input=user_input,
                            max_attempts=max_credential_attempts,
                            tools=tools,
                            execute_tool=execute_tool,
                            tool_choice=tool_choice,
                            return_after_tool=return_after_tool,
                        )
                else:
                    result = model.run(
                        instructions=instructions,
                        user_input=user_input,
                        tools=tools,
                        execute_tool=execute_tool,
                        tool_choice=tool_choice,
                        return_after_tool=return_after_tool,
                    )
            except Exception as error:
                if retryable_model_tier_error(error):
                    continue
                raise
            last_result = result
            last_index = index
            if not validator(result):
                continue
            with self._quality_lock:
                self._quality_active_index = index
                current_profile = self._quality_profiles.get(validation_profile)
                selected_at = (
                    current_profile[1]
                    if current_profile is not None and current_profile[0] == index
                    else self._clock()
                )
                self._quality_profiles[validation_profile] = (index, selected_at)
                self._quality_last_failover = attempted > 1
            return result
        if last_result is not None:
            with self._quality_lock:
                self._quality_active_index = last_index
                self._quality_last_failover = attempted > 1
            return last_result
        raise ProviderFailoverError(
            f"All {len(order)} configured LLM tiers failed response validation"
        )

    def run_validated_bounded(
        self,
        validator: Callable[[GenerationResult], bool],
        *,
        instructions: str,
        user_input: str,
        validation_profile: str = "default",
        max_attempts: int = 3,
        max_credential_attempts: int = 2,
        max_output_tokens: int | None = None,
        tools: Sequence[ToolSpec] = (),
        execute_tool: ToolExecutor | None = None,
        tool_choice: ToolChoice = "auto",
        return_after_tool: bool = False,
    ) -> GenerationResult:
        """Validate against a small quality tier instead of scanning every model.

        Large structured artifacts should finish on a strong model or fail into their
        explicit artifact fallback. Walking the entire global chat chain turns one deck
        into a many-minute serial job and eventually selects models too small for it.
        """
        return self.run_validated(
            validator,
            instructions=instructions,
            user_input=user_input,
            validation_profile=validation_profile,
            tools=tools,
            execute_tool=execute_tool,
            tool_choice=tool_choice,
            return_after_tool=return_after_tool,
            max_attempts=max_attempts,
            max_credential_attempts=max_credential_attempts,
            json_mode=True,
            max_output_tokens=max_output_tokens,
        )

    def failover_status(self) -> dict[str, object]:
        status = self._pool.status()
        with self._quality_lock:
            quality_index = self._quality_active_index
            quality_failover = self._quality_last_failover
        active_slot = quality_index + 1 if quality_index is not None else int(status["active_slot"])
        routes: list[dict[str, object]] = []
        credential_count = 0
        child_failover_used = False
        for label, model in zip(self._labels, self._models, strict=True):
            model_status = (
                model.failover_status()
                if hasattr(model, "failover_status")
                else {"credential_count": 1, "active_slot": 1}
            )
            count = int(model_status.get("credential_count", 1))
            credential_count += count
            child_failover_used = bool(
                child_failover_used or model_status.get("last_failover", False)
            )
            routes.append(
                {
                    "label": label,
                    "provider": model_status.get("provider", "API"),
                    "credential_count": count,
                    "active_key_slot": model_status.get("active_slot", 1),
                    "last_failover": model_status.get("last_failover", False),
                    "last_attempt_count": model_status.get("last_attempt_count", 0),
                    "cooldown_slots": model_status.get("cooldown_slots", []),
                    "last_attempts": model_status.get("last_attempts", []),
                }
            )
        return {
            **status,
            "provider": "Multi-provider capability route",
            "model_count": len(self._models),
            "credential_count": credential_count,
            "active_slot": active_slot,
            "active_model": self._labels[active_slot - 1],
            "routes": routes,
            "quality_fallback_used": quality_failover,
            "last_failover": bool(
                status["last_failover"] or quality_failover or child_failover_used
            ),
        }
