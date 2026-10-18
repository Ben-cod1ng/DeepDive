"""Validated public contracts and agent outputs."""
from typing import Literal
from pydantic import BaseModel, Field, ConfigDict, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Limits(StrictModel):
    subquestions: int = Field(4, ge=1, le=6)
    sources: int = Field(30, ge=1, le=30)
    claims: int = Field(40, ge=1, le=40)
    repair_rounds: int = Field(2, ge=0, le=2)
    model_calls: int = Field(80, ge=5, le=80)
    active_seconds: int = Field(900, ge=30, le=900)


class RunRequest(StrictModel):
    question: str = Field(min_length=1, max_length=10000)
    session_id: str | None = None
    project_id: str | None = None
    source_urls: list[str] = Field(default_factory=list, max_length=10)
    document_ids: list[str] = Field(default_factory=list, max_length=10)
    research_mode: Literal["web", "documents", "mixed"] = "web"
    review_mode: Literal["manual", "auto"] = "manual"
    refresh: bool = False
    limits: Limits = Field(default_factory=Limits)

    @model_validator(mode="after")
    def document_selection(self):
        if self.research_mode == "web" and self.document_ids:
            raise ValueError("Select documents or mixed research mode to use documents")
        if self.research_mode != "web" and not self.document_ids:
            raise ValueError("Select at least one document")
        if self.document_ids and not (self.session_id or self.project_id):
            raise ValueError("Document research requires its existing session or project")
        if self.research_mode == "documents" and self.source_urls:
            raise ValueError("Document-only research cannot include public source URLs")
        if len(set(self.document_ids)) != len(self.document_ids):
            raise ValueError("Document IDs must be unique")
        if len(self.document_ids) > self.limits.sources:
            raise ValueError("Selected documents exceed the source limit")
        return self

    @field_validator("question")
    @classmethod
    def nonempty(cls, value):
        if not value.strip():
            raise ValueError("Question must contain text")
        return value.strip()


class ReviewRequest(StrictModel):
    decision: Literal["approve", "revise", "stop"]
    feedback: str = Field("", max_length=10000)

    @model_validator(mode="after")
    def require_feedback(self):
        if self.decision == "revise" and not self.feedback.strip():
            raise ValueError("Revision requires feedback")
        return self


class Named(StrictModel):
    name: str = Field(min_length=1, max_length=150)

    @field_validator("name")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("Name must contain text")
        return value.strip()


class Preference(StrictModel):
    text: str = Field(min_length=1, max_length=2000)


class Task(StrictModel):
    id: str
    question: str
    facets: list[str]
    queries: list[str] = Field(min_length=1, max_length=2)
    role: Literal["primary", "reporting", "community"]


class Plan(StrictModel):
    resolved_question: str
    time_sensitive: bool
    tasks: list[Task] = Field(min_length=1, max_length=6)


class ToolCall(StrictModel):
    name: Literal["search_web", "fetch_source", "search_evidence", "read_passages", "read_table", "calculate", "recall_memory", "fetch_market_series"]
    arguments: dict = Field(default_factory=dict)


class ToolSelection(StrictModel):
    calls: list[ToolCall] = Field(default_factory=list, max_length=4)
    done: bool = False


class Claim(StrictModel):
    id: str
    task_id: str
    text: str = Field(min_length=1, max_length=2500)
    passage_ids: list[str] = Field(min_length=1, max_length=6)
    kind: Literal["fact", "attributed_opinion", "derived"] = "fact"
    quotes: dict[str, str] = Field(default_factory=dict)


class ClaimBatch(StrictModel):
    claims: list[Claim] = Field(default_factory=list, max_length=40)


class Verdict(StrictModel):
    claim_id: str
    status: Literal["supported", "unsupported", "contradicted", "insufficient"]
    reason: str
    quotes: dict[str, str] = Field(default_factory=dict)


class VerdictBatch(StrictModel):
    decisions: list[Verdict]


class CoverageItem(StrictModel):
    task_id: str
    supported_facets: dict[str, list[str]] = Field(default_factory=dict)
    missing_facets: list[str]
    contradictions: list[str]
    next_queries: list[str] = Field(default_factory=list, max_length=2)


class Coverage(StrictModel):
    items: list[CoverageItem]


class ReportSection(StrictModel):
    title: str
    claim_ids: list[str]


class ReportOutline(StrictModel):
    title: str
    sections: list[ReportSection]
    summary_claim_ids: list[str]


class ContextSummary(StrictModel):
    summary: str


class SummarySelection(StrictModel):
    claim_ids: list[str] = Field(default_factory=list, max_length=8)


class PassageRanking(StrictModel):
    passage_ids: list[str] = Field(default_factory=list, max_length=16)


class SessionRequest(Named):
    project_id: str | None = None
