"""Diff two drug-state snapshots into change events. Pure functions, no I/O.

The materiality filter is the reason this module exists: 77% of FDA's daily
record activity is `update_type: Reverified`, meaning FDA looked and nothing
changed, and `status: Current` co-occurs with `availability: Available` on
two thirds of records. A monitor that alerts on raw churn pages people about
nothing. Rules:

MATERIAL (worth waking someone for; the agent assesses these):
- new shortage record
- availability worsening (Available -> Limited Availability / Unavailable)
- status becoming To Be Discontinued
- new recall that is open, Class I or II

INFO (logged, not escalated):
- availability improving, shortage record removed, recall terminated,
  new Class III / unclassified recall

IGNORED entirely:
- update_date bumps and Reverified touches with no field change
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ChangeEvent:
    drug: str
    kind: str                # e.g. shortage_new, availability_worse, recall_new_open
    severity: str            # "material" | "info"
    key: str                 # record identity (package NDC / recall number)
    detail: str
    before: dict = field(default_factory=dict)
    after: dict = field(default_factory=dict)


# Ranked availability: higher is worse. Free-text values rank as unknown (-1)
# and never trigger a worsening event on their own; the raw change still
# surfaces as info.
_AVAIL_RANK = {"available": 0, "limited availability": 1, "unavailable": 2,
               "unvailable": 2}  # FDA's own typo is a real production value


def _rank(availability: str | None) -> int:
    return _AVAIL_RANK.get((availability or "").strip().lower(), -1)


def diff_states(old: dict, new: dict) -> list[ChangeEvent]:
    """Compare two snapshots of the same drug. Empty list = quiet day."""
    drug = new.get("drug", "?")
    events: list[ChangeEvent] = []

    old_sh, new_sh = old.get("shortages", {}), new.get("shortages", {})
    for key, rec in new_sh.items():
        prev = old_sh.get(key)
        if prev is None:
            events.append(ChangeEvent(
                drug, "shortage_new", "material", key,
                f"new shortage record: {rec.get('presentation')} "
                f"({rec.get('company')}), availability {rec.get('availability')}",
                after=rec,
            ))
            continue
        if rec.get("status") != prev.get("status"):
            severity = (
                "material"
                if rec.get("status") == "To Be Discontinued" else "info"
            )
            events.append(ChangeEvent(
                drug, "status_change", severity, key,
                f"status {prev.get('status')} -> {rec.get('status')} "
                f"({rec.get('presentation')})",
                before=prev, after=rec,
            ))
        if rec.get("availability") != prev.get("availability"):
            old_rank, new_rank = _rank(prev.get("availability")), _rank(rec.get("availability"))
            worse = old_rank >= 0 and new_rank >= 0 and new_rank > old_rank
            events.append(ChangeEvent(
                drug, "availability_worse" if worse else "availability_change",
                "material" if worse else "info", key,
                f"availability {prev.get('availability')!r} -> "
                f"{rec.get('availability')!r} ({rec.get('presentation')}, "
                f"{rec.get('company')})",
                before=prev, after=rec,
            ))
        # No branch for update_date / update_type alone: a Reverified touch
        # with no field change is exactly the no-op this filter exists to drop.
    for key, prev in old_sh.items():
        if key not in new_sh:
            events.append(ChangeEvent(
                drug, "shortage_removed", "info", key,
                f"shortage record no longer listed: {prev.get('presentation')} "
                f"({prev.get('company')})",
                before=prev,
            ))

    old_rc, new_rc = old.get("recalls", {}), new.get("recalls", {})
    for key, rec in new_rc.items():
        prev = old_rc.get(key)
        if prev is None:
            is_open = rec.get("status") != "Terminated"
            serious = rec.get("classification") in ("Class I", "Class II")
            events.append(ChangeEvent(
                drug, "recall_new_open" if (is_open and serious) else "recall_new",
                "material" if (is_open and serious) else "info", key,
                f"new {rec.get('classification')} recall "
                f"({rec.get('status')}): {rec.get('description')}",
                after=rec,
            ))
        elif rec.get("status") != prev.get("status"):
            events.append(ChangeEvent(
                drug, "recall_status_change", "info", key,
                f"recall {key}: {prev.get('status')} -> {rec.get('status')}",
                before=prev, after=rec,
            ))

    return events
