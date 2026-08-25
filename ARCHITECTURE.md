# FDATrack - Build Specification

> **Note, added 2026-08-25.** This is the original specification, written before implementation and committed unchanged below this note for transparency about how the build was directed. The build followed it closely; where reality diverged, reality won:
>
> - **A seventh tool, `verify_sources`, was added.** Citations became a product requirement during the build: every answer ends with the exact queries issued, and every cited link is re-checked live before display.
> - **A sixth refusal rule (R6) was added** after the first adversarial run leaked alternative drug names inside a refusal: a refusal may not name the drugs it refuses to discuss.
> - **Two of Section 10's "verified ground truth" numbers were wrong.** Clozapine has 5 historical recalls, not zero; Lactated Ringer's has 18 enforcement records including an ongoing 2026 Class I, not zero. The original checks searched only the openfda name fields, which many enforcement records lack. The build's combined-field search found them; both were hand-verified against FDA's site and the evals pin the corrected numbers.
> - **Phase 3 (the monitor) shipped**, with replay fixtures proving it stays quiet on no-op days and escalates real changes, and with the state model deliberately structured for a future multi-user deployment.
> - **Phase 4 (the hosted front-end) shipped separately** on Vercel and Railway, reusing `core/` unchanged behind a thin FastAPI adapter. It is not part of this submission.
> - **The Assessment schema grew teeth**: signals, data gaps and refusals are non-empty by schema, and sources plus the marketed-product list are attached by the harness, never written by the model.
> - The two clinicians who shaped the drug list are referred to by role rather than by name in this public copy.

**Project:** Boomi Principal AI Field Architect take-home, plus a hosted build for real user feedback
**Status:** Spec for implementation. Hand to Claude Code.
**Domain:** fdatrack.com
**Revision:** 2 (supersedes rev 1 - user validation changed the thesis, see Section 1.1)

---

## 0. Read this first

This spec is written to be executed by an AI coding agent. Three rules:

1. **Section 10 (Empirical Findings) is verified ground truth.** Every claim was confirmed against the live API on 2026-08-24. Do not re-derive it. Several findings contradict what the docs imply.
2. **Section 9 (Do Not Build) is binding.** Scope discipline is a graded deliverable.
3. **Two deployment targets, one core.** See Section 8. The repo is what gets graded. The hosted site is for real user feedback. Neither may compromise the other.

---

## 1. What this is

An agent that answers one question, for clinicians and the pharmacy staff who supply them:

> **"What does FDA actually say about this product right now?"**

Not "is it short." That distinction is the entire point of this revision.

**Scope: any drug in FDA's data. Not a fixed list.** The agent takes free-text input and queries live. Anesthesia drugs appear throughout this spec because they came from the real users (1.1) and because they exercise the hardest paths, but nothing in the design restricts input to them. Verified across oncology, psychiatric, endocrine, biologic and common oral generics (Section 7). The only fixed list anywhere in the build is the Phase 3 monitor's default watchlist, which is configuration, not scope.

For any drug the user names, the agent reports which FDA signals exist:

| Signal | Meaning |
|---|---|
| `shortage_current` | An active shortage record exists |
| `discontinuation` | Product is flagged To Be Discontinued - permanent, not temporary |
| `recall_open` | An open enforcement action exists |
| `recall_historical` | Prior recalls, now terminated |
| `no_fda_signal` | **Nothing found. This is not the same as "fine."** |

**Primary users:** a practicing CRNA and a nurse practitioner. Both reviewed the drug list this build is scoped around (Section 10.7). Secondary user: the pharmacy buyer supplying an OR.

### 1.1 The thesis, and how it changed

Revision 1 assumed FDA's shortage database covered what an anesthesia provider cares about. **A real clinician was asked, and it does not.**

The CRNA named the products that actually go short in his OR. Verified against all 1,628 shortage records:

| What the CRNA named | Shortage records |
|---|---|
| **Propofol** - "the #1 drug we use" | **0** |
| Sevoflurane | **0** |
| Isoflurane | **0** |
| Lactated Ringer's | **0** |
| Cefazolin | **0** |
| Ondansetron | **0** |
| Normal saline | 22, none currently active |
| Desflurane | 1, discontinuation only |
| Insulin (the NP's addition) | 4, discontinuation only |

The string "ringer" appears **zero times in any field of any record.** So does "sevoflurane." So does "cefazolin."

**Why:** FDA's shortage database tracks *manufacturer-reported* shortages of specific NDC presentations. What the CRNA experiences is *allocation* - the distributor fills 60% of the saline order, the wholesaler backorders cefazolin. No manufacturer reports that to FDA, so it never appears. This is a structural gap, not a data quality problem.

**But the enforcement endpoint is not silent on those same drugs** (Section 10.8): sodium chloride has 1,003 recall records including 2026 Class I events; cefazolin 35; ondansetron 24; insulin 23; propofol 19.

**So the thesis is:** the honest answer for a clinician is rarely "here is your shortage." It is "here is every signal FDA publishes about this product, here is where FDA is silent, and here is why silence is not reassurance." The gap between clinical reality and public data coverage is the finding, and the README leads with it.

---

## 2. Hard boundaries

**Product requirements, not disclaimers. Each must be an explicitly tested behavior, not a prompt hope.**

The agent operates on **sourcing and status** questions, never **clinical** ones. The line is the active ingredient.

- **R1 - No cross-molecule suggestion.** If no same-molecule source is available, report that and stop. Never propose a different drug, even if pressed.
- **R2 - "Not listed" is not "not short."** Zero records means the product is not in FDA's database, which is not the same as adequately supplied. Report the former, never the latter. **Given Section 1.1, this is now the most frequently exercised rule in the build, not an edge case.**
- **R3 - Scope confidence by product form.** Reliability differs sharply across injectables, volatile gases, IV fluids and oral products. Say what can and cannot be compared rather than comparing badly.
- **R4 - Never state or compute a dose.**
- **R5 - Surface presentation groups, never an equivalence verdict.** When presentations of the same molecule differ, the agent **presents the groups and their raw FDA strings for the human to judge.** It does not conclude "these are interchangeable," and it does not suggest adjusting volume to compensate for a concentration difference. That is a pharmacy and workflow control. See Section 6.

### Why the boundaries are defensible

**FDA publishes no therapeutic alternatives in this API.** The shortage schema is 16 fields (Section 10.4). No alternatives field, no substitution guidance, no therapeutic equivalent. Of 353 current anesthesia records, **6** carry a `related_info_link` to an FDA document. The `related_info` free text is logistics, not clinical guidance:

- `"Check wholesalers for inventory"`
- `"Additional product will be made available as it is released."`
- `"Historical NDC: 0409-1582-29"`

Any therapeutic substitution would be **invented, not sourced.** The refusal is honest, not merely cautious.

---

## 3. Why an agent

Five places a decision tree breaks. The README names these explicitly.

**3.1 - Free text carries the signal, at varying confidence.**
`"Next Delivery: August 2026; Estimated Recovery: September 2026"` and `"Check wholesalers for inventory"` are different situations. `related_info` is populated on 58% of records and no parser reduces it to a flag. A model reads it and judges whether a recovery claim is credible.

**3.2 - The right endpoint is not knowable in advance.**
This is new in rev 2 and it is the strongest justification. Ask about rocuronium and the shortage endpoint answers richly. Ask about propofol and it is empty, so the question becomes whether enforcement has anything, and whether the product is even marketed. **The agent must decide where to look next based on what came back empty.** That is not a fixed pipeline.

**3.3 - Absence is ambiguous.**
Zero shortage records: adequately supplied, or untracked? Not derivable from the response. Requires a judgment call plus a check that the product exists at all in `/drug/ndc`.

**3.4 - Name resolution is fuzzy and fails per-drug.**
Shortages say `"Rocuronium Bromide Injection"`. NDC says `"ROCURONIUM BROMIDE"`. `openfda.unii`, the clean join key, **is not searchable on the NDC endpoint** (Section 10.9). Resolution means trying paths and judging which results are real.

**3.5 - One ingredient name spans unrelated products.**
Epinephrine covers homeopathic `6 [hp_X]/mL` dilutions, EpiPens, code-cart ampules and 20 mg/mL topical - a 200-fold span among IV forms alone (Section 10.10). Deciding which cluster the user meant requires reading context, not matching strings.

**The honest counter, which belongs in the README:** detection and delivery are *not* agentic and should not pretend to be. See 4.2.

---

## 4. Architecture

### 4.1 The core split

```
+----------------------------------------------------------+
|  CLIENTS                                                 |
|  Claude Desktop / Claude Code   (graded submission)      |
|  fdatrack.com                   (clinician feedback)     |
|  scheduled monitor              (phase 3)                |
+-------------------------------+--------------------------+
                                |
                        === JUDGMENT ===
      Which endpoint do I try next, given this came back empty?
      Is "check wholesalers" reassurance or a non-answer?
      Does zero records mean fine, or untracked?
      Which product cluster did they actually mean?
                                |
+-------------------------------+--------------------------+
|  MCP SERVER   (6 tools, Section 5)                       |
|                                                          |
|    resolve_drug()            fuzzy name -> molecule      |
|    get_shortage_picture()    all records, grouped        |
|    get_discontinuations()    To Be Discontinued          |
|    find_alternate_sources()  same-molecule NDCs          |
|    check_recalls()           enforcement actions         |
|    get_label_facts()         per-product specifics       |
|                                                          |
|  === MECHANISM, all deterministic ===                    |
|    paginate past 1000 / 25000 caps                       |
|    group 50mg/5mL == 100mg/10mL == 10mg/mL               |
|    filter API-grade bulk (1 kg/kg, 25 kg/25kg)           |
|    reconcile MM/DD/YYYY vs YYYYMMDD                      |
|    retry name paths: generic -> ingredient -> brand      |
|    attach provenance to every result                     |
+-------------------------------+--------------------------+
                                |
                          api.fda.gov
```

**The MCP boundary is the boundary between mechanism and judgment.** Everything deterministic below. Everything requiring a call above.

This is the architectural claim to defend in the working session. The failure mode to avoid is a thin one-tool-per-endpoint wrapper, which pushes every rate limit, pagination cap and unit mismatch into the model's context to rediscover on every run, unverifiably and differently each time.

### 4.2 Three layers, only one agentic

| Layer | Implementation | Rationale |
|---|---|---|
| **Detect** | Plain code. Snapshot, diff, drop `update_type: Reverified` no-ops. | This *is* a cron job. No model. 77% of record activity is a non-event (10.5). |
| **Assess** | Agent loop over the MCP tools. | The tree breaks here. This is the build. |
| **Deliver** | Plain code. If material, emit. | Also a cron job. Also fine. |

State this in the README. *"I used an agent where judgment was required and plain code everywhere else"* beats instrumenting all three.

### 4.3 Repo layout - transport-agnostic core

**Load-bearing.** This is what lets one codebase serve both the graded repo and fdatrack.com. `core/` must not import MCP or HTTP anything.

```
core/                     # pure library. no MCP, no HTTP, no framework
  fda_client.py           # rate limiting, pagination, retries, provenance
  normalize.py            # strength parsing, dates, names, bulk-API detection
  tools.py                # the 6 tool fns -> (data, provenance)
  assess.py               # the agent loop (calls an LLM)
  schemas.py              # pydantic models
servers/
  mcp_stdio.py            # THIN adapter: core.tools as MCP tools    [graded]
  api.py                  # THIN adapter: FastAPI over core.assess   [fdatrack]
cli/
  main.py                 # assess one drug, print full trace
web/                      # Next.js, deploys to Vercel               [phase 4]
monitor/
  snapshot.py             # persist watchlist state to JSON
  diff.py                 # compare snapshots, filter no-ops
  run.py                  # detect -> core.assess -> emit
evals/
  cases.yaml              # golden cases, independently verified
  run_evals.py
fixtures/                 # committed API responses for offline replay
```

**Language: Python** for `core/`, `servers/`, `cli/`, `monitor/`. Data munging, mature MCP SDK, pydantic gives the schemas free. **TypeScript / Next.js** for `web/` only, talking to `servers/api.py` over HTTP.

---

## 5. MCP tool surface

**Granularity rule:** each tool is *one complete retrieval step with the mess already handled.* The model chooses which to call next and interprets results.

- Too fine (one per endpoint): the model does plumbing, not thinking.
- Too coarse (`assess_drug()` does everything): the server reasons, there is no agent, nothing is verifiable.

### 5.1 Universal return envelope

```python
{
  "data": <tool-specific>,
  "provenance": {
    "endpoint": "https://api.fda.gov/drug/shortages.json",
    "queries": ["<each actual query string issued>"],
    "total_matched": 26,
    "returned": 26,
    "filtered_out": [{"reason": "api_grade_bulk", "count": 12}],
    "fetched_at": "2026-08-24T18:22:04Z",
    "warnings": ["openfda.unii not searchable on /drug/ndc; fell back to active_ingredients.name"]
  }
}
```

**Provenance is a first-class feature, not logging.** The model reasons about its own confidence, the evidence deliverable comes nearly free, and "how do you know it's right" is answered with a trace rather than a claim. It is also what the fdatrack.com UI renders as "show your work."

### 5.2 The tools

**`resolve_drug(query: str) -> ResolvedDrug`**
Handles 3.4. Tries in order: shortages `generic_name`, NDC `active_ingredients.name`, NDC `brand_name`, openfda `substance_name`. Returns ranked candidates with matched path and confidence. **Does not pick.** Must handle paths disagreeing, and must report when a product is not marketed at all versus merely untracked.

**`get_shortage_picture(ingredient: str) -> ShortagePicture`**
All shortage records for the molecule, grouped by presentation. Per group: availability distribution, companies, `update_date` range, `update_type`, `shortage_reason`, and **`related_info` verbatim, never summarized by the server** - that is the model's call per 3.1. Rocuronium is the shape test: 26 records, 11 firms, 3 presentations, mixed availability. **Must return cleanly and informatively on zero results**, which is the common case (1.1).

**`get_discontinuations(ingredient: str) -> Discontinuations`**
Records with `status: "To Be Discontinued"`. **441 of 1,628 records, 27% of the database** (10.5), and where succinylcholine, desflurane and insulin actually live. For a clinician, permanent withdrawal is a bigger planning event than temporary constraint. Rev 1 ignored this bucket entirely.

**`find_alternate_sources(ingredient: str, route: str | None) -> AlternateSources`**
Every marketed NDC for the same molecule. **Must filter API-grade bulk** (10.10) and flag `marketing_end_date`.

**Per R5, this tool groups and presents. It does not conclude.** Returns presentations clustered by parsed strength, **each cluster carrying its raw FDA strings verbatim**, so the human sees that `10 mg/mL`, `50 mg/5mL` and `100 mg/10mL` are one cluster of 68 products rather than three groups of 49, 11 and 8. It returns **no `is_equivalent` boolean and no substitution recommendation.** Where strength strings are unparseable, say so rather than guessing.

**`check_recalls(ingredient: str | None, ndc: str | None, firm: str | None) -> Recalls`**
Enforcement actions, open and historical. **Promoted in rev 2 from a cross-check to a primary retrieval path**, because for many drugs it is the only endpoint with anything to say (1.1). Must **not** rely on naive substring matching (10.11). Returns classification, status, reason, dates normalized to ISO, and distinguishes open from terminated.

**`get_label_facts(ndc: str) -> LabelFacts`**
Route, strength, dosage form, packaging for one product. Confirms a candidate is the same route and form.

**Not MCP tools:** snapshot and diff. Plain code in `monitor/`, must not appear on the tool surface.

---

## 6. Assessment output

```python
class Assessment(BaseModel):
    query: str
    resolved_to: str | None
    resolution_confidence: Literal["high", "medium", "low"]
    is_marketed: bool | None          # distinguishes "untracked" from "does not exist"

    signals: list[Signal]             # see Section 1. EMPTY IS A VALID, COMMON RESULT.
    presentation_groups: list[PresentationGroup]   # R5: grouped, not judged
    data_gaps: list[str]              # explicit "the data does not say"
    refusals: list[str]               # e.g. "did not evaluate therapeutic alternatives (R1)"
    overall_confidence: Literal["high", "medium", "low"]
    provenance_trace: list[Provenance]

class Signal(BaseModel):
    kind: Literal["shortage_current", "discontinuation",
                  "recall_open", "recall_historical", "no_fda_signal"]
    detail: str
    source_endpoint: str

class PresentationGroup(BaseModel):
    parsed_strength: str | None       # None when unparseable - say so, do not guess
    raw_strength_strings: list[str]   # verbatim FDA values, always shown
    product_count: int
    firms: list[str]
    # deliberately NO is_equivalent field. R5.
```

`data_gaps` and `refusals` are **populated on every run, including successful ones.** An assessment that never says what it did not look at is the failure mode this build exists to avoid.

When `signals` contains only `no_fda_signal`, the prose must state that FDA publishes nothing about this product **and that this is not evidence of adequate supply** (R2).

---

## 7. Build phases

Review between each. If a phase does not land, the previous one still ships clean.

### Phase 1 - Engine (~3-4h) - REQUIRED, GRADED
`core/` + `servers/mcp_stdio.py` + `cli/`. All six tools with real normalization and provenance. Agent loop producing `Assessment`. Verified working in Claude Desktop.

### Phase 2 - Evidence (~2h) - REQUIRED, GRADED
Their most heavily weighted deliverable. Non-negotiable.

- **Fixtures:** committed real API responses, offline replay.

**The tool accepts any drug. The cases below are chosen because they exercise different code paths, not because they define scope.** The anesthesia drugs came from the two clinicians and drove the design (1.1); the generality cases exist because a reviewer will type in something random and the eval set must not read as anesthesia-only.

- **Golden cases**, each hand-verified against FDA's own site:

| Case | Tests |
|---|---|
| `rocuronium` | full loop, 26 records, 3 presentations, 11 firms |
| `propofol` | R2. Zero shortage records, 19 recalls, 49 marketed NDCs |
| `lactated ringers` | R2 extreme. Zero mentions anywhere in the database |
| `succinylcholine` | discontinuation path |
| `epinephrine` | 3.5 / R5. Homeopathic through 20 mg/mL topical in one result set |
| `sodium chloride` | recall-dominant. 1,003 enforcement records, no active shortage |
| `penicillin g benzathine` | 3.1. Real `related_info_link` |
| nonsense string | graceful resolution failure |
| **`clozapine`** | **generality.** 61 marketed NDCs, **zero shortage, zero recalls.** Purest `no_fda_signal` case: `is_marketed: true` with nothing else to say. Must not read as an error or a lookup failure. |
| **`levothyroxine`** | **generality + volume.** **173 recalls**, zero shortages, 700 NDC products. Stress-tests recall handling and presentation grouping at scale. |
| **`carboplatin`** | **generality.** Oncology shortage, 35 records + 8 recalls. Proves this is not an anesthesia tool wearing a general-purpose label. |

Verified 2026-08-24: all three generality cases resolve on the first path (`active_ingredients.name`), no fallback needed. Signal mix across a wider spread, for reference when writing assertions: methotrexate 21 shortage / 14 recall; adalimumab 7 / 1; semaglutide 3 / 43; hydrochlorothiazide 13 / 0; metformin 0 / 91; albuterol 0 / 42; amoxicillin 0 / 20; warfarin 0 / 9; sertraline 0 / 7.

- **Refusal tests:** adversarial prompts attempting to elicit therapeutic substitution (R1), a dose (R4), or an equivalence verdict (R5). Must refuse. **These are the tests to show them.**
- **Known-failures section in the README.** Honest accounting is graded in your favor.

### Phase 3 - Monitor (~1-2h) - IF TIME
`monitor/`. Snapshot to JSON, diff, drop Reverified no-ops, call `core.assess` on real changes, emit to stdout or file. **No email, no SMS, no database.**

A correct monitor is mostly silent, which is unshowable. That is what replay mode is for: show it correctly staying quiet on a no-op *and* correctly escalating on a real change, both from committed fixtures.

**If Phase 3 does not happen:** ship 1 and 2, describe the monitor in the README's "what I'd do next." Naming what you cut is a question they explicitly ask.

### Phase 4 - fdatrack.com (~3-4h) - NOT GRADED, SEE SECTION 8
Do not start until Phases 1 and 2 are locked.

---

## 8. Two deployment targets

**The repo is the graded artifact. fdatrack.com is for real user feedback. Neither may compromise the other.**

| | Graded submission | fdatrack.com |
|---|---|---|
| Audience | Boomi reviewers | The two clinicians, colleagues |
| Interface | MCP server + CLI | Single input box, results page |
| Deploy | They clone and run locally | Vercel + Railway |
| Auth | None needed | None. Not collecting anything. |
| Purpose | Demonstrate judgment | Get clinician reactions |

### 8.1 Sequencing, and the honest-hours problem

Phases 1 and 2 are roughly 5-6 hours. The assignment budgets 4-8. **Phase 4 is an additional 3-4 hours that must not be reported as part of the submitted build.**

Report hours split, explicitly: *"N hours on the submitted build. M additional hours on a hosted front-end for clinician testing, which is not part of this submission."* They calibrate on hours and they reward honest accounting. Blending them looks like a 12-hour answer to an 8-hour brief.

**If real feedback arrives from either clinician before the working session, it is worth more than any feature.** "I put this in front of two clinicians and here is what they said" is the strongest thing you can bring. That is the argument for building Phase 4 early. It is not an argument for building it before Phase 2.

### 8.2 fdatrack.com scope

**Not a chat app.** An input box, a results page, and a "show your work" panel rendering the provenance trace. No session state, no conversation history, no accounts.

- Next.js on Vercel, `servers/api.py` on Railway. `core/` untouched.
- **API key mandatory, server-side only.** openFDA allows 240 req/min per IP and 1,000/day without a key, 120,000/day with one. One assessment is 6-15 calls. Without a key a shared server IP supports roughly 70-150 assessments/day **across all users combined.** With a key and a handful of clinicians testing, headroom is ample - the constraint only bites at real scale.
- Cache assessments by normalized drug name, ~6h TTL. The underlying data updates daily at best.
- **The R1-R5 refusals become load-bearing rather than merely correct.** A public URL answering drug questions is not the same risk surface as a repo one clinician runs. Needs a visible disclaimer that this is FDA public data, not clinical guidance, and that FDA silence is not evidence of supply.
- Locally-run MCP sidesteps the rate limit entirely since every user is their own IP. Worth stating in the README as a real advantage of the submitted design.

---

## 9. Do not build

Binding. Scope judgment is graded.

- No web front-end **until Phases 1 and 2 are locked**. Then Phase 4 only.
- No database. JSON snapshots on disk, plus a cache for the hosted version.
- No auth, no user accounts, no analytics, no data collection.
- No email, SMS, or Slack delivery. Stdout and files.
- **No therapeutic alternative logic, ever.** (R1)
- **No dose calculation, ever.** (R4)
- **No equivalence verdicts.** Group and present. (R5)
- No device, food, or veterinary endpoints. Drugs only.
- No FAERS adverse-event analysis. 20.7M records, a different problem.
- No batch or formulary mode in Phase 1. Single drug only.
- No fine-tuning, no vector DB, no RAG. The data is small and structured.

---

## 10. Empirical findings - verified 2026-08-24

**Ground truth. Do not re-derive.**

### 10.1 Endpoint inventory (drug only)

| Endpoint | Records | Export |
|---|---|---|
| `/drug/event` | 20,692,690 | 2026-08-17 |
| `/drug/label` | 262,032 | 2026-08-22 |
| `/drug/ndc` | 137,198 | 2026-08-22 |
| `/drug/orangebook` | 48,664 | 2026-08-22 |
| `/drug/drugsfda` | 29,277 | 2026-08-22 |
| `/drug/enforcement` | 17,876 | 2026-08-19 |
| **`/drug/shortages`** | **1,628** | 2026-08-22 |

Endpoint is **`/drug/shortages.json`**, plural. Inventory at `https://api.fda.gov/download.json`.

### 10.2 Hard limits
- `limit` max **1000**. `limit=1001` returns **`API_KEY_MISSING`**, a misleading error that will send a naive retry loop chasing credentials. Handle explicitly.
- `skip` max **25000**. Above: `BAD_REQUEST`, *"Skip value must 25000 or less."*
- Rate: **240 req/min per IP. 1,000/day without a key. 120,000/day with.** Free key at https://open.fda.gov/apis/authentication/

### 10.3 `count` support is inconsistent
- **Not supported on `/drug/shortages`.** `count=status.exact` returns `{"error":{"code":"NOT_FOUND","message":"Nothing to count"}}`. All shortage aggregation must be client-side over paginated results.
- Works on `/drug/enforcement`, `/drug/event`, `/drug/ndc`, `/food/event`.

### 10.4 Shortage record schema (non-empty per 300 records)
```
300  presentation, update_type, initial_posting_date, package_ndc,
     generic_name, company_name, contact_info, availability,
     update_date, therapeutic_category, status, dosage_form
278  openfda            <- unii, rxcui, spl_set_id, application_number
173  related_info       <- free text, the judgment surface (3.1)
115  shortage_reason
  9  related_info_link  <- FDA guidance doc, only 3% of records
  1  change_date        <- effectively useless
```
**No alternatives field exists.** Empirical basis for Section 2.

### 10.5 Status and update_type
Full database, all 1,628 records:
```
1177  Current
 441  To Be Discontinued   <- 27%. Rev 1 ignored this entirely. See get_discontinuations().
  10  Resolved             <- FDA essentially never marks anything resolved
```
`update_type` per 1,000 current:
```
774  Reverified   <- FDA looked, nothing changed. DROP THESE.
214  Revised
 12  New
```
**`change_date` is populated in 2 of 1,000 records.** The API gives you no diff. Change detection requires snapshotting and diffing yourself. `update_date` says when FDA last touched the record, not what changed.

### 10.6 `availability` values (per 1,000 current), and the status trap
```
656  Available          <- co-occurring with status:"Current"
244  Unavailable
 98  Limited Availability
  1  "Next Delivery and Estimated Recovery: December 2028"   <- free text in an enum field
  1  "Unvailable"       <- FDA's own typo. Real value, real record.
```
**`status: "Current"` co-occurs with `availability: "Available"` on 656 of 1,000 records**, so the status flag alone is nearly meaningless. Alerting on status flips produces constant noise.

`shortage_reason` per 1,000: 650 absent, 117 Other, 84 Demand increase, 58 Discontinuation, 46 Active ingredient shortage, 23 Shipping delay, 17 GMP compliance, 4 Inactive ingredient, 1 Regulatory delay.

### 10.7 Coverage gap - the central finding

**Anesthesia is the largest therapeutic category in current shortages: 353 of 1,177 current records, 30%.** (Rev 1 said "297 of 1,000 sampled." That was a sample. Use 353/1,177.)

But `therapeutic_category` is **not a reliable filter.** Fentanyl, hydromorphone and remifentanil are core OR drugs filed under **Analgesia/Addiction**, not Anesthesia. Dexamethasone is under Dermatology/Endocrinology. Filtering on `therapeutic_category:"Anesthesia"` silently drops fentanyl.

Verified against the clinicians' list, all statuses, plus a raw substring scan of every field of all 1,628 records:

| Drug | Shortage records | Notes |
|---|---|---|
| propofol | **0** | "the #1 drug we use". 49 marketed NDCs, 19 recalls |
| sevoflurane | **0** | zero mentions anywhere. 21 marketed NDCs, 3 recalls |
| isoflurane | **0** | zero mentions anywhere |
| lactated ringers | **0** | "ringer" appears 0 times in 1,628 records |
| cefazolin | **0** | zero mentions. 51 marketed NDCs, 35 recalls |
| ondansetron | **0** | 24 recalls incl. 2026 |
| phenylephrine / ephedrine | **0** | |
| sugammadex / ketamine | **0** | |
| neostigmine / glycopyrrolate | **0** | |
| cisatracurium / vecuronium | **0** | |
| sodium chloride | 22 | none Current |
| desflurane | 1 | To Be Discontinued |
| insulin | 4 | all To Be Discontinued |
| succinylcholine | 1 | To Be Discontinued |
| **lidocaine** | **89** | Anesthesia |
| **bupivacaine** | **76** | Anesthesia |
| **dexmedetomidine** | **56** | Anesthesia |
| **rocuronium** | **26** | Anesthesia |
| **midazolam** | **26** | Anesthesia |
| **hydromorphone** | **26** | Analgesia/Addiction |
| **fentanyl** | **19** | Analgesia/Addiction |
| **atropine** | **19** | Anesthesia |
| **etomidate** | **18** | Anesthesia |

**Eleven of twenty-two core anesthesia drugs return zero shortage records.**

### 10.8 Enforcement covers what shortages misses

| Drug | Recall records | Notes |
|---|---|---|
| sodium chloride | **1,003** | 2026 activity, 11 Class I in sample |
| cefazolin | 35 | 3 Class I |
| ondansetron | 24 | 2026 activity |
| insulin | 23 | 14 Class I |
| propofol | 19 | 4 Class I |
| sevoflurane | 3 | |
| desflurane | 1 | |
| lactated ringers | 0 | genuinely absent from both |

2026 drug recalls overall: **504.** Class II 440, Class I 21, Class III 42, Not Yet Classified 1.

### 10.9 Join keys - one works, one does not
- **Works:** shortage `package_ndc` -> `/drug/ndc` `packaging.package_ndc`. Verified.
- **Fails:** **`openfda.unii` is NOT searchable on `/drug/ndc`.** Returns `NOT_FOUND` for a valid UNII. Fall back to `active_ingredients.name`. Rocuronium UNII: `I65MW4OFHZ`.
- Name mismatch: shortages `"Rocuronium Bromide Injection"` vs NDC `"ROCURONIUM BROMIDE"`.

### 10.10 Strength strings - a grouping problem, not a math problem

FDA does not normalize `strength`. **Rocuronium**, 80 products:
```
 49   10 mg/mL       -+
 11   50 mg/5mL       |-- one cluster, 68 products, same concentration
  8   100 mg/10mL    -+

  8   1 kg/kg        -+
  2   25 kg/25kg      |-- API-grade bulk. NOT usable product. Filter out.
  1   100 kg/100kg    |
  1   1 g/g          -+
```
Without grouping, an agent asked "who else makes this" answers **8 sources** when the correct answer is **68.** That is the reason this matters - retrieval correctness, not clinical arithmetic.

**Epinephrine**, 200 products, shows the opposite failure - one ingredient name spanning unrelated products:
```
  6 [hp_X]/mL     homeopathic dilution
  0.005 mg/mL     dilute topical / ophthalmic
  0.1 mg/mL       IV push cardiac syringe
  1 mg/mL         code cart ampule, IM anaphylaxis
  0.3 mg/0.3mL    auto-injector
  20 mg/mL        topical / dental
```
A 200-fold span among IV forms alone. Similar in **phenylephrine** (`5 mg/1` oral tablet, `5 mg/15mL` oral liquid, `10 mg/mL` concentrated IV vial) and **heparin** (`1000`, `5000`, `10000`, `20000 [USP'U]/mL`, plus `200 [USP'U]/100mL` premixed bags).

**Per R5: group them, show the raw strings, let the human judge.** Do not emit an equivalence verdict.

### 10.11 Naive text search is a trap
`search=product_description:"dog"` on food enforcement returns 42 hits, top results **hot dog buns**. Substring matching produces confident garbage. Applies directly to `check_recalls`.

### 10.12 Dates - two formats in one API
- `/drug/shortages`: `MM/DD/YYYY` -> `"08/17/2026"`
- `/drug/enforcement`: `YYYYMMDD` -> `"20161102"`
- Range syntax: `search=report_date:[20260101+TO+20261231]`. Do **not** blanket-urlencode. The `+` and brackets are significant.

---

## 11. Prior art - know it before you are asked

At least six openFDA MCP servers exist publicly, including [Certus](https://github.com/aditya-damerla128/Certus) (shortages + recalls + labeling), [cyanheads/openfda-mcp-server](https://github.com/cyanheads/openfda-mcp-server) and [openpharma-org/fda-mcp](https://github.com/openpharma-org/fda-mcp).

**A thin MCP wrapper over openFDA is a commodity.** Assume the reviewer knows. The differentiator is the coverage-gap finding (1.1), the mechanism/judgment split (4.1), the refusal boundaries (Section 2), and the provenance trace (5.1). Not the fact of having built an MCP server.

Address it directly in the README rather than waiting to be asked.

---

## 12. README requirements

They grade the story as heavily as the build.

1. **Who this is for.** A practicing CRNA and a nurse practitioner. Both reviewed the drug list.
2. **Lead with the gap.** *"I built this for a nurse anesthetist. The first thing it told me was that FDA does not track most of what he cares about."* Section 1.1 with the table.
3. **What it does for them.** Every FDA signal on a product, and honest silence where there is none.
4. **Why it needed to be agentic.** The five points in Section 3, especially 3.2 - the right endpoint is not knowable in advance. Include the honest counter: detect and deliver are plain code.
5. **The boundaries and why they are honest.** R1-R5 with the evidence that FDA publishes no alternatives.
6. **What I deliberately cut.** Section 9.
7. **What still fails.** The known-failures list. Graded in your favor.
8. **AI-usage notes**, half a page. Where the model helped, where it led you wrong, how you caught it. **Include the rev-1 concentration-normalization overclaim**: the model framed presentation grouping as a clinical safety feature, the user pushed back correctly, and it was reframed as a retrieval-correctness requirement with an added refusal (R5). That is a clean documented instance of catching a wrong model claim.
9. **Hours, split honestly.** Submitted build versus hosted front-end. See 8.1.

**One framing worth a sentence:** Boomi is an integration company. A reusable tool surface that absorbs the ugliness of a messy public API so consumers do not have to is literally their business model.

---

## 13. Open items

- [ ] **Alert threshold.** What the CRNA actually wants to be told versus what he would ignore. Needed for Phase 3.
- [ ] **openFDA API key.** Free, instant, required. https://open.fda.gov/apis/authentication/
- [ ] **Model choice for the assess loop.** Swappable via env var. Do not hardcode.
- [ ] **fdatrack.com DNS + Vercel/Railway setup.** Phase 4 only.
- [ ] **Disclaimer copy for the hosted site.** FDA public data, not clinical guidance, silence is not evidence of supply.
