"""The six tool functions. Each is one complete retrieval step with the mess
already handled: pagination, grouping, bulk filtering, date reconciliation,
name-path fallbacks, and provenance on every result.

Every function returns {"data": <model dump>, "provenance": <Provenance dump>}.
No function here concludes anything clinical. Grouping is retrieval
correctness; judgment lives above the MCP boundary.
"""

from __future__ import annotations

from collections import Counter, defaultdict

from core.fda_client import FDAClient, phrase, utcnow_iso
from core.normalize import (
    contains_token,
    is_api_grade_bulk,
    parse_strength,
    presentation_key,
    to_iso_date,
)
from core.schemas import (
    AlternateSources,
    DiscontinuationRecord,
    Discontinuations,
    FilteredOut,
    LabelFacts,
    PresentationGroup,
    Provenance,
    RecallRecord,
    Recalls,
    ResolveCandidate,
    ResolvedDrug,
    ShortageGroup,
    ShortagePicture,
)

SHORTAGES = "/drug/shortages.json"
NDC = "/drug/ndc.json"
ENFORCEMENT = "/drug/enforcement.json"


def _provenance(
    client: FDAClient,
    endpoint: str,
    queries: list[str],
    total: int,
    returned: int,
    warnings: list[str] | None = None,
    filtered_out: list[FilteredOut] | None = None,
) -> Provenance:
    return Provenance(
        endpoint=(
            f"https://api.fda.gov{endpoint}"
            if endpoint.startswith("/") else endpoint
        ),
        queries=queries,
        total_matched=total,
        returned=returned,
        filtered_out=filtered_out or [],
        fetched_at=utcnow_iso(),
        warnings=warnings or [],
    )


def _envelope(data, provenance: Provenance) -> dict:
    return {"data": data.model_dump(), "provenance": provenance.model_dump()}


# ------------------------------------------------------------------ resolve

def resolve_drug(client: FDAClient, query: str) -> dict:
    """Fuzzy name -> molecule candidates. Tries paths in order and reports
    which matched. Does not pick a winner. Distinguishes 'not marketed at all'
    from 'marketed but untracked'."""
    queries: list[str] = []
    warnings: list[str] = []
    candidates: list[ResolveCandidate] = []
    paths_tried: list[str] = []
    path_names: dict[str, set[str]] = {}

    def try_path(endpoint: str, field: str, label: str, extract) -> int:
        paths_tried.append(label)
        mark = len(client.query_log)
        body = client.search(endpoint, search=phrase(field, query), limit=100)
        queries.extend(client.query_log[mark:])
        results = body.get("results", [])
        total = body.get("meta", {}).get("results", {}).get("total", 0)
        names = Counter()
        for r in results:
            for name in extract(r):
                if name:
                    names[name.strip()] += 1
        if names:
            path_names[label] = set(n.lower() for n in names)
            top, count = names.most_common(1)[0]
            exact = contains_token(top, query)
            candidates.append(
                ResolveCandidate(
                    name=top,
                    matched_path=label,
                    match_count=total,
                    confidence="high" if exact else "medium",
                )
            )
        return total

    try_path(
        SHORTAGES, "generic_name", "shortages.generic_name",
        lambda r: [r.get("generic_name")],
    )
    ndc_total = try_path(
        NDC, "active_ingredients.name", "ndc.active_ingredients.name",
        lambda r: [i.get("name") for i in r.get("active_ingredients", [])],
    )
    if ndc_total == 0:
        ndc_total = try_path(
            NDC, "brand_name", "ndc.brand_name",
            lambda r: [r.get("brand_name") or r.get("generic_name")],
        )
    if ndc_total == 0:
        ndc_total = try_path(
            NDC, "openfda.substance_name", "ndc.openfda.substance_name",
            lambda r: [i.get("name") for i in r.get("active_ingredients", [])]
            or [r.get("generic_name")],
        )

    is_marketed: bool | None
    if ndc_total > 0:
        is_marketed = True
    elif candidates:
        is_marketed = None
        warnings.append(
            "name matched shortage records but no NDC product; "
            "marketing status could not be confirmed"
        )
    else:
        is_marketed = False

    paths_agree: bool | None = None
    if len(path_names) >= 2:
        sets = list(path_names.values())
        paths_agree = any(
            a & b for i, a in enumerate(sets) for b in sets[i + 1 :]
        ) or any(
            contains_token(x, query) for s in sets for x in s
        )
        if not paths_agree:
            warnings.append(
                "resolution paths returned non-overlapping names; "
                "treat resolution as low confidence"
            )

    data = ResolvedDrug(
        query=query,
        candidates=candidates,
        is_marketed=is_marketed,
        paths_tried=paths_tried,
        paths_agree=paths_agree,
    )
    prov = _provenance(
        client, "(multiple)", queries,
        total=sum(c.match_count for c in candidates),
        returned=len(candidates), warnings=warnings,
    )
    return _envelope(data, prov)


