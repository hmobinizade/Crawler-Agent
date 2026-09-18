from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, Field, HttpUrl

ExtractionMethod = Literal["local", "requests_bs4", "playwright"]

class DiscoverRequest(BaseModel):
    url: HttpUrl
    template: dict[str, Any] = Field(min_length=1)

class ConfirmRequest(BaseModel):
    approved: bool

class CodegenRequest(BaseModel):
    # Either a previously returned DiscoveryResult JSON object or a raw extraction structure.
    structure: dict[str, Any] | None = None
    url: HttpUrl | None = None
    html: str | None = None
    source_job_id: str | None = None


class CrawlRequest(BaseModel):
    source_job_id: str | None = None
    structure_id: str | None = None
    structure: dict[str, Any] | None = None
    mode: Literal["generic", "custom"] = "generic"
    url: HttpUrl | None = None
    max_pages: int = Field(default=20, ge=1, le=500)
    timeout_seconds: int = Field(default=240, ge=30, le=1800)
    headless: bool = True


class FieldPlan(BaseModel):
    name: str
    path: str
    description: str = ""
    required: bool = True
    value_type: Literal["string", "number", "boolean", "array", "object", "null"] = "string"
    selector: str | None = None
    selector_type: Literal["css", "xpath", "meta", "jsonld", "attribute", "text", "none"] = "none"
    attribute: str | None = None
    selector_candidates: list[str] = Field(default_factory=list)
    scope_selector: str | None = None
    extraction_hint: str = ""
    found: bool = False
    value: Any = None
    evidence: str | None = None
    confidence: float = 0.0
    item_selector: str | None = None
    item_fields: dict[str, Any] = Field(default_factory=dict)
    collection_count: int | None = None

class DiscoveryResult(BaseModel):
    status: Literal["READY", "NEEDS_HUMAN", "INCOMPLETE", "FAILED"]
    host: str
    url: str
    page_title: str = ""
    anti_bot_state: str = "NONE"
    method: ExtractionMethod = "playwright"
    method_reason: str = ""
    source: Literal["local", "static", "playwright", "none"] = "none"
    local_crawler: str | None = None
    template: dict[str, Any] = Field(default_factory=dict)
    proposed_json: dict[str, Any] = Field(default_factory=dict)
    fields: list[FieldPlan] = Field(default_factory=list)
    missing_required: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    crawler_notes: list[str] = Field(default_factory=list)
    trace: list[str] = Field(default_factory=list)

class Job(BaseModel):
    id: str
    discovery: DiscoveryResult
    confirmed: bool = False
    generated_files: list[str] = Field(default_factory=list)
    browser_session_id: str | None = None
    needs_browser_analysis: bool = False
