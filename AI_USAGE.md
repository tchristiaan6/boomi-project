# Build log and AI-usage notes

An agent that checks every FDA database for any drug you name, cites what it found, and says plainly when FDA has nothing on it.

**8 hours total · 11 test cases · 52/52 assertions offline · 4/4 refusals held**

## 01 · Deciding what to build

- Started by reviewing the available API data in a Claude Cowork session
- Brainstormed possible projects, considered previous supplement-related projects
- Worked within Cowork to find data discrepancies
- Reached out to family and friends for feedback on a drug shortage tracker
- Ultimately decided on a "get the latest FDA data for any drug" tool

**What the feedback changed.** My brother is a CRNA and my sister-in-law is an NP. I sent them a list of test drugs. They came back with what actually goes short: gases, saline, lactated ringers, antibiotics, insulins, and propofol, which my brother called "the #1 drug we use."

I checked all 1,628 shortage records against that list. **Propofol, sevoflurane, isoflurane, cefazolin and ondansetron all return zero.** The word "ringer" does not appear in any field of any record.

FDA tracks shortages that manufacturers report. What he deals with is allocation, where the distributor fills part of the order. Nobody reports that to FDA. So the tool reports every signal FDA does publish, and is clear when there is none.

## 02 · Limitations and concerns

Found by probing the live API before writing any code.

- **limit > 1000** - returns `API_KEY_MISSING`, a misleading error
- **skip cap** - hard-capped at 25,000 records
- **404 = zero** - openFDA returns HTTP 404 for "no matches", not an error
- **Two date formats** - shortages use `mm/dd/yyyy`, enforcement uses `yyyymmdd`
- **count unsupported** - works on enforcement and ndc, not on shortages
- **No diff available** - `change_date` is populated on 2 of 1,000 records, so you have to snapshot and compare yourself
- **Status is not status** - `Current` and `Available` appear together on 2 of every 3 records
- **Category is unreliable** - fentanyl and hydromorphone are filed under Analgesia, not Anesthesia
- **Strengths unnormalized** - `10 mg/mL`, `50 mg/5mL` and `100 mg/10mL` are the same strength written three ways
- **Bulk chemicals in results** - `1 kg/kg` drums listed beside hospital vials
- **Naive text search fails** - searching recalls for "dog" returns hot dog buns
- **Broken join key** - `openfda.unii` is not searchable on `/drug/ndc`
- **FDA's own typo** - one record's availability reads `"Unvailable"`

**Normalization, in plain terms.** Drug strengths are written however each manufacturer wrote them. Rocuronium shows up as `10 mg/mL`, `50 mg/5mL`, and `100 mg/10mL`. Those are all the same strength, just written three ways. If the tool treats them as three different products, then "who else makes this drug" comes back as **8 manufacturers when the real answer is 68**. So the tool does the math to recognize when two labels mean the same thing. **That is only about counting correctly. It is not about telling anyone two products can be swapped.**

The reverse problem also exists. Epinephrine covers a homeopathic dilution, an EpiPen, and a hospital ampule, all under one name, across a 200-fold range of strengths. The tool has to keep those apart so a question about a crash cart does not get answered with nasal spray. Both are about pulling the right records. Neither is a medical judgment, and the tool does not make one.

## 03 · Requirements and design doc

- Loaded the Cowork-produced `ARCHITECTURE.md` and `CONTEXT.md` into Claude Code, along with a description of the overall project
- Included empirical findings on the API traps above, a "do not build" list, and the decision logic behind choices already settled
- Multi-phase approach: a GitHub repo for local testing (the Boomi portion), and a web front-end for user testing, given the real use case

**MCP design.** The boundary is the architecture: everything deterministic lives below the MCP surface; everything requiring judgment lives above it. A one-tool-per-endpoint wrapper would push every rate limit and unit mismatch into the model's context to rediscover on every run, unverifiably. Seven tools, each one complete retrieval step with the mess already handled - too fine and the model does plumbing; too coarse and the server reasons and nothing is verifiable. Provenance on every result: exact queries issued, totals, what was filtered and why.

**API endpoints.**

| Endpoint | Records | What it answers |
|---|---:|---|
| `/drug/shortages.json` | 1,628 | Manufacturer-reported shortages and To Be Discontinued flags |
| `/drug/ndc.json` | 137,198 | Every marketed product: forms, strengths, labelers, packaging |
| `/drug/enforcement.json` | 17,876 | Recalls, open and historical, with classifications |
| `/drug/label.json` | cut | **Deliberately excluded.** 192KB per record, and what it adds is dosing and contraindication text the tool refuses to act on anyway. |

Plus one LLM API for the assess loop, swappable via env var. Three databases, no auth beyond a free rate-limit key, no database, no vector store.

## 04 · Architecture

One codebase, three transports; the diagrams live in the [README](README.md). The claims they draw:

- **The MCP boundary is the boundary between mechanism and judgment.** The mess is handled once, below the line. The model spends its judgment on what only judgment can do: choosing the next endpoint when one comes back empty, reading free-text notes, and saying what the data does not say.
- **Neither deployment target compromises the other.** The graded repo has no hosting dependencies: clone, install, run, all evals pass offline from committed fixtures. The hosted site reuses `core/` unchanged behind a thin FastAPI adapter. Hours reported separately.
- **In the monitor, only the middle step is agentic.** Detect and deliver are cron jobs and are built as cron jobs.