# ---------------------------------------------------------------- shortages

def get_shortage_picture(client: FDAClient, ingredient: str) -> dict:
    """All shortage records for the molecule, grouped by presentation.
    related_info is returned verbatim, never summarized (spec 3.1).
    Zero records is the common case and returns cleanly (R2)."""
    search = phrase("generic_name", ingredient)
    mark = len(client.query_log)
    records, total, warnings = client.search_all(SHORTAGES, search)
    queries = client.query_log[mark:]

    # Guard the substring trap: keep only records whose generic_name actually
    # contains the requested token(s).
    kept = [r for r in records if contains_token(r.get("generic_name"), ingredient)]
    dropped = len(records) - len(kept)
    filtered = (
        [FilteredOut(reason="generic_name_token_mismatch", count=dropped)]
        if dropped else []
    )

    groups: dict[str, list[dict]] = defaultdict(list)
    for r in kept:
        groups[presentation_key(r.get("presentation"))].append(r)

    out_groups: list[ShortageGroup] = []
    for presentation, recs in sorted(groups.items()):
        upd = sorted(filter(None, (to_iso_date(r.get("update_date")) for r in recs)))
        post = sorted(
            filter(None, (to_iso_date(r.get("initial_posting_date")) for r in recs))
        )
        out_groups.append(
            ShortageGroup(
                presentation=presentation,
                package_ndcs=sorted(
                    {r["package_ndc"] for r in recs if r.get("package_ndc")}
                ),
                record_count=len(recs),
                statuses=dict(Counter(r.get("status", "?") for r in recs)),
                availability=dict(Counter(r.get("availability", "?") for r in recs)),
                companies=sorted({r.get("company_name", "?") for r in recs}),
                update_types=dict(Counter(r.get("update_type", "?") for r in recs)),
                shortage_reasons=sorted(
                    {r["shortage_reason"] for r in recs if r.get("shortage_reason")}
                ),
                update_date_range=[upd[0], upd[-1]] if upd else [],
                initial_posting_range=[post[0], post[-1]] if post else [],
                related_info=sorted(
                    {r["related_info"] for r in recs if r.get("related_info")}
                ),
                related_info_links=sorted(
                    {r["related_info_link"] for r in recs if r.get("related_info_link")}
                ),
            )
        )

    data = ShortagePicture(
        ingredient=ingredient,
        record_count=len(kept),
        status_totals=dict(Counter(r.get("status", "?") for r in kept)),
        groups=out_groups,
    )
    prov = _provenance(
        client, SHORTAGES, queries, total, len(kept),
        warnings=warnings, filtered_out=filtered,
    )
    return _envelope(data, prov)


# ---------------------------------------------------- discontinuations

def get_discontinuations(client: FDAClient, ingredient: str) -> dict:
    """Records FDA flags To Be Discontinued: permanent withdrawal, a bigger
    planning event than temporary constraint. 27% of the database lives here."""
    search = f'{phrase("generic_name", ingredient)}+AND+status:"To+Be+Discontinued"'
    mark = len(client.query_log)
    records, total, warnings = client.search_all(SHORTAGES, search)
    queries = client.query_log[mark:]

    kept = [r for r in records if contains_token(r.get("generic_name"), ingredient)]
    dropped = len(records) - len(kept)

    data = Discontinuations(
        ingredient=ingredient,
        record_count=len(kept),
        records=[
            DiscontinuationRecord(
                generic_name=r.get("generic_name", "?"),
                presentation=r.get("presentation", "?"),
                company=r.get("company_name", "?"),
                initial_posting_date=to_iso_date(r.get("initial_posting_date")),
                update_date=to_iso_date(r.get("update_date")),
                related_info=r.get("related_info"),
            )
            for r in kept
        ],
    )
    prov = _provenance(
        client, SHORTAGES, queries, total, len(kept), warnings=warnings,
        filtered_out=(
            [FilteredOut(reason="generic_name_token_mismatch", count=dropped)]
            if dropped else []
        ),
    )
    return _envelope(data, prov)


