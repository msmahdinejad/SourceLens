from __future__ import annotations

import threading
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import TypeVar

T = TypeVar("T")
R = TypeVar("R")


class ProviderFailoverError(RuntimeError):
    """All configured provider credentials failed without exposing their values."""


def retryable_provider_error(error: Exception) -> bool:
    """Return whether another credential/provider attempt can reasonably succeed."""
    if isinstance(error, ProviderFailoverError):
        return True
    status = getattr(error, "status_code", None) or getattr(error, "code", None)
    response = getattr(error, "response", None)
    status = status or getattr(response, "status_code", None)
    try:
        numeric_status = int(status)
    except (TypeError, ValueError):
        numeric_status = None
    if numeric_status == 400 and "user location is not supported" in str(error).casefold():
        return True
    # Google can return 404 when a model is unavailable to one API key's
    # project even though a key from another existing project can use it.
    if numeric_status in {401, 403, 404, 408, 409, 429}:
        return True
    if numeric_status is not None and 500 <= numeric_status <= 599:
        return True
    if isinstance(error, (ConnectionError, TimeoutError)):
        return True
    name = type(error).__name__.casefold()
    return any(
        marker in name
        for marker in (
            "ratelimit",
            "timeout",
            "connect",
            "connection",
            "ssl",
            "eof",
            "authentication",
            "permission",
            "servererror",
        )
    )


def retryable_model_tier_error(error: Exception) -> bool:
    """Return whether another model/provider tier may support the same request.

    Credential pools stay conservative for malformed 400/422 requests; a 404 is
    retried because provider projects can have different model access. At the
    model-tier seam, these statuses commonly
    mean unsupported model, tool schema, or generated tool call and should advance
    to the next independently validated adapter.
    """
    # Imported lazily to keep the generic provider module independent at import time.
    from paperlens.language_models.base import ModelCapabilityError

    if isinstance(error, ModelCapabilityError) or retryable_provider_error(error):
        return True
    return _error_status(error) in {400, 404, 405, 413, 415, 422}


def retryable_search_provider_error(error: Exception) -> bool:
    """Classify transient HTTP search/media failures for shared credential pools."""

    return retryable_provider_error(error) or _error_status(error) == 425