## 05 · Test cases and evidence

11 test cases, 52 assertions, all running offline from committed fixtures. No keys required.

| Case | What it proves |
|---|---|
| rocuronium | The full loop. 26 records, 11 firms, 3 presentations, mixed availability. |
| propofol | Zero shortage records on the drug my brother calls #1. Silence reported as silence. |
| lactated ringers | Zero shortage mentions anywhere - but 18 recall records, including an **ongoing 2026 Class I** on the exact bags he cannot get. |
| succinylcholine | The discontinuation path. Permanent withdrawal, not temporary constraint. |
| epinephrine | Homeopathic dilutions through 20 mg/mL topical in one result set. Grouping without verdicts. |
| sodium chloride | Recall-dominant: 1,000+ enforcement records, no active shortage. |
| penicillin g benzathine | A real FDA guidance link and a rich free-text recovery note to judge. |
| clozapine | Generality. Marketed, widely prescribed, almost nothing to report. |
| levothyroxine | Volume. 184 recalls, 679 marketed products, zero shortages. |
| carboplatin | Oncology shortage. Proves this is not an anesthesia tool wearing a general-purpose label. |
| nonsense string | Fails closed with a warning rather than guessing what you meant. |

**Example eval case.** My spec recorded clozapine and Lactated Ringer's as having **zero recalls**, "genuinely absent from both databases." The build's recall search - which combines `openfda` name fields with a whole-word description search - found **5 clozapine recalls and 18 for Lactated Ringer's**. My original checks had searched too narrowly. Hand-verified against FDA's site, then pinned in the evals.

## 06 · Safety features

Problems I hit while building, and what I did about each one.

- **No data does not imply no shortage.** Most drugs a clinician cares about return nothing from FDA. Added an explicit disclaimer so silence never reads as an all-clear.
- **Multiple volumes require normalization.** Manageable for liquids and pills, difficult for gases, creams, ointments and injectables. Added refusals for any arithmetic, plus a disclaimer that swapping products is a clinical decision, meaning a human one.
- **Cross-molecule suggestions.** The model wants to be helpful and recommend an alternative drug. Removed that behavior. There is no alternatives logic anywhere in the codebase, and the schema has no `is_equivalent` field by design.
- **Refusals that leak.** Under a "hypothetically, for education" prompt the model refused to recommend a paralytic, then named four of them inside the refusal. Added a rule that a refusal cannot name what it declines.
- **Tested adversarial and misspelling prompts.** Pressure prompts for substitutions, doses and yes/no equivalence answers, plus misspelled drug names. Misspellings fail closed with a warning rather than guessing.
- **Empty but valid answers.** One run returned a schema-valid assessment reading `summary: "placeholder"` with every list empty. Added minimum-content rules so those bounce instead of passing.
- **Added references and sources to every response**, and every citation is re-checked live before it displays, so nothing shows a link that does not resolve.

## 07 · Future and planned features

- **User accounts and per-user watchlists.** The monitor was built for this. Drug state is global, so a shortage on propofol is one fact no matter who watches it, and the three seams (watchlist source, state store, delivery routing) are documented in the repo. Swaps, not rewrites.
- **New-approvals watcher.** New drug detection and alerting, using FDA's approvals endpoint as a second input. Same detect, assess, deliver shape.
- **Batch and formulary mode.** Assess a whole drug list at once. Cut from this build because single-drug answers are checkable one at a time and batch answers are not.
- **Fuzzy name matching.** Today a misspelling fails closed with a warning. It should offer a correction instead.
- **Manufacturer, distributor and partner details.** Who actually supplies a given product, which is closer to the question a buyer is really asking.

## 08 · Hours and AI usage

Reported split, because only the first five hours are the submission.

| Work | Hours | Notes |
|---|---:|---|
| Deciding what to build | 2 | Probing the API, killing two ideas, the conversations with two clinicians |
| Building with Claude Code | 3 | Engine, evals, monitor, against the spec |
| **Boomi deliverable** | **5** | The submitted build |
| Web front-end | 2 | fdatrack.com, for clinician feedback. Not part of the submission. |
| Write-up | 1 | This document and the architecture diagrams |
| **Total** | **8** | |

**How it was built.**

- **Spec and requirements first, then implementation.** The model wrote *all* of the code. I directed what got built, kept the scope clean, applied user feedback, tested inputs and outputs, and used AI to verify our test cases.
- **Where it helped most:** confirming API access and outputs, finding data discrepancies, building test cases, brainstorming, the overall build spec, and the running code.
- **Documented catches where the model was wrong:** the drug equivalence overclaim; two of my own "verified" numbers; grouping that produced 26 groups where the real answer was 3; a refusal that declined to recommend an alternative and then named four of them inside the refusal; and an answer that was schema-valid but empty, reading `summary: "placeholder"` with every list blank.

**The pattern.** Catches came from re-verifying expected answers, adversarial evals, reading the actual output, and (planned) expert feedback.

---

Data verified against api.fda.gov 2026-08-24 · repo: github.com/tchristiaan6/boomi-project · live: fdatrack.com