# ------------------------------------------------------- alternate sources

def find_alternate_sources(
    client: FDAClient, ingredient: str, route: str | None = None
) -> dict:
    """Every marketed NDC for the same molecule, clustered by
    (route, dosage form, parsed concentration). Filters API-grade bulk.
    Presents groups with verbatim strings; concludes nothing (R5, R1)."""
    search = phrase("active_ingredients.name", ingredient)
    mark = len(client.query_log)
    records, total, warnings = client.search_all(NDC, search)
    queries = client.query_log[mark:]

    kept, bulk_count, mismatch_count = [], 0, 0
    for r in records:
        names = " ; ".join(i.get("name", "") for i in r.get("active_ingredients", []))
        if not contains_token(names, ingredient):
            mismatch_count += 1
            continue
        if is_api_grade_bulk(r):
            bulk_count += 1
            continue
        kept.append(r)

    if route:
        route_l = route.lower()
        before = len(kept)
        kept = [
            r for r in kept
            if any(route_l in (rt or "").lower() for rt in r.get("route", []) or [])
        ]
        warnings.append(
            f"route filter '{route}' kept {len(kept)} of {before} products"
        )

    clusters: dict[tuple, list[dict]] = defaultdict(list)
    unparseable: set[str] = set()
    for r in kept:
        routes = ",".join(sorted(r.get("route", []) or [])) or None
        raw_strengths = [
            (i.get("strength") or "").strip()
            for i in r.get("active_ingredients", [])
            if contains_token(i.get("name"), ingredient)
        ]
        raw = " + ".join(s for s in raw_strengths if s) or None
        parsed = parse_strength(raw_strengths[0]) if raw_strengths else None
        if raw and parsed is None:
            unparseable.add(raw)
        clusters[(parsed, routes)].append(
            {"raw": raw or "(no strength string)", "record": r}
        )

    groups: list[PresentationGroup] = []
    for (parsed, routes), items in sorted(
        clusters.items(), key=lambda kv: -len(kv[1])
    ):
        ending = []
        for it in items:
            end = to_iso_date(it["record"].get("marketing_end_date"))
            if end:
                brand = it["record"].get("brand_name") or it["record"].get(
                    "generic_name", "?"
                )
                ending.append(f"{brand} (ends {end})")
        groups.append(
            PresentationGroup(
                parsed_strength=parsed,
                route=routes,
                dosage_forms=sorted(
                    {it["record"].get("dosage_form") or "?" for it in items}
                ),
                raw_strength_strings=sorted({it["raw"] for it in items}),
                product_count=len(items),
                firms=sorted(
                    {it["record"].get("labeler_name", "?") for it in items}
                ),
                marketing_ending=sorted(set(ending)),
            )
        )

    filtered = []
    if bulk_count:
        filtered.append(FilteredOut(reason="api_grade_bulk", count=bulk_count))
    if mismatch_count:
        filtered.append(
            FilteredOut(reason="ingredient_token_mismatch", count=mismatch_count)
        )

    data = AlternateSources(
        ingredient=ingredient,
        marketed_product_count=len(kept),
        groups=groups,
        unparseable_strengths=sorted(unparseable),
    )
    prov = _provenance(
        client, NDC, queries, total, len(kept),
        warnings=warnings, filtered_out=filtered,
    )
    return _envelope(data, prov)


# ---------------------------------------------------------------- recalls

