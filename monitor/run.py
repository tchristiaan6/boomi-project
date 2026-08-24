"""The monitor: detect -> assess -> deliver. Only the middle step is agentic.

  python -m monitor.run                  # live run over the watchlist
  python -m monitor.run --no-assess      # detect + deliver only, no model
  python -m monitor.run --replay quiet   # demo: a Reverified-only day, correctly silent
  python -m monitor.run --replay change  # demo: real changes, correctly escalated

First live run snapshots a baseline and reports nothing (there is nothing to
diff against). Subsequent runs diff against the stored state. A correct
monitor is mostly silent; the replay fixtures exist so that silence is
demonstrable instead of unshowable.

Delivery is stdout plus a JSONL file. Deliberately no email, SMS, or
database (spec section 9). Multi-user note: events are per-drug facts; a
hosted deployment routes each drug's events to its subscribers at this
layer, and only this layer.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

import yaml

from core.env import load_env

load_env()

from core.fda_client import FDAClient, utcnow_iso
from monitor.diff import ChangeEvent, diff_states
from monitor.state import JsonStateStore, take_snapshot

WATCHLIST = Path(__file__).resolve().parent / "watchlist.yaml"
REPLAY_DIR = Path(__file__).resolve().parent / "fixtures"
ALERT_LOG = Path(__file__).resolve().parent / "state" / "alerts.jsonl"


def load_watchlist(path: Path = WATCHLIST) -> list[str]:
    return yaml.safe_load(path.read_text())["drugs"]


def deliver(events: list[ChangeEvent], assessments: dict[str, str]) -> None:
    """Local delivery: stdout + JSONL. The hosted seam: replace this with
    per-user routing; nothing upstream changes."""
    for ev in events:
        tag = "ALERT" if ev.severity == "material" else "info "
        print(f"  [{tag}] {ev.drug}: {ev.kind} - {ev.detail}")
    for drug, summary in assessments.items():
        print(f"\n  assessment ({drug}):\n    {summary}\n")
    if events:
        ALERT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with ALERT_LOG.open("a") as f:
            for ev in events:
                f.write(json.dumps({
                    "at": utcnow_iso(), **asdict(ev),
                    "assessment": assessments.get(ev.drug),
                }) + "\n")


def assess_drugs(drugs: list[str]) -> dict[str, str]:
    """Run the agent on drugs with material changes. Degrades honestly:
    without ANTHROPIC_API_KEY the events still deliver, unassessed."""
    import os
    if "ANTHROPIC_API_KEY" not in os.environ:
        print("  (no ANTHROPIC_API_KEY: delivering events without assessment)")
        return {}
    from core.assess import assess
    out = {}
    for drug in drugs:
        try:
            out[drug] = assess(drug).summary
        except Exception as exc:
            out[drug] = f"assessment failed: {exc}"
    return out


def run_live(no_assess: bool) -> int:
    drugs = load_watchlist()
    store = JsonStateStore()
    client = FDAClient()
    all_events: list[ChangeEvent] = []
    baselines = 0
    try:
        for drug in drugs:
            new = take_snapshot(client, drug)
            old = store.load(drug)
            if old is None:
                baselines += 1
                print(f"{drug}: baseline snapshot stored "
                      f"({len(new['shortages'])} shortage records, "
                      f"{len(new['recalls'])} recall records)")
            else:
                events = diff_states(old, new)
                material = [e for e in events if e.severity == "material"]
                print(f"{drug}: {len(events)} change(s), "
                      f"{len(material)} material")
                all_events.extend(events)
            store.save(drug, new)
    finally:
        client.close()

    material_drugs = sorted({e.drug for e in all_events if e.severity == "material"})
    assessments = {} if (no_assess or not material_drugs) else assess_drugs(material_drugs)
    if all_events:
        print()
        deliver(all_events, assessments)
    elif not baselines:
        print("\nNo changes since last run. A quiet monitor is a working monitor.")
    return 0


def run_replay(scenario: str) -> int:
    before = json.loads((REPLAY_DIR / f"{scenario}_before.json").read_text())
    after = json.loads((REPLAY_DIR / f"{scenario}_after.json").read_text())
    events = diff_states(before, after)
    material = [e for e in events if e.severity == "material"]
    print(f"replay '{scenario}': {len(events)} event(s), {len(material)} material")
    deliver(events, {})
    if scenario == "quiet":
        ok = not events
        print("PASS: Reverified-only day produced zero events."
              if ok else "FAIL: quiet day produced events.")
        return 0 if ok else 1
    ok = bool(material)
    print("PASS: real changes escalated as material."
          if ok else "FAIL: real changes were not escalated.")
    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--replay", choices=["quiet", "change"], default=None)
    parser.add_argument("--no-assess", action="store_true",
                        help="skip the agent step, deliver raw events")
    args = parser.parse_args()
    if args.replay:
        return run_replay(args.replay)
    return run_live(args.no_assess)


if __name__ == "__main__":
    sys.exit(main())
