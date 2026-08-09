from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, create_model


class WorkflowRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    artifact_type: str
    workspace_id: str = Field(min_length=1, max_length=81)
    document_ids: list[str] = Field(min_length=1, max_length=20)
    language: Literal["auto", "en", "fa"] = "auto"
    title: str | None = Field(default=None, max_length=180)
    options: dict[str, Any] | BaseModel = Field(default_factory=dict)


class WorkflowPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_type: str
    retrieval_focus: str
    guidance: str


class WorkflowResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_type: str
    title: str
    content_markdown: str = ""
    data: dict[str, Any] = Field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class WorkflowModels:
    request: type[WorkflowRequest]
    plan: type[WorkflowPlan]
    result: type[WorkflowResult]


@dataclass(frozen=True, slots=True)
class _Workflow:
    artifact_type: str
    fields: dict[str, dict[str, Any]]
    models: WorkflowModels
    creatable: bool
    normalizer: Any = None


def _option_model(name: str, fields: dict[str, dict[str, Any]]) -> type[BaseModel]:
    definitions: dict[str, tuple[Any, Any]] = {}
    for field_name, schema in fields.items():
        kind = schema["type"]
        default = schema["default"]
        if kind == "choice":
            annotation = Literal.__getitem__(tuple(schema["choices"]))
            value = default
        elif kind == "integer":
            annotation = int
            value = Field(default=default, ge=schema["minimum"], le=schema["maximum"])
        elif kind == "boolean":
            annotation = bool
            value = default
        elif kind == "number":
            annotation = float
            value = Field(default=default, ge=schema["minimum"], le=schema["maximum"])
        elif kind == "participants":
            annotation = list[dict[str, Any]]
            value = default
        else:
            annotation = str
            value = Field(default=default, max_length=schema.get("max_length"))
        definitions[field_name] = (annotation, value)
    return create_model(
        f"{name}Options",
        __config__=ConfigDict(extra="forbid"),
        **definitions,
    )


class WorkflowRegistry:
    """Typed Studio workflow lookup with option migration and validation."""

    def __init__(self) -> None:
        self._workflows: dict[str, _Workflow] = {}

    def register(
        self,
        artifact_type: str,
        *,
        fields: dict[str, dict[str, Any]],
        retrieval_focus: str,
        guidance: str,
        creatable: bool,
        normalizer: Any = None,
        typed_options: bool = True,
    ) -> None:
        name = artifact_type.title().replace("_", "") + "Workflow"
        literal = Literal.__getitem__(artifact_type)
        options = _option_model(name, fields) if typed_options else dict[str, Any]
        request = create_model(
            f"{name}Request",
            __base__=WorkflowRequest,
            artifact_type=(literal, artifact_type),
            options=(options, Field(default_factory=dict)),
        )
        plan = create_model(
            f"{name}Plan",
            __base__=WorkflowPlan,
            artifact_type=(literal, artifact_type),
            retrieval_focus=(str, retrieval_focus),
            guidance=(str, guidance),
        )
        result = create_model(
            f"{name}Result",
            __base__=WorkflowResult,
            artifact_type=(literal, artifact_type),
        )
        self._workflows[artifact_type] = _Workflow(
            artifact_type=artifact_type,
            fields=deepcopy(fields),
            models=WorkflowModels(request=request, plan=plan, result=result),
            creatable=creatable,
            normalizer=normalizer,
        )

    def contains(self, artifact_type: str) -> bool:
        return artifact_type in self._workflows

    def models(self, artifact_type: str) -> WorkflowModels:
        return self._workflows[artifact_type].models

    def plan(self, artifact_type: str) -> WorkflowPlan:
        workflow = self._workflows[artifact_type]
        return workflow.models.plan()

    def catalog(self) -> list[dict[str, Any]]:
        return [
            {
                "artifact_type": workflow.artifact_type,
                "fields": deepcopy(workflow.fields),
                "defaults": {
                    name: deepcopy(field["default"]) for name, field in workflow.fields.items()
                },
            }
            for workflow in self._workflows.values()
            if workflow.creatable
        ]

    def normalize(self, artifact_type: str, options: dict[str, Any]) -> dict[str, Any]:
        workflow = self._workflows[artifact_type]
        if workflow.normalizer is not None:
            return workflow.normalizer(options)
        if not workflow.creatable:
            return dict(options)

        options = dict(options)
        if artifact_type == "video_overview":
            legacy_minutes = options.pop("duration_minutes", None)
            if (
                "duration_seconds" not in options
                and isinstance(legacy_minutes, (int, float))
                and not isinstance(legacy_minutes, bool)
            ):
                options["duration_seconds"] = round(legacy_minutes * 60)
            options.pop("music_usage", None)

        schema = workflow.fields
        unexpected = set(options) - set(schema)
        if unexpected:
            raise ValueError(f"Unsupported Studio option: {sorted(unexpected)[0]}")
        normalized = {name: field["default"] for name, field in schema.items()}
        normalized.update(options)
        for name, value in normalized.items():
            field = schema[name]
            if field["type"] == "choice" and value not in field["choices"]:
                raise ValueError(f"Unsupported value for Studio option {name}")
            if field["type"] == "integer":
                if not isinstance(value, int) or isinstance(value, bool):
                    raise ValueError(f"Studio option {name} must be an integer")
                if not field["minimum"] <= value <= field["maximum"]:
                    raise ValueError(f"Studio option {name} is outside the allowed range")
            if field["type"] == "boolean" and not isinstance(value, bool):
                raise ValueError(f"Studio option {name} must be a boolean")
            if field["type"] in {"text", "textarea", "font"} and (
                not isinstance(value, str) or len(value) > field["max_length"]
            ):
                raise ValueError(f"Studio option {name} is invalid")
            if field["type"] == "font" and (
                not value.strip()
                or any(character in value for character in ("/", "\\", "\x00", "\r", "\n"))
            ):
                raise ValueError(f"Studio option {name} is invalid")
        if (
            artifact_type == "report"
            and normalized["report_format"] == "custom"
            and not normalized["custom_report_type"].strip()
        ):
            raise ValueError("A custom report brief is required for a custom report")
        if (
            artifact_type == "slide_deck"
            and normalized["deck_format"] == "custom"
            and not normalized["custom_deck_brief"].strip()
        ):
            raise ValueError("A custom deck brief is required for a custom slide deck")
        return normalized
