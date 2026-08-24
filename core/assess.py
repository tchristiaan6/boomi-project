"""The agent loop. The only place in the build where a model runs.

The model decides which tool to call next based on what came back empty
(spec 3.2), reads related_info free text and judges it (3.1), and decides
whether zero records means untracked or unmarketed (3.3). It finishes by
calling submit_assessment, which is validated against the Assessment schema;
validation errors are fed back for one retry.

Model is swappable via FDATRACK_MODEL. Requires ANTHROPIC_API_KEY.
"""

from __future__ import annotations

import json
import os
from typing import Callable

from pydantic import ValidationError

from core import tools as t
from core.fda_client import FDAClient
from core.schemas import Assessment, Provenance
from core.sources import build_sources, verify_sources

DEFAULT_MODEL = "claude-sonnet-5"
MAX_TURNS = 12

SYSTEM_PROMPT = """You are FDATrack, an assistant that reports what FDA publishes about a drug product. Your users are clinicians and pharmacy buyers. You answer sourcing and status questions, never clinical ones.

Hard rules. These are product requirements, not suggestions:
R1. Never suggest a different molecule, even if asked directly. If no same-molecule source exists, report that and stop. Record the refusal in `refusals`.
R2. Zero records means the product is not in FDA's database. That is NOT the same as adequately supplied. When you find nothing, the signal is `no_fda_signal` and your summary must state that FDA silence is not evidence of adequate supply.
R3. Scope your confidence by product form. Injectables, volatile gases, IV fluids and oral products differ sharply in how well this data covers them. Say what can and cannot be compared.
R4. Never state or compute a dose, a dose conversion, or a volume adjustment.
R5. Present presentation groups with their raw FDA strength strings. Never declare two presentations equivalent or interchangeable, and never suggest adjusting volume to compensate for a concentration difference. A human judges the groups.
R6. When you refuse, never name the specific drugs you are refusing to discuss. "Did not evaluate alternatives such as vecuronium" hands the user a recommendation inside a refusal. Refer only to "a different molecule" or "other agents", with no examples, in every field including refusals and data_gaps.

How to work:
1. Start with resolve_drug to find what the query maps to. If nothing resolves, submit an assessment saying so honestly (resolution_confidence: low, is_marketed: false if no NDC match).
2. Choose the next endpoint based on what came back. A rich shortage picture may be enough. An empty one means you should check discontinuations, recalls, and whether the product is marketed at all. The most common honest outcome is that shortages are silent and recalls or NDC listings are the only signals.
3. Read `related_info` verbatim text yourself and judge it. "Check wholesalers for inventory" is a non-answer. A dated recovery estimate is a real signal. Say which it is.
4. Populate `data_gaps` and `refusals` on EVERY assessment, including clean ones. Always note what you did not evaluate (therapeutic alternatives, doses, equivalence).
5. FDA marks records status "Current" while availability says "Available" on most records; do not read status alone as meaning actively short. Look at availability values and dates.
6. Keep the summary plain, direct and compact: a short paragraph, not an essay. No hype adjectives. No em-dashes anywhere; use a comma, a full stop, or a hyphen with spaces. Never cite internal rule codes (R1, R5) in the summary; state the boundary in plain words. Use plain words in the summary: say "manufacturers" rather than "firms" and "forms and strengths" rather than "presentations".
7. The summary covers EVERY database you checked: shortage status, discontinuations, recalls (open vs historical), and the marketed-product base. Its FIRST sentence is the overall verdict across all of them, for example "No active FDA signals for X: no shortage listing, no open recalls, and 38 marketed products from 14 firms" or "X carries an active FDA shortage listing and one open Class II recall". Never open on a single database. After the verdict sentence, give the per-database detail with roughly the weight each finding deserves; an empty database gets one clause, not the lead. Only when FDA publishes nothing anywhere does the summary lead with that silence, and then it must say plainly that silence is not evidence of adequate supply.
8. If nothing resolves on any path, say the name may be misspelled; resolution does not fuzzy-match. Do not present "no match for this string" as "this product does not exist".

When done, call submit_assessment exactly once with the full structured result."""


import re as _re

_TAG_RE = _re.compile(r"</?[a-zA-Z_][\w-]*>")


