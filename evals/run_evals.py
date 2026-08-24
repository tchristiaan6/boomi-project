"""Golden-case eval runner for the six tools. No model involved.

  python -m evals.run_evals             # offline: replay committed fixtures
  python -m evals.run_evals --live      # against api.fda.gov (drift expected)
  python -m evals.run_evals --record    # refresh fixtures from the live API

Default mode is deterministic and needs no API key of any kind.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

from core import tools as t
from core.fda_client import FDAClient

CASES = Path(__file__).resolve().parent / "cases.yaml"

TOOLS = {
    "resolve_drug": t.resolve_drug,
    "get_shortage_picture": t.get_shortage_picture,
    "get_discontinuations": t.get_discontinuations,
    "find_alternate_sources": t.find_alternate_sources,
    "check_recalls": t.check_recalls,
    "get_label_facts": t.get_label_facts,
}


def dig(obj, path: str):
    """Walk 'data.groups.0.parsed_strength' style paths. Returns (found, value)."""
    cur = obj
    for part in path.split("."):
        if isinstance(cur, list):
            try:
                cur = cur[int(part)]
            except (ValueError, IndexError):
                return False, None
        elif isinstance(cur, dict):
            if part not in cur:
                return False, None
            cur = cur[part]
        else:
            return False, None
    return True, cur


def check(envelope: dict, exp: dict) -> tuple[bool, str]:
    path, op = exp["path"], exp["op"]
    want = exp.get("value")
    found, got = dig(envelope, path)

    if op == "not_exists":
        ok = not found
        return ok, f"{path} {'absent' if ok else f'unexpectedly present: {got!r}'}"
    if not found:
        return False, f"{path} not found in result"
    if op == "eq":
        return got == want, f"{path} == {got!r} (want {want!r})"
    if op == "ge":
        return got >= want, f"{path} == {got!r} (want >= {want!r})"
    if op == "le":
        return got <= want, f"{path} == {got!r} (want <= {want!r})"
    if op == "len_eq":
        return len(got) == want, f"len({path}) == {len(got)} (want {want})"
    if op == "len_ge":
        return len(got) >= want, f"len({path}) == {len(got)} (want >= {want})"
    if op == "contains":
        hay = got if isinstance(got, str) else json.dumps(got)
        return str(want) in hay, f"{path} contains {want!r}"
    return False, f"unknown op {op!r}"


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--live", action="store_true",
                      help="run against api.fda.gov instead of fixtures")
    mode.add_argument("--record", action="store_true",
                      help="run live and (re)write fixtures")
    parser.add_argument("--case", default=None, help="run one case by name")
    args = parser.parse_args()

    fixtures_mode = "replay"
    if args.live:
        fixtures_mode = "live"
    elif args.record:
        fixtures_mode = "record"

    cases = yaml.safe_load(CASES.read_text())
    if args.case:
        cases = [c for c in cases if c["name"] == args.case]
        if not cases:
            print(f"no case named {args.case!r}", file=sys.stderr)
            return 2

    client = FDAClient(fixtures_mode=fixtures_mode)
    passed = failed = 0
    try:
        for case in cases:
            print(f"\n== {case['name']} ({fixtures_mode})")
            for call in case["calls"]:
                fn = TOOLS[call["tool"]]
                try:
                    envelope = fn(client, **call["args"])
                except Exception as exc:
                    print(f"  ERROR {call['tool']}({call['args']}): {exc}")
                    failed += len(call.get("expect", []))
                    continue
                for exp in call.get("expect", []):
                    ok, msg = check(envelope, exp)
                    tag = "ok  " if ok else "FAIL"
                    print(f"  {tag} {call['tool']}: {msg}")
                    passed += ok
                    failed += not ok
    finally:
        client.close()

    print(f"\n{passed} passed, {failed} failed"
          + (" (live drift is expected for grow-only counts)"
             if args.live and failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