def check_recalls(
    client: FDAClient,
    ingredient: str | None = None,
    ndc: str | None = None,
    firm: str | None = None,
) -> dict:
    """Enforcement actions, open and historical. Primary retrieval path for
    many drugs the shortage endpoint is silent on. Avoids naive substring
    matching: tries openfda name fields first, falls back to a quoted-phrase
    product_description search with a token-match post-filter."""
    if not any([ingredient, ndc, firm]):
        raise ValueError("check_recalls needs at least one of ingredient/ndc/firm")

    queries: list[str] = []
    warnings: list[str] = []
    records: list[dict] = []
    total = 0
    mark = len(client.query_log)

    if ingredient:
        search = (
            f'({phrase("openfda.generic_name", ingredient)}'
            f'+OR+{phrase("openfda.substance_name", ingredient)}'
            f'+OR+{phrase("openfda.brand_name", ingredient)}'
            f'+OR+{phrase("product_description", ingredient)})'
        )
        raw, total, w = client.search_all(ENFORCEMENT, search)
        warnings += w
        # Whole-word post-filter guards the substring trap (S10.11): a record
        # stays only if an openfda name field or the product description
        # actually contains the requested token(s).
        records = [
            r for r in raw
            if contains_token(r.get("product_description"), ingredient)
            or any(
                contains_token(n, ingredient)
                for f in ("generic_name", "substance_name", "brand_name")
                for n in (r.get("openfda", {}).get(f) or [])
            )
        ]
        if len(raw) != len(records):
            warnings.append(
                f"dropped {len(raw) - len(records)} phrase-search hits that "
                "did not contain the whole word in any name field"
            )
    elif ndc:
        search = (
            f'(openfda.package_ndc:"{ndc}"+OR+openfda.product_ndc:"{ndc}"'
            f'+OR+{phrase("product_description", ndc)})'
        )
        records, total, w = client.search_all(ENFORCEMENT, search)
        warnings += w
    else:
        search = phrase("recalling_firm", firm)
        records, total, w = client.search_all(ENFORCEMENT, search)
        warnings += w

    queries = client.query_log[mark:]

    def to_model(r: dict) -> RecallRecord:
        return RecallRecord(
            status=r.get("status", "?"),
            classification=r.get("classification", "?"),
            product_description=(r.get("product_description") or "")[:300],
            reason_for_recall=(r.get("reason_for_recall") or "")[:300],
            recalling_firm=r.get("recalling_firm", "?"),
            recall_initiation_date=to_iso_date(r.get("recall_initiation_date")),
            report_date=to_iso_date(r.get("report_date")),
            termination_date=to_iso_date(r.get("termination_date")),
            distribution_pattern=(r.get("distribution_pattern") or "")[:120] or None,
        )

    open_recs = [r for r in records if r.get("status") != "Terminated"]
    terminated = [r for r in records if r.get("status") == "Terminated"]
    terminated.sort(
        key=lambda r: to_iso_date(r.get("recall_initiation_date")) or "", reverse=True
    )

    data = Recalls(
        query={"ingredient": ingredient, "ndc": ndc, "firm": firm},
        record_count=len(records),
        open_count=len(open_recs),
        by_classification=dict(
            Counter(r.get("classification", "?") for r in records)
        ),
        open_recalls=[to_model(r) for r in open_recs[:50]],
        recent_terminated=[to_model(r) for r in terminated[:10]],
    )
    if len(open_recs) > 50:
        warnings.append(f"showing 50 of {len(open_recs)} open recalls")
    prov = _provenance(
        client, ENFORCEMENT, queries, total, len(records), warnings=warnings
    )
    return _envelope(data, prov)


# ------------------------------------------------------------- label facts

def get_label_facts(client: FDAClient, ndc: str) -> dict:
    """Route, strength, dosage form, packaging for one product. Confirms a
    candidate is the same route and form. Accepts product or package NDC."""
    mark = len(client.query_log)
    body = client.search(
        NDC, search=f'(product_ndc:"{ndc}"+OR+packaging.package_ndc:"{ndc}")', limit=5
    )
    queries = client.query_log[mark:]
    results = body.get("results", [])
    total = body.get("meta", {}).get("results", {}).get("total", 0)

    if not results:
        data = LabelFacts(ndc=ndc, found=False)
    else:
        r = results[0]
        data = LabelFacts(
            ndc=ndc,
            found=True,
            brand_name=r.get("brand_name"),
            generic_name=r.get("generic_name"),
            dosage_form=r.get("dosage_form"),
            route=r.get("route", []) or [],
            active_ingredients=r.get("active_ingredients", []),
            packaging=[
                p.get("description", "")
                for p in r.get("packaging", [])
            ][:10],
            marketing_start_date=to_iso_date(r.get("marketing_start_date")),
            marketing_end_date=to_iso_date(r.get("marketing_end_date")),
            product_type=r.get("product_type"),
        )
    warnings = []
    if total > 1:
        warnings.append(f"{total} products matched this NDC; returning the first")
    prov = _provenance(client, NDC, queries, total, min(total, 1), warnings=warnings)
    return _envelope(data, prov)
