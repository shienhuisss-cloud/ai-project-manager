"""HTTP 请求与模型结果共用的类型定义。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Intent = Literal["requested", "hypothetical", "excluded", "unclear"]
Kind = Literal["added", "modified", "removed", "clarification", "unchanged", "uncertain"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Evidence(StrictModel):
    source_id: str = Field(min_length=1, max_length=100)
    quote: str = Field(min_length=1, max_length=50000)


class Requirement(StrictModel):
    title: str = Field(min_length=1, max_length=300)
    description: str = Field(default="", max_length=10000)
    intent: Intent
    stated_acceptance_criteria: list[str] = Field(default_factory=list, max_length=100)
    evidence: list[Evidence] = Field(min_length=1, max_length=100)
    questions: list[str] = Field(default_factory=list, max_length=100)

    @field_validator("title")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("标题不能为空")
        return value.strip()


class DraftRequirement(Requirement):
    id: str | None = None


class ExtractResult(StrictModel):
    candidates: list[Requirement] = Field(max_length=500)
    questions: list[str] = Field(default_factory=list, max_length=100)


class Change(StrictModel):
    kind: Kind
    related_requirement_ids: list[str] = Field(default_factory=list, max_length=100)
    proposed_after: str = Field(default="", max_length=10000)
    reason: str = Field(default="", max_length=5000)
    evidence: list[Evidence] = Field(min_length=1, max_length=100)
    questions: list[str] = Field(default_factory=list, max_length=100)
    title: str = Field(default="", max_length=300)
    intent: Intent = "requested"
    stated_acceptance_criteria: list[str] | None = Field(default=None, max_length=100)
    resolved_questions: list[str] = Field(default_factory=list, max_length=100)


class CompareResult(StrictModel):
    changes: list[Change] = Field(max_length=500)


class ProjectCreate(StrictModel):
    name: str = Field(min_length=1, max_length=200)

    @field_validator("name")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("项目名称不能为空")
        return value.strip()


class RegisterRequest(StrictModel):
    username: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=6, max_length=200)


class LoginRequest(StrictModel):
    username: str = Field(min_length=1, max_length=50)
    password: str = Field(min_length=1, max_length=200)


class MemberAddRequest(StrictModel):
    username: str = Field(min_length=1, max_length=50)


class SourceCreate(StrictModel):
    text: str = Field(min_length=1, max_length=200000)
    label: str = Field(default="", max_length=200)
    source_type: Literal["chat", "meeting", "email", "other"] = "chat"


class DraftUpdate(StrictModel):
    requirements: list[DraftRequirement] = Field(default_factory=list, max_length=500)
    questions: list[str] = Field(default_factory=list, max_length=100)
    expected_updated_at: str | None = None
    parent_version_id: str | None = None


class AnalyzeRequest(StrictModel):
    source_ids: list[str] = Field(default_factory=list, max_length=100)
    expected_updated_at: str | None = None


class ConfirmRequest(StrictModel):
    request_id: str = Field(min_length=1, max_length=200)
    expected_updated_at: str | None = None


class CompareRequest(StrictModel):
    source_ids: list[str] = Field(default_factory=list, max_length=100)
    baseline_version_id: str | None = None


class Decision(StrictModel):
    change_index: int = Field(ge=0)
    decision: Literal["accept", "ignore"]
    revised_after: str | None = Field(default=None, max_length=10000)


class ApplyRequest(StrictModel):
    run_id: str
    request_id: str = Field(min_length=1, max_length=200)
    decisions: list[Decision] = Field(min_length=1, max_length=500)


def output_schema(model):
    schema = model.model_json_schema()

    def visit(value):
        if isinstance(value, dict):
            value.pop("default", None)
            if value.get("type") == "object":
                value["additionalProperties"] = False
                value["required"] = list(value.get("properties", {}))
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(schema)
    return schema
