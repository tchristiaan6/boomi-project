"""Citations. Turns the provenance trace into a user-facing source list and
verifies every link before it is shown.

Two kinds of source per answer:
- The exact api.fda.gov queries the tools issued (the evidence itself).
- FDA's human-browsable page for each database touched, so a non-technical
  reader has somewhere to click that is not raw JSON.

Verification re-issues each API query with limit=1 (cheap, counts as one
request) and GETs each web page. For API queries a 404 NOT_FOUND is VALID:
openFDA returns 404 for zero matches, and "zero records" is a real answer
this tool stands behind.
"""

from __future__ import annotations

import re
import time

import httpx

from core.schemas import Provenance, Source

# Human-browsable counterpart for each database the tools touch.
ENDPOINT_PAGES = {
    "/drug/shortages.json": (
        "FDA Drug Shortages (browsable)",
        "https://www.fda.gov/drugs/drug-safety-and-availability/drug-shortages",
    ),
    "/drug/ndc.json": (
        "FDA National Drug Code Directory (browsable)",
        "https://www.fda.gov/drugs/drug-approvals-and-databases/national-drug-code-directory",
    ),
    "/drug/enforcement.json": (
        "FDA Enforcement Reports / recalls (browsable)",
        "https://www.fda.gov/safety/recalls-market-withdrawals-safety-alerts",
    ),
}

_ENDPOINT_LABELS = {
    "/drug/shortages.json": "openFDA drug shortages query",
    "/drug/ndc.json": "openFDA NDC directory query",
    "/drug/enforcement.json": "openFDA enforcement (recalls) query",
}

_LIMIT_RE = re.compile(r"([?&])limit=\d+")


def build_sources(trace: list[Provenance]) -> list[Source]:
    """Deduplicated source list from a provenance trace: every actual query
    URL, plus one browsable FDA page per endpoint touched."""
    sources: list[Source] = []
    seen: set[str] = set()
    endpoints_touched: list[str] = []

    for prov in trace:
        for url in prov.queries:
            if url in seen:
                continue
            seen.add(url)
            endpoint = _endpoint_of(url)
            if endpoint and endpoint not in endpoints_touched:
                endpoints_touched.append(endpoint)
            label = _ENDPOINT_LABELS.get(endpoint, "openFDA query")
            sources.append(Source(url=url, label=label))

    for endpoint in endpoints_touched:
        if endpoint in ENDPOINT_PAGES:
            label, url = ENDPOINT_PAGES[endpoint]
            if url not in seen:
                seen.add(url)
                sources.append(Source(url=url, label=label))
    return sources


def _endpoint_of(url: str) -> str:
    m = re.search(r"api\.fda\.gov(/[a-z/]+\.json)", url)
    return m.group(1) if m else ""


def verify_sources(
    sources: list[Source], timeout: float = 10.0, throttle: float = 0.3
) -> list[Source]:
    """Check every source URL and mark it verified or not, in place.

    api.fda.gov queries are re-issued with limit=1 so the check is cheap;
    200 and 404 (zero matches) both count as a valid, working query.
    Web pages count as valid on any status below 400 after redirects."""
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        for i, src in enumerate(sources):
            if i:
                time.sleep(throttle)
            try:
                if "api.fda.gov" in src.url:
                    check_url = _LIMIT_RE.sub(r"\g<1>limit=1", src.url)
                    resp = client.get(check_url)
                    src.verified = resp.status_code in (200, 404)
                    src.note = (
                        "query valid, matches records"
                        if resp.status_code == 200
                        else "query valid, zero matching records"
                        if resp.status_code == 404
                        else f"HTTP {resp.status_code}"
                    )
                else:
                    resp = client.get(src.url)
                    src.verified = resp.status_code < 400
                    src.note = f"HTTP {resp.status_code}"
            except httpx.HTTPError as exc:
                src.verified = False
                src.note = f"unreachable: {type(exc).__name__}"
    return sources


def format_sources(sources: list[Source]) -> str:
    """Plain-text Sources block for CLI output and MCP clients."""
    if not sources:
        return "Sources: none recorded."
    lines = ["Sources:"]
    for i, s in enumerate(sources, 1):
        if s.verified is True:
            mark = "verified"
        elif s.verified is False:
            mark = "FAILED CHECK"
        else:
            mark = "not checked"
        note = f", {s.note}" if s.note else ""
        lines.append(f"  [{i}] {s.label} ({mark}{note})")
        lines.append(f"      {s.url}")
    return "\n".join(lines)
