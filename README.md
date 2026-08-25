# FDATrack

An agent that answers one question for clinicians and the pharmacy staff who supply them:

> **"What does FDA actually say about this product right now?"**

That turns out to be a different question from "is it short," and the difference is the whole project.

## Who this is for

My brother is a CRNA. When a drug he needs is constrained, he finds out from his pharmacy or from an empty shelf. My sister-in-law is a nurse practitioner; she is the reason insulins are in scope. Both reviewed the drug list this build is tested against. The secondary user is the pharmacy buyer supplying an OR.

## The finding this is built around

I started with a substitution tool, then a shortage monitor. Both died on contact with the data. What killed the monitor: I asked my brother what actually goes short in his OR. He said the simplest things - normal saline, lactated ringers, the volatile gases, antibiotics, and propofol, which he called the number one drug they use. My sister-in-law added insulins.

I checked every one of those against all 1,628 records in FDA's shortage database (2026-08-24):

| What they named | Shortage records |
|---|---|
| Propofol - "the #1 drug we use" | **0** |
| Sevoflurane | **0** |
| Isoflurane | **0** |
| Lactated Ringer's | **0** - "ringer" appears nowhere in the database |
| Cefazolin | **0** |
| Ondansetron | **0** |
| Normal saline | 22, none currently active |
| Insulin | 4, all discontinuations |

Eleven of twenty-two core anesthesia drugs return zero shortage records. The reason is structural: FDA's shortage database tracks manufacturer-reported shortages of specific NDC presentations. What he experiences is allocation - the distributor fills 60% of the saline order, the wholesaler backorders the cefazolin. Nobody reports that to FDA, so it never appears.

But FDA is not silent about these drugs, it is silent about them *in that one database*. The enforcement endpoint tells a different story. Lactated Ringer's has zero shortage mentions and 18 recall records, including an **ongoing Class I recall initiated April 2026 on B. Braun 1000 mL bags** - the exact product he can't get. Propofol has 19 recalls, 4 of them Class I. Sodium chloride has over 1,000.

So the tool reports every signal FDA publishes about a product, across endpoints, and when FDA has nothing it says so plainly and says what that silence does and does not mean. For the drugs a clinician cares about most, "FDA does not track this" is usually the honest answer, and this tool treats that as a finding, not a failure.

## What it does

For any drug you name - free text, not a fixed list - it reports which of these signals exist:

| Signal | Meaning |
|---|---|
| `shortage_current` | An active shortage record exists |
| `discontinuation` | Flagged To Be Discontinued: permanent, not temporary |
| `recall_open` | An open enforcement action exists |
| `recall_historical` | Prior recalls, now terminated |
| `no_fda_signal` | Nothing found. **This is not the same as "fine."** |

Every result carries a provenance block: the exact queries issued, totals matched, what was filtered out and why, and warnings. "How do you know it's right" is answered with a trace, not a claim.

## Quick start

Requires Python 3.11+.

```bash
git clone https://github.com/tchristiaan6/boomi-project.git
cd boomi-project
python3 -m venv .venv && .venv/bin/pip install -e .
```

Run the eval suite first. It replays committed API fixtures, so it is deterministic and needs no key of any kind:

```bash
.venv/bin/python -m evals.run_evals
```

Look up any drug directly, no model needed (live API, no key required):

```bash
.venv/bin/fdatrack lookup shortage rocuronium
.venv/bin/fdatrack lookup recalls "lactated ringers"
.venv/bin/fdatrack lookup alternates propofol
```

Full agent assessment (copy `.env.example` to `.env` and add an `ANTHROPIC_API_KEY`; model swappable via `FDATRACK_MODEL`):

```bash
.venv/bin/fdatrack assess "propofol"
```