class ProviderPool[T]:
    """Thread-safe ordered failover with cooldown and secret-safe diagnostics."""

    def __init__(
        self,
        resources: Sequence[T],
        *,
        provider: str,
        cooldown_seconds: float = 60.0,
        retryable: Callable[[Exception], bool] = retryable_provider_error,
        minimum_interval_seconds: float = 0.0,
        global_minimum_interval_seconds: float = 0.0,
        retry_rounds: int = 1,
        wait_for_cooldown: bool = False,
        restore_priority_after_cooldown: bool = True,
        max_wait_seconds: float = 120.0,
        clock: Callable[[], float] | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if not resources:
            raise ValueError("At least one provider resource is required")
        self._resources = tuple(resources)
        self.provider = provider
        self.cooldown_seconds = cooldown_seconds
        self._retryable = retryable
        self.minimum_interval_seconds = max(0.0, minimum_interval_seconds)
        self.global_minimum_interval_seconds = max(0.0, global_minimum_interval_seconds)
        self.retry_rounds = max(1, retry_rounds)
        self.wait_for_cooldown = wait_for_cooldown
        self.restore_priority_after_cooldown = restore_priority_after_cooldown
        self.max_wait_seconds = max(0.0, max_wait_seconds)
        self._clock = clock or (lambda: time.monotonic())
        self._sleeper = sleeper
        self._active_index = 0
        self._cooldowns: dict[int, float] = {}
        self._next_allowed: dict[int, float] = {}
        self._global_next_allowed = 0.0
        self._last_attempt_count = 0
        self._last_failover = False
        self._total_rate_wait_ms = 0
        self._last_attempts: list[dict[str, object]] = []
        self._lock = threading.RLock()

    def run(
        self,
        operation: Callable[[T, int], R],
        *,
        max_attempts: int | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> R:
        failures: list[tuple[int, int | None, str]] = []
        attempt_records: list[dict[str, object]] = []
        total_attempts = 0
        for _round in range(self.retry_rounds):
            candidates = self._candidates()
            for index in candidates:
                if should_cancel is not None and should_cancel():
                    raise ProviderFailoverError(f"{self.provider} request was cancelled")
                if max_attempts is not None and total_attempts >= max(1, max_attempts):
                    break
                total_attempts += 1
                wait_seconds = self._reserve(index, should_cancel=should_cancel)
                started = self._clock()
                try:
                    result = operation(self._resources[index], index)
                except Exception as error:
                    duration_ms = round((self._clock() - started) * 1000)
                    attempt_records.append(
                        {
                            "slot": index + 1,
                            "status": "failed",
                            "status_code": _error_status(error),
                            "error_type": type(error).__name__,
                            "wait_ms": round(wait_seconds * 1000),
                            "duration_ms": duration_ms,
                        }
                    )
                    if not self._retryable(error):
                        self._record_attempts(attempt_records, total_attempts)
                        raise
                    failures.append((index, _error_status(error), type(error).__name__))
                    self._mark_failure(index, error)
                    continue
                attempt_records.append(
                    {
                        "slot": index + 1,
                        "status": "completed",
                        "status_code": None,
                        "error_type": None,
                        "wait_ms": round(wait_seconds * 1000),
                        "duration_ms": round((self._clock() - started) * 1000),
                    }
                )
                with self._lock:
                    self._active_index = index
                    self._cooldowns.pop(index, None)
                    self._last_attempt_count = total_attempts
                    self._last_failover = total_attempts > 1
                    self._last_attempts = attempt_records[-20:]
                return result

        with self._lock:
            self._last_attempt_count = total_attempts
            self._last_failover = total_attempts > 1
            self._last_attempts = attempt_records[-20:]
        attempts = ", ".join(
            f"slot {index + 1}: {status or error_type}" for index, status, error_type in failures
        )
        raise ProviderFailoverError(
            f"All {total_attempts} attempted {self.provider} credentials failed"
            + (f" ({attempts})" if attempts else "")
        )

    def status(self) -> dict[str, object]:
        now = self._clock()
        with self._lock:
            unavailable = [index + 1 for index, until in self._cooldowns.items() if until > now]
            return {
                "provider": self.provider,
                "credential_count": len(self._resources),
                "active_slot": self._active_index + 1,
                "last_attempt_count": self._last_attempt_count,
                "last_failover": self._last_failover,
                "cooldown_slots": sorted(unavailable),
                "total_rate_wait_ms": self._total_rate_wait_ms,
                "last_attempts": list(self._last_attempts),
            }

    def _candidates(self) -> list[int]:
        now = self._clock()
        with self._lock:
            expired = [index for index, until in self._cooldowns.items() if until <= now]
            for index in expired:
                self._cooldowns.pop(index, None)
            # A failed first-choice provider is sticky-skipped only during its
            # cooldown. As soon as that window expires, probe the configured
            # priority order again instead of remaining on a fallback forever.
            if self.restore_priority_after_cooldown and 0 in expired:
                self._active_index = 0
            order = list(range(self._active_index, len(self._resources))) + list(
                range(0, self._active_index)
            )
            available = [index for index in order if self._cooldowns.get(index, 0.0) <= now]
            # Media pools opt into waiting for cooldowns. Other provider pools
            # preserve the original best-effort behavior and immediately probe
            # the configured order when every slot is cooling down.
            return available or order

    def _reserve(
        self,
        index: int,
        *,
        should_cancel: Callable[[], bool] | None = None,
    ) -> float:
        with self._lock:
            now = self._clock()
            cooldown_ready = self._cooldowns.get(index, 0.0) if self.wait_for_cooldown else now
            ready_at = max(
                now,
                cooldown_ready,
                self._next_allowed.get(index, 0.0),
                self._global_next_allowed,
            )
            wait_seconds = max(0.0, ready_at - now)
            if wait_seconds > self.max_wait_seconds:
                raise ProviderFailoverError(
                    f"{self.provider} is cooling down longer than the configured wait"
                )
            self._next_allowed[index] = ready_at + self.minimum_interval_seconds
            self._global_next_allowed = ready_at + self.global_minimum_interval_seconds
            self._total_rate_wait_ms += round(wait_seconds * 1000)
        remaining = wait_seconds
        while remaining > 0:
            if should_cancel is not None and should_cancel():
                raise ProviderFailoverError(f"{self.provider} request was cancelled")
            interval = min(0.1, remaining) if should_cancel is not None else remaining
            self._sleeper(interval)
            remaining -= interval
        return wait_seconds

    def _mark_failure(self, index: int, error: Exception) -> None:
        with self._lock:
            cooldown = max(self.cooldown_seconds, _retry_after_seconds(error))
            self._cooldowns[index] = self._clock() + cooldown
            self._active_index = (index + 1) % len(self._resources)

    def _record_attempts(
        self,
        attempts: list[dict[str, object]],
        total_attempts: int,
    ) -> None:
        with self._lock:
            self._last_attempt_count = total_attempts
            self._last_failover = total_attempts > 1
            self._last_attempts = attempts[-20:]


def _error_status(error: Exception) -> int | None:
    status = getattr(error, "status_code", None) or getattr(error, "code", None)
    response = getattr(error, "response", None)
    status = status or getattr(response, "status_code", None)
    try:
        return int(status)
    except (TypeError, ValueError):
        return None


def _retry_after_seconds(error: Exception) -> float:
    response = getattr(error, "response", None)
    headers = getattr(response, "headers", None) or {}
    value = headers.get("retry-after") or headers.get("Retry-After")
    try:
        return max(0.0, min(600.0, float(value)))
    except (TypeError, ValueError):
        pass
    try:
        retry_at = parsedate_to_datetime(str(value))
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=UTC)
        return max(0.0, min(600.0, (retry_at - datetime.now(UTC)).total_seconds()))
    except (TypeError, ValueError, OverflowError):
        return 0.0
