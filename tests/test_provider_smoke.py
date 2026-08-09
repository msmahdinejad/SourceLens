from __future__ import annotations

from paperlens.config import Settings
from paperlens.language_models.base import ExecutedToolCall, GenerationResult
from paperlens.model_routes import ModelRoute
from paperlens.provider_smoke import run_live_provider_smoke


def test_live_provider_smoke_uses_strongest_route_and_redacts_secrets(monkeypatch) -> None:
    routes = [
        ModelRoute("Google strong", "gemini-strong", "Gemini", "https://g.test", "google", 2),
        ModelRoute("Google weak", "gemini-weak", "Gemini", "https://g.test", "google", 2),
        ModelRoute("NVIDIA strong", "nemotron", "NVIDIA", "https://n.test", "nvidia", 1),
    ]
    secrets = {"google": ["secret-google"], "nvidia": ["secret-nvidia"]}
    monkeypatch.setattr(
        "paperlens.provider_smoke.configured_model_routes",
        lambda _settings: routes,
    )
    monkeypatch.setattr(
        "paperlens.provider_smoke.route_api_keys",
        lambda _settings, route: secrets[route.credential_pool],
    )
    created = []

    class Model:
        def __init__(self, route, key) -> None:
            self.route = route
            self.key = key
            self.calls = 0
            created.append(route.model)

        def run(self, **kwargs):
            self.calls += 1
            if self.route.credential_pool == "nvidia":
                raise RuntimeError(f"429 quota for {self.key}")
            if kwargs.get("tools"):
                first = kwargs["execute_tool"]("sourcelens_smoke", {"value": "subject-a"})
                second = kwargs["execute_tool"]("sourcelens_smoke", {"value": "subject-b"})
                return GenerationResult(
                    text="",
                    tool_calls=[
                        ExecutedToolCall(
                            name="sourcelens_smoke",
                            arguments={"value": "subject-a"},
                            output=first,
                        ),
                        ExecutedToolCall(
                            name="sourcelens_smoke",
                            arguments={"value": "subject-b"},
                            output=second,
                        ),
                    ],
                )
            return GenerationResult(text="SOURCELENS_SMOKE_OK")

    results = run_live_provider_smoke(
        Settings(_env_file=None),
        model_factory=lambda route, key: Model(route, key),
    )

    assert created == ["gemini-strong", "nemotron"]
    assert [(item.text_status, item.tool_status) for item in results] == [
        ("pass", "pass"),
        ("quota-blocked", "quota-blocked"),
    ]
    assert all(item.slot == 1 for item in results)
    assert "secret-google" not in repr(results)
    assert "secret-nvidia" not in repr(results)