An openFDA key is optional (`FDA_API_KEY` in `.env`, free at https://open.fda.gov/apis/authentication/). Without one you get 1,000 requests/day per IP, which covers dozens of assessments.

### MCP server (Claude Desktop / Claude Code)

```json
{
  "mcpServers": {
    "fdatrack": {
      "command": "/absolute/path/to/boomi-project/.venv/bin/python",
      "args": ["-m", "servers.mcp_stdio"]
    }
  }
}
```

Because the server runs on your machine, every user is their own IP against openFDA's rate limits. That is a real advantage of local MCP over a hosted API for this data.

The server exposes the six retrieval tools plus `verify_sources`, a utility the client calls to confirm every cited link is live before showing it to the user.

## The messy parts: why this data is harder than it looks

Everything below is a real behavior of the openFDA APIs, verified against the live service. Together they are why "just call the FDA API" is not a plan.

1. **The shortage flag does not mean what it says.** Two thirds of records marked as a "Current" shortage simultaneously say the product is "Available." Alerting on the status flag alone would page people constantly about nothing.
2. **Silence is ambiguous, and silence is the common case.** Zero records for a drug can mean supply is fine, or that nobody reports the problem to FDA. The database cannot tell you which. Propofol, the most-used drug in an OR, has zero shortage records ever.
3. **Fields that should be codes contain sentences.** The "availability" field, supposedly a status code, contains free text like "Next Delivery and Estimated Recovery: December 2028." One record's status is "Unvailable" - FDA's own typo, live in production.
4. **The same drug has different names in different databases.** Shortages say "Rocuronium Bromide Injection," the product directory says "ROCURONIUM BROMIDE." The one clean identifier that links them exists in the data but cannot be searched on the product directory. Matching is guesswork that has to be checked.
5. **Two date formats in one API.** One database writes 08/17/2026, another writes 20161102. Compare them naively and every date calculation is wrong.
6. **There is no way to ask "what changed?"** The field meant to track changes is filled in on 2 of every 1,000 records, and 77% of daily updates are FDA saying it looked and nothing changed. Anyone who wants change detection has to store snapshots and compare them.
7. **Strength labels are not standardized.** 10 mg/mL, 50 mg/5 mL and 100 mg/10 mL are the same concentration written three ways. Count them naively and you tell a buyer there are 8 makers of rocuronium when the true answer is 68.
8. **Factory chemicals are listed next to hospital vials.** The product directory mixes bulk drums of raw ingredient ("1 kg/kg") in with finished products. A source count that includes them is wrong and looks fine.
9. **Search takes your words too literally.** Searching enforcement records for "dog" returns hot dog buns. Many recall records also lack structured name fields entirely, so a careful search has to combine fields and then re-check every hit. Two numbers in my own working notes were wrong because an earlier check searched too narrowly.
10. **Even the error messages mislead.** Ask for 1,001 records and the API answers "API key missing." It is not missing; the page size cap is 1,000. A naive retry loop would chase credentials forever.
11. **The category labels cannot be trusted for filtering.** Fentanyl is filed under "Analgesia," not "Anesthesia." Filter a watchlist by category and core drugs silently vanish.
12. **Every record can look unique when it is not.** Shortage presentation strings embed the package's NDC number, so 26 rocuronium records read as 26 different presentations. Cleaned, they are 3.

The tool layer of this build exists to absorb all twelve, so neither the model nor the user ever deals with them.

## Sources on every answer

Every assessment ends with a Sources section, built and checked mechanically:

- The citations are the **exact API queries the tools actually issued** during that assessment, taken from the provenance trace, plus FDA's human-browsable page for each database touched. The model never writes or invents a URL.
- Before display, **every listed link is re-checked live**: API queries are re-issued (cheaply, at one record) and web pages are fetched. Each source is labeled verified or failed. A query that returns zero matches still verifies, because "zero records" is a real answer this tool stands behind.
- In the CLI this appears as the footer of `fdatrack assess`. Over MCP, the server instructs the client to end every answer with the same cited-and-verified Sources section, using the `verify_sources` tool before finalizing.

## Why an agent

The honest version first: two of the three layers of this problem are not agentic. Detecting that a record changed is a diff. Delivering an alert is a print statement. Both are plain code here (see `monitor/` below). I used an agent only where a decision tree actually breaks:

1. **The right endpoint is not knowable in advance.** Ask about rocuronium and the shortage endpoint answers richly. Ask about propofol and it is empty, so the question becomes whether enforcement has anything and whether the product is even marketed. The agent decides where to look next based on what came back empty.
2. **Free text carries the signal.** `"Next Delivery: August 2026"` and `"Check wholesalers for inventory"` are different situations. The `related_info` field is returned verbatim by the tools and judged by the model, because no parser reduces it to a flag.
3. **Absence is ambiguous.** Zero records means adequately supplied, or untracked. Deciding which requires checking whether the product is marketed at all, then saying which reading the evidence supports.
4. **Name resolution is fuzzy and fails per drug.** Shortages say `"Rocuronium Bromide Injection"`, NDC says `"ROCURONIUM BROMIDE"`, and the clean join key (UNII) is not searchable on the NDC endpoint. Resolution means trying paths and judging the results.
5. **One ingredient name spans unrelated products.** Epinephrine covers homeopathic oral dilutions, auto-injectors, code-cart ampules, and 20 mg/mL dental topical. Deciding which cluster the user meant requires reading context.

The MCP boundary is the boundary between mechanism and judgment. Everything deterministic lives below it: pagination past the 1,000-record cap, grouping `50mg/5mL` with `10mg/mL`, filtering API-grade bulk (`1 kg/kg`), reconciling `MM/DD/YYYY` against `YYYYMMDD`, retrying name paths. Everything requiring a call lives above it. A thin one-tool-per-endpoint wrapper would push every one of those quirks into the model's context to rediscover on every run, unverifiably and differently each time.

The full system diagram (clients, the boundary, the tool surface, the core, the three databases, and where each deployment runs) is in [ARCHITECTURE_DIAGRAM.html](ARCHITECTURE_DIAGRAM.html). GitHub shows HTML files as source; open it in a browser.

## The boundaries, and why they are honest

Six rules, each an explicitly tested behavior (`evals/refusals.yaml` runs adversarial prompts against the real agent loop; the transcript in `evals/out/` is a saved run you can reproduce):

- **R1 - No cross-molecule suggestion.** If no same-molecule source is available, it reports that and stops, even when pressed with "hypothetically, for education."
- **R2 - "Not listed" is never "not short."** The most exercised rule in the build, given the coverage gap.
- **R3 - Confidence scoped by product form.** Injectables, volatile gases, IV fluids and orals differ sharply in how well this data covers them.
- **R4 - Never state or compute a dose.** Including "just the arithmetic" conversions between vial sizes.
- **R5 - Group presentations, never declare equivalence.** The 68 rocuronium products clustering at one concentration are shown with FDA's raw strings. There is deliberately no `is_equivalent` field in the schema. A human judges.
- **R6 - A refusal never names what it refuses.** "Did not evaluate alternatives such as vecuronium" is a recommendation wearing a disclaimer, so refusals and data gaps may say only "a different molecule" or "other agents," with no examples. This rule exists because the first adversarial run leaked exactly that way; the eval's leak scan treats a named alternative as a failure even inside a refusal, and no negated phrasing excuses it.

These refusals are not caution theater. FDA publishes no therapeutic alternatives anywhere in this API - the shortage schema is 16 fields and none is an alternative, a substitution, or an equivalence. Any substitution the tool produced would be invented, not sourced.

## Evidence

- `evals/cases.yaml` - 11 golden cases, 52 assertions, each hand-verified against FDA's own site. Chosen to exercise different code paths: the rich loop (rocuronium), pure silence (clozapine), silence-with-recalls (propofol, lactated ringers), discontinuation (succinylcholine), the 200-fold strength spread (epinephrine), recall volume (sodium chloride, levothyroxine), oncology (carboplatin), graceful failure (nonsense input).
- Default mode replays committed fixtures offline: deterministic, no keys, no network.
- `--live` reruns against api.fda.gov. Grow-only counts (recalls, NDC listings) will drift upward from the fixture date; that is the live database moving, and the README's numbers are pinned to 2026-08-24 for that reason.
- `evals/run_refusals.py` - the adversarial suite. 4/4 passing as committed.

Two of the eval numbers correct my own earlier verification notes: clozapine has 5 historical recalls and Lactated Ringer's has 18 (not zero as first recorded), because the original check searched too narrowly. Both corrected numbers were hand-verified against FDA's site.

## What I deliberately cut

- No web front-end in this submission. Boomi said they would run it, so the interfaces are MCP and a CLI.
- No database, no auth, no accounts, no analytics. No email/SMS delivery.
- No therapeutic alternative logic, no dose calculation, no equivalence verdicts - ever, see above.
- No FAERS adverse-event analysis (20.7M records, a different problem). No device/food/vet endpoints.
- No batch or formulary mode: single drug per query. Batch is easy to add and hard to verify, so it waits.
- No fine-tuning, no vector DB, no RAG. The data is small and structured.

## Known failures and rough edges

- **No fuzzy name matching.** "lactaded ringers" returns zero results with a warning saying resolution does not fuzzy-match. It fails closed and tells you, but it does not guess what you meant.
- **Multi-ingredient products resolve by brand name only.** `find_alternate_sources` on "lactated ringers" finds nothing, because the NDC ingredients are the component salts. The resolve step reports the brand match; the alternates step cannot cluster combination fluids.
- **Recall search can miss records** that lack `openfda` name fields *and* name the drug only by brand in the free-text description.
- **Combination-product clustering keys on the queried ingredient's strength only.** Fine for single-ingredient injectables, coarse for multi-ingredient products.
- **The refusal evals are textual.** A negation-aware substring check is honest about being a heuristic; the committed transcript exists so a human can read the actual answers.
- **Live counts drift from committed fixtures.** Expected and documented, but it means `--live` eval runs are advisory, not pass/fail.

## The monitor

`monitor/` watches a list of drugs and reports only what matters. Three steps, and only the middle one is agentic:

- **Detect (plain code).** Snapshot each drug's FDA state to JSON, diff against the last run. The materiality filter is the point: 77% of FDA's record activity is "we looked, nothing changed," and the filter drops all of it. What escalates: a new shortage record, availability getting worse, a To Be Discontinued flag, a new open Class I or II recall. Improvements and paperwork churn are logged as info, not alerts.
- **Assess (the agent).** Only drugs with a material change get a full assessment. No key, no problem: events still deliver, unassessed.
- **Deliver (plain code).** Stdout plus a JSONL log. Deliberately no email, SMS, or database.

```bash
python -m monitor.run                  # live run over monitor/watchlist.yaml
python -m monitor.run --replay quiet   # demo: a Reverified-only day, correctly silent
python -m monitor.run --replay change  # demo: real changes, correctly escalated
```

A correct monitor is mostly silent, which is unshowable in a live demo, so the two replay scenarios are committed fixtures: one proves it stays quiet through a day of FDA no-op touches, the other proves it escalates an availability drop, a discontinuation flag, and a new open recall (a synthetic record, labeled as such). The default watchlist is their drugs; it is configuration, not scope.

**Built single-user, architected for multi-user.** Drug state is global - a shortage on propofol is one fact no matter who watches it - so snapshots and diffs are keyed by drug, never by user. The pieces a hosted deployment swaps are seams, not rewrites: the watchlist becomes the union of all users' lists, `StateStore` (two methods, JSON files today) becomes a database table, and `deliver()` becomes per-user routing of each drug's events to its subscribers. Snapshot, diff, and materiality logic ship unchanged.

## What I would do next

- **User accounts and individual tracking.** Google login, and per-user watchlists so each person tracks their own drugs. The monitor was built for this: drug state is global, users are subscribers, and the seams (watchlist source, state store, delivery routing) are documented in `monitor/`. A hosted front-end already exists for feedback purposes (separate private repo, not part of this submission); accounts are what it needs next.
- **A new-approvals watcher.** The monitor currently watches for changes on listed drugs. FDA's `/drug/drugsfda` endpoint (approvals) could feed a second detect source: newly approved or newly marketed drugs appearing as events. Same detect/assess/deliver shape, one more input.
- **Batch mode** for formulary-scale questions: assess a whole list, summarize what changed. Easy to add on the engine, deliberately cut from this submission because it is hard to verify well (single-drug answers are checkable one at a time).

## Prior art

Public openFDA MCP servers already exist (Certus, cyanheads/openfda-mcp-server, openpharma-org/fda-mcp, others). A thin MCP wrapper over this API is a commodity. What this build adds is the coverage-gap finding, the mechanism/judgment split at the tool boundary, the tested refusals, and provenance as a first-class output. Boomi is an integration company; a reusable tool surface that absorbs a messy public API so consumers do not have to felt like the right thing to bring to this interview.

## Hours

5 hours on the submitted build: 2 deciding what to build, 3 building it.

A further 2 hours went to a hosted front-end for clinician feedback and 1 hour to the write-up. Neither is part of this submission.

## AI usage

See [AI_USAGE.md](AI_USAGE.md): the build log, the decisions, where the model helped most, where it was wrong, and how each catch happened.

