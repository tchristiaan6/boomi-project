"""CLI: assess one drug with the full trace, or call a single tool raw.

  fdatrack assess "rocuronium"          # agent loop, needs ANTHROPIC_API_KEY
  fdatrack lookup shortage rocuronium   # one tool, no model needed
  fdatrack lookup resolve "lactated ringers"
  fdatrack lookup alternates propofol --route INTRAVENOUS
  fdatrack lookup recalls propofol
  fdatrack lookup discontinuations insulin
  fdatrack lookup label 0409-9522
"""

from __future__ import annotations

import argparse
import json
import sys

from core.env import load_env

load_env()

from core import tools as t
from core.fda_client import FDAClient


def _print(obj: dict) -> None:
    print(json.dumps(obj, indent=2, default=str))


def cmd_lookup(args: argparse.Namespace) -> int:
    client = FDAClient()
    try:
        if args.tool == "resolve":
            out = t.resolve_drug(client, args.query)
        elif args.tool == "shortage":
            out = t.get_shortage_picture(client, args.query)
        elif args.tool == "discontinuations":
            out = t.get_discontinuations(client, args.query)
        elif args.tool == "alternates":
            out = t.find_alternate_sources(client, args.query, args.route)
        elif args.tool == "recalls":
            out = t.check_recalls(client, ingredient=args.query)
        elif args.tool == "label":
            out = t.get_label_facts(client, args.query)
        else:
            print(f"unknown tool: {args.tool}", file=sys.stderr)
            return 2
        _print(out)
        return 0
    finally:
        client.close()


def _print_human(result) -> None:
    from core.sources import format_sources

    d = result
    print(f"FDATrack assessment: {d.query}")
    resolved = d.resolved_to or "(did not resolve)"
    marketed = {True: "yes", False: "no", None: "unknown"}[d.is_marketed]
    print(f"Resolved to: {resolved}  |  confidence: {d.resolution_confidence}"
          f"  |  marketed: {marketed}\n")
    print(d.summary + "\n")
    print("Signals:")
    for s in d.signals:
        print(f"  - {s.kind}: {s.detail}")
    if d.presentation_groups:
        print("\nPresentation groups (FDA strings verbatim, no equivalence judged):")
        for g in d.presentation_groups:
            print(f"  - {g.parsed_strength or 'unparseable strength'}"
                  f" | {g.route or '?'} | {g.product_count} products"
                  f" | raw: {', '.join(g.raw_strength_strings)}")
    print("\nWhat the data does not say:")
    for gap in d.data_gaps:
        print(f"  - {gap}")
    print("\nNot evaluated (by design):")
    for ref in d.refusals:
        print(f"  - {ref}")
    print(f"\nOverall confidence: {d.overall_confidence}\n")
    print(format_sources(d.sources))


def cmd_assess(args: argparse.Namespace) -> int:
    from core.assess import assess  # imports anthropic lazily

    def on_event(kind: str, payload: dict) -> None:
        if not args.trace:
            return
        if kind == "tool_call":
            print(f"\n>> {payload['name']}({json.dumps(payload['input'])})",
                  file=sys.stderr)
        elif kind == "tool_result":
            prov = payload["result"].get("provenance", {})
            if prov:
                print(
                    f"   matched={prov.get('total_matched')} "
                    f"returned={prov.get('returned')} "
                    f"warnings={prov.get('warnings')}",
                    file=sys.stderr,
                )
            elif "error" in payload["result"]:
                print(f"   error: {payload['result']['error']}", file=sys.stderr)
        elif kind == "text":
            print(f"   [model] {payload['text'][:400]}", file=sys.stderr)

    result = assess(args.query, on_event=on_event)
    if args.json:
        _print(result.model_dump())
    else:
        _print_human(result)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="fdatrack",
        description="What does FDA actually say about this product right now?",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_assess = sub.add_parser("assess", help="full agent assessment of one drug")
    p_assess.add_argument("query", help="free-text drug name")
    p_assess.add_argument("--no-trace", dest="trace", action="store_false",
                          help="suppress the tool-call trace on stderr")
    p_assess.add_argument("--json", action="store_true",
                          help="emit the full Assessment JSON instead of text")
    p_assess.set_defaults(func=cmd_assess, trace=True)

    p_lookup = sub.add_parser("lookup", help="run one tool directly, no model")
    p_lookup.add_argument(
        "tool",
        choices=["resolve", "shortage", "discontinuations",
                 "alternates", "recalls", "label"],
    )
    p_lookup.add_argument("query", help="drug name (or NDC for 'label')")
    p_lookup.add_argument("--route", default=None,
                          help="route filter for 'alternates'")
    p_lookup.set_defaults(func=cmd_lookup)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
