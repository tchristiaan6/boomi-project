"""Pydantic models for tool envelopes and the final assessment.

Deliberate absences are part of the design:
- PresentationGroup has no is_equivalent field (R5).
- There is no alternatives/substitution model anywhere (R1).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------- provenance

class FilteredOut(BaseModel):
    reason: str
    count: int


class Provenance(BaseModel):
    endpoint: str
    queries: list[str] = Field(default_factory=list)
    total_matched: int = 0
    returned: int = 0
    filtered_out: list[FilteredOut] = Field(default_factory=list)
    fetched_at: str
    warnings: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------- tool data

class ResolveCandidate(BaseModel):
    name: str
    matched_path: str          # e.g. 'shortages.generic_name', 'ndc.brand_name'
    match_count: int
    confidence: Literal["high", "medium", "low"]


class ResolvedDrug(BaseModel):
    query: str
    candidates: list[ResolveCandidate]
    is_marketed: bool | None   # None = could not determine
    paths_tried: list[str]
    paths_agree: bool | None   # None when fewer than two paths returned anything


class ShortageGroup(BaseModel):
    presentation: str                  # parenthetical NDC / dup-strength stripped
    package_ndcs: list[str]            # the specific NDCs in this group
    record_count: int
    statuses: dict[str, int]           # e.g. {"Current": 3}
    availability: dict[str, int]       # verbatim values incl. FDA's own typos
    companies: list[str]
    update_types: dict[str, int]
    shortage_reasons: list[str]
    update_date_range: list[str]       # [earliest_iso, latest_iso]
    initial_posting_range: list[str]
    related_info: list[str]            # verbatim free text, never summarized here
    related_info_links: list[str]


class ShortagePicture(BaseModel):
    ingredient: str
    record_count: int
    status_totals: dict[str, int]
    groups: list[ShortageGroup]


class DiscontinuationRecord(BaseModel):
    generic_name: str
    presentation: str
    company: str
    initial_posting_date: str | None
    update_date: str | None
    related_info: str | None


class Discontinuations(BaseModel):
    ingredient: str
    record_count: int
    records: list[DiscontinuationRecord]


class PresentationGroup(BaseModel):
    """R5: grouped, never judged. No is_equivalent field, by design."""
    parsed_strength: str | None        # None when unparseable - say so, do not guess
    route: str | None
    dosage_forms: list[str]            # FDA's form labels vary; all shown
    raw_strength_strings: list[str]    # verbatim FDA values, always shown
    product_count: int
    firms: list[str]
    marketing_ending: list[str]        # products with a marketing_end_date, as "name (end date)"


class AlternateSources(BaseModel):
    ingredient: str
    marketed_product_count: int
    groups: list[PresentationGroup]
    unparseable_strengths: list[str]   # shown, not guessed at


class RecallRecord(BaseModel):
    recall_number: str | None = None   # FDA's stable id, e.g. D-123-2026
    status: str                        # Ongoing / Completed / Terminated / Pending
    classification: str                # Class I / II / III / Not Yet Classified
    product_description: str
    reason_for_recall: str
    recalling_firm: str
    recall_initiation_date: str | None # ISO
    report_date: str | None            # ISO
    termination_date: str | None       # ISO
    distribution_pattern: str | None


class Recalls(BaseModel):
    query: dict[str, str | None]
    record_count: int
    open_count: int                    # status != Terminated
    by_classification: dict[str, int]
    open_recalls: list[RecallRecord]
    recent_terminated: list[RecallRecord]


class LabelFacts(BaseModel):
    ndc: str
    found: bool
    brand_name: str | None = None
    generic_name: str | None = None
    dosage_form: str | None = None
    route: list[str] = Field(default_factory=list)
    active_ingredients: list[dict] = Field(default_factory=list)
    packaging: list[str] = Field(default_factory=list)
    marketing_start_date: str | None = None
    marketing_end_date: str | None = None
    product_type: str | None = None


class Source(BaseModel):
    """One citation. Built by the harness from provenance, never invented by
    the model, and verified with a live HTTP check before display."""
    url: str
    label: str
    verified: bool | None = None       # None = not checked (e.g. offline mode)
    note: str | None = None


# ---------------------------------------------------------------- assessment

SignalKind = Literal[
    "shortage_current", "discontinuation", "recall_open",
    "recall_historical", "no_fda_signal",
]


class Signal(BaseModel):
    kind: SignalKind
    detail: str
    source_endpoint: str


class Assessment(BaseModel):
    query: str
    resolved_to: str | None
    resolution_confidence: Literal["high", "medium", "low"]
    is_marketed: bool | None           # distinguishes "untracked" from "does not exist"

    # Populated on EVERY assessment, including clean ones. An empty signals
    # list is not valid: FDA silence is itself a signal (no_fda_signal).
    # An assessment that never says what it did not look at is the failure
    # mode this build exists to avoid, so the minimums are schema-enforced.
    signals: list[Signal] = Field(min_length=1)
    presentation_groups: list[PresentationGroup]
    data_gaps: list[str] = Field(min_length=1)
    refusals: list[str] = Field(min_length=1)
    overall_confidence: Literal["high", "medium", "low"]
    summary: str = Field(min_length=80)  # plain-language answer for the clinician
    provenance_trace: list[Provenance]
    # Citations: harness-built from provenance after submission, then verified
    # with live HTTP checks. The model never writes these.
    sources: list[Source] = Field(default_factory=list)