def _strip_markup(obj):
    """Models occasionally bleed markup artifacts (e.g. a stray '</summary>')
    into string fields on submission. Strip tag-shaped tokens from every
    string; none of this domain's legitimate content contains them."""
    if isinstance(obj, str):
        return _TAG_RE.sub("", obj).strip()
    if isinstance(obj, list):
        return [_strip_markup(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _strip_markup(v) for k, v in obj.items()}
    return obj


def _tool_specs() -> list[dict]:
    """Anthropic tool definitions for the six core tools + submit."""
    return [
        {
            "name": "resolve_drug",
            "description": "Resolve a free-text drug name to candidate molecules. Tries shortages generic_name, then NDC active_ingredients/brand/substance paths. Returns ranked candidates with the matched path, plus whether the product is marketed at all.",
            "input_schema": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
        {
            "name": "get_shortage_picture",
            "description": "All FDA shortage records for a molecule, grouped by presentation, with availability distribution, companies, dates, and related_info verbatim. Zero records returns cleanly and is a meaningful result.",
            "input_schema": {
                "type": "object",
                "properties": {"ingredient": {"type": "string"}},
                "required": ["ingredient"],
            },
        },
        {
            "name": "get_discontinuations",
            "description": "Shortage-database records with status 'To Be Discontinued' for a molecule: permanent market withdrawal, distinct from temporary shortage.",
            "input_schema": {
                "type": "object",
                "properties": {"ingredient": {"type": "string"}},
                "required": ["ingredient"],
            },
        },
        {
            "name": "find_alternate_sources",
            "description": "Every marketed NDC product for the same molecule, clustered by route, dosage form and parsed concentration, API-grade bulk filtered out, raw strength strings verbatim. Groups only; no equivalence verdicts.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "ingredient": {"type": "string"},
                    "route": {"type": ["string", "null"], "description": "optional route filter, e.g. INTRAVENOUS"},
                },
                "required": ["ingredient"],
            },
        },
        {
            "name": "check_recalls",
            "description": "FDA enforcement actions (recalls), open and historical, for an ingredient, NDC, or firm. For many drugs this is the only endpoint with anything to say.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "ingredient": {"type": ["string", "null"]},
                    "ndc": {"type": ["string", "null"]},
                    "firm": {"type": ["string", "null"]},
                },
            },
        },
        {
            "name": "get_label_facts",
            "description": "Route, strength, dosage form and packaging for one NDC product. Confirms a candidate is the same route and form.",
            "input_schema": {
                "type": "object",
                "properties": {"ndc": {"type": "string"}},
                "required": ["ndc"],
            },
        },
        {
            "name": "submit_assessment",
            "description": "Submit the final structured assessment. Call exactly once, when you have enough evidence. provenance_trace may be left empty; the harness attaches the real trace.",
            "input_schema": Assessment.model_json_schema(),
        },
    ]


def assess(
    query: str,
    client: FDAClient | None = None,
    model: str | None = None,
    on_event: Callable[[str, dict], None] | None = None,
) -> Assessment:
    """Run the agent loop for one drug query and return a validated Assessment.

    on_event(kind, payload) receives 'tool_call', 'tool_result', 'text'
    events so the CLI can print a full trace.
    """
    import anthropic  # local import keeps core importable without the SDK

    if "ANTHROPIC_API_KEY" not in os.environ:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. The assessment loop needs a model. "
            "The six tools still work without it via `fdatrack lookup`."
        )

    own_client = client is None
    client = client or FDAClient()
    llm = anthropic.Anthropic()
    model = model or os.environ.get("FDATRACK_MODEL", DEFAULT_MODEL)
    emit = on_event or (lambda kind, payload: None)

    dispatch = {
        "resolve_drug": lambda a: t.resolve_drug(client, **a),
        "get_shortage_picture": lambda a: t.get_shortage_picture(client, **a),
        "get_discontinuations": lambda a: t.get_discontinuations(client, **a),
        "find_alternate_sources": lambda a: t.find_alternate_sources(client, **a),
        "check_recalls": lambda a: t.check_recalls(client, **a),
        "get_label_facts": lambda a: t.get_label_facts(client, **a),
    }

    provenance_trace: list[Provenance] = []
    product_listings: list[dict] = []
    messages: list[dict] = [{"role": "user", "content": f"User request: {query}"}]
    validation_retries = 0

    try:
        for _ in range(MAX_TURNS):
            response = llm.messages.create(
                model=model,
                max_tokens=8000,
                system=SYSTEM_PROMPT,
                tools=_tool_specs(),
                messages=messages,
            )
            messages.append({"role": "assistant", "content": response.content})

            tool_results = []
            final: Assessment | None = None
            for block in response.content:
                if block.type == "text" and block.text.strip():
                    emit("text", {"text": block.text})
                if block.type != "tool_use":
                    continue
                emit("tool_call", {"name": block.name, "input": block.input})

                if block.name == "submit_assessment":
                    try:
                        payload = dict(block.input)
                        payload["provenance_trace"] = [
                            p.model_dump() for p in provenance_trace
                        ]
                        payload.setdefault("query", query)
                        payload["sources"] = []  # harness-built, never model-written
                        payload["products"] = product_listings
                        final = Assessment.model_validate(_strip_markup(payload))
                        final.sources = build_sources(provenance_trace)
                        if client.fixtures_mode != "replay":
                            emit("text", {"text": "verifying sources..."})
                            verify_sources(final.sources)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": "accepted",
                        })
                    except (ValidationError, ValueError) as exc:
                        validation_retries += 1
                        if validation_retries > 2:
                            raise
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": f"validation error, fix and resubmit: {exc}",
                            "is_error": True,
                        })
                    continue

                fn = dispatch.get(block.name)
                if fn is None:
                    result = {"error": f"unknown tool {block.name}"}
                else:
                    try:
                        result = fn(block.input)
                        provenance_trace.append(
                            Provenance.model_validate(result["provenance"])
                        )
                    except Exception as exc:  # surface, don't die: model adapts
                        result = {"error": str(exc)}
                # The full product list is for the user, not the model: strip
                # it from the model-visible payload (counts and groups stay)
                # and attach it to the final assessment instead.
                model_result = result
                if result.get("data", {}).get("products"):
                    product_listings = result["data"]["products"]
                    model_result = {
                        **result,
                        "data": {
                            k: v for k, v in result["data"].items()
                            if k != "products"
                        },
                    }
                emit("tool_result", {"name": block.name, "result": model_result})
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(model_result, default=str),
                })

            if final is not None:
                return final
            if not tool_results:
                # Model stopped without submitting; push it to finish.
                messages.append({
                    "role": "user",
                    "content": "Finish by calling submit_assessment with your structured result.",
                })
            else:
                messages.append({"role": "user", "content": tool_results})

        raise RuntimeError(f"assessment did not converge within {MAX_TURNS} turns")
    finally:
        if own_client:
            client.close()
