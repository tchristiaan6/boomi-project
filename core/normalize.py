"""Normalization: strength parsing, bulk detection, dates, names.

Strength parsing exists for retrieval correctness (grouping 50mg/5mL with
10mg/mL so "who else makes this" counts 68, not 8). It is NOT clinical
arithmetic and nothing here emits an equivalence judgment (R5).
"""

from __future__ import annotations

import re
from datetime import datetime

# mass units -> mg
_MASS_TO_MG = {"kg": 1_000_000.0, "g": 1000.0, "mg": 1.0, "ug": 0.001, "mcg": 0.001}
# volume units -> mL
_VOL_TO_ML = {"l": 1000.0, "ml": 1.0}

_QTY = r"([\d.,]+)\s*"
_UNIT = r"([a-zA-Z\[\]'_%]+)"
# denominator unit may be absent ('5 mg/1' = per single dosage unit)
_RATIO_RE = re.compile(
    rf"^\s*{_QTY}{_UNIT}\s*/\s*(?:([\d.,]+)\s*)?([a-zA-Z\[\]'_%]*)\s*$"
)
_SINGLE_RE = re.compile(rf"^\s*{_QTY}{_UNIT}\s*$")


def _num(s: str | None) -> float | None:
    if s is None:
        return None
    try:
        return float(s.replace(",", ""))
    except ValueError:
        return None


def parse_strength(raw: str | None) -> str | None:
    """Parse an FDA strength string into a canonical concentration key.

    '50 mg/5mL' -> '10 mg/mL';  '100 mg/10mL' -> '10 mg/mL'
    "1000 [USP'U]/mL" -> "1000 [USP'U]/mL" (kept per-mL, unit verbatim)
    '6 [hp_X]/mL' -> '6 [hp_X]/mL' (homeopathic, kept verbatim per-mL)
    '5 mg/1' -> '5 mg' (per-unit oral form)
    Unparseable -> None. Never guesses.
    """
    if not raw or not raw.strip():
        return None
    s = raw.strip()

    m = _RATIO_RE.match(s)
    if m:
        n_qty, n_unit, d_qty_s, d_unit = m.groups()
        n = _num(n_qty)
        d = _num(d_qty_s) if d_qty_s else 1.0
        if n is None or d is None or d == 0:
            return None
        if d_qty_s is None and not d_unit:
            return None  # malformed, e.g. '5 mg/'
        n_unit_l = n_unit.lower()
        d_unit_l = d_unit.lower()

        # '5 mg/1' style: per single dosage unit
        if not d_unit:
            return _fmt(n / d, n_unit)

        if n_unit_l in _MASS_TO_MG and d_unit_l in _VOL_TO_ML:
            mg = n * _MASS_TO_MG[n_unit_l]
            ml = d * _VOL_TO_ML[d_unit_l]
            return _fmt(mg / ml, "mg") + "/mL"

        if n_unit_l in _MASS_TO_MG and d_unit_l in _MASS_TO_MG:
            # mass/mass (e.g. 1 kg/kg): meaningful only as a ratio; keep as-is,
            # bulk detection handles the API-grade case.
            mg_n = n * _MASS_TO_MG[n_unit_l]
            mg_d = d * _MASS_TO_MG[d_unit_l]
            if mg_d == 0:
                return None
            return _fmt(mg_n / mg_d, "mg") + "/mg"

        # non-mass numerators ([USP'U], [hp_X], [iU] etc.) per volume:
        if d_unit_l in _VOL_TO_ML:
            ml = d * _VOL_TO_ML[d_unit_l]
            return _fmt(n / ml, n_unit) + "/mL"

        # unknown unit pair: keep normalized ratio with verbatim units
        return f"{_fmt(n / d, n_unit)}/{d_unit}"

    m = _SINGLE_RE.match(s)
    if m:
        qty, unit = m.groups()
        n = _num(qty)
        if n is None:
            return None
        return _fmt(n, unit)

    return None


def _fmt(value: float, unit: str) -> str:
    return f"{value:g} {unit}"


_BULK_RATIO_RE = re.compile(
    r"^\s*([\d.,]+)\s*(kg|g|mg)\s*/\s*([\d.,]+)?\s*(kg|g|mg)\s*$", re.IGNORECASE
)


def is_api_grade_bulk(product: dict) -> bool:
    """Detect API-grade bulk that is not a usable clinical product.

    Two signals: NDC product_type says BULK INGREDIENT, or the strength is a
    mass/mass identity ratio (1 kg/kg, 25 kg/25kg, 1 g/g)."""
    ptype = (product.get("product_type") or "").upper()
    if "BULK" in ptype:
        return True
    for ing in product.get("active_ingredients") or []:
        m = _BULK_RATIO_RE.match(ing.get("strength") or "")
        if m:
            n = _num(m.group(1))
            d = _num(m.group(3)) if m.group(3) else 1.0
            n_mg = (n or 0) * _MASS_TO_MG[m.group(2).lower()]
            d_mg = (d or 1) * _MASS_TO_MG[m.group(4).lower()]
            if d_mg and abs(n_mg / d_mg - 1.0) < 0.01:
                return True
    return False


def to_iso_date(raw: str | None) -> str | None:
    """Reconcile openFDA's two date formats to ISO.
    shortages: MM/DD/YYYY ('08/17/2026'); enforcement: YYYYMMDD ('20161102')."""
    if not raw:
        return None
    raw = raw.strip()
    for fmt in ("%m/%d/%Y", "%Y%m%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None  # unparseable: caller decides whether to surface verbatim


_PAREN_RE = re.compile(r"\s*\([^)]*\)")


def presentation_key(raw: str | None) -> str:
    """Group key for shortage presentation strings. FDA embeds the package NDC
    (and sometimes a redundant concentration) in parentheses, which makes every
    record its own group. Strip parentheticals, keep the rest verbatim.
    'Rocuronium Bromide, Injection, 50 mg/5 mL (10 mg/mL) (NDC 0409-1403-10)'
    -> 'Rocuronium Bromide, Injection, 50 mg/5 mL'"""
    if not raw:
        return "(no presentation string)"
    cleaned = _PAREN_RE.sub("", raw).strip().rstrip(",").strip()
    return cleaned or raw.strip()


def norm_name(name: str | None) -> str:
    """Lowercase, collapse whitespace/punctuation for loose name comparison."""
    if not name:
        return ""
    return re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()


def contains_token(haystack: str | None, needle: str) -> bool:
    """Whole-word containment: the needle's tokens must appear as a contiguous
    token run in the haystack. 'ropivacaine' does not match 'bupivacaine',
    and partial-word substring hits are impossible by construction."""
    if not haystack:
        return False
    h = norm_name(haystack).split()
    n = norm_name(needle).split()
    if not n:
        return False
    return any(h[i : i + len(n)] == n for i in range(len(h) - len(n) + 1))
