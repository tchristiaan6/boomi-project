# FDATrack

**Status: Phase 1 (engine) built and verified against the live API. README narrative, eval suite and fixtures land in Phase 2. This notice is removed when the build is submission-ready.**

An agent that answers one question for clinicians and the pharmacy staff who supply them:

> **"What does FDA actually say about this product right now?"**

Not "is it short." That distinction is the point. I built this for my brother, a nurse anesthetist. The first thing it told me was that FDA does not track most of what he cares about. Propofol, the drug he calls the number one in his OR: zero shortage records. The word "ringer" appears nowhere in FDA's shortage database. The tool reports every signal FDA does publish - shortages, discontinuations, recalls - and when FDA is silent it says so plainly, because silence is not evidence of adequate supply.

## Quick start

Requires Python 3.11+.

```bash
git clone https://github.com/tchristiaan6/boomi-project.git
cd boomi-project
python3 -m venv .venv && .venv/bin/pip install -e .
```

Look up any drug with one tool, no model or API key needed:

```bash
.venv/bin/fdatrack lookup shortage rocuronium
.venv/bin/fdatrack lookup recalls propofol
.venv/bin/fdatrack lookup alternates rocuronium
.venv/bin/fdatrack lookup resolve "lactated ringers"
```

Full agent assessment (needs `ANTHROPIC_API_KEY`; model swappable via `FDATRACK_MODEL`):

```bash
export ANTHROPIC_API_KEY=sk-ant-...
.venv/bin/fdatrack assess "rocuronium"
```

An openFDA API key is optional (`export FDA_API_KEY=...`, free at https://open.fda.gov/apis/authentication/). Without one you get 1,000 requests/day per IP, which covers dozens of assessments.

### MCP server (Claude Desktop / Claude Code)

```json
{
  "mcpServers": {
    "fdatrack": {
      "command": "/absolute/path/to/boomi-project/.venv/bin/python",
      "args": ["-m", "servers.mcp_stdio"],
      "cwd": "/absolute/path/to/boomi-project"
    }
  }
}
```

Six tools: `resolve_drug`, `get_shortage_picture`, `get_discontinuations`, `find_alternate_sources`, `check_recalls`, `get_label_facts`. Every result carries a provenance block: the exact queries issued, totals, what was filtered out and why, and warnings.

## What it will not do

It will not tell you to use a different drug. It will not calculate a dose. When two presentations of the same drug differ, it shows you the groups with FDA's raw strength strings and lets you decide, rather than declaring them interchangeable. These are tested behaviors, not disclaimers.

## Layout

```
core/       pure library: client, normalization, the six tools, the agent loop
servers/    thin MCP stdio adapter
cli/        assess one drug, or call one tool raw
monitor/    (phase 3) snapshot/diff watchlist, plain code, no model
evals/      (phase 2) golden cases verified against FDA's own site
fixtures/   (phase 2) committed API responses for offline replay
```
