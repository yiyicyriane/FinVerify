"""Deterministic number parsing and scale normalisation.

The LLM copies numbers exactly as the statement shows them and names the scale
from the statement header ("In millions" -> ``millions``). Conversion to raw
USD happens only here, never inside an LLM call.

This module does not "fix" model output: a ratio reported as 46.9 instead of
0.469 stays 46.9 so the error is visible to the analysis.
"""

import re
from typing import Optional

from agent.schemas import INPUT_CONCEPT, SCALE_FACTOR

_DASHES = {"", "-", "–", "—", "n/a", "na", "none", "null"}


def parse_number(value) -> Optional[float]:
    """Parse a reported number: ``1234``, ``"$1,234"``, ``"(14,264)"`` (negative), ``"—"`` (None)."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"not a number: {value!r}")
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if text.lower() in _DASHES:
        return None
    negative = text.startswith("(") and text.endswith(")") or text.startswith("-")
    digits = re.sub(r"[^0-9.]", "", text)
    if not digits or digits.count(".") > 1:
        raise ValueError(f"not a number: {value!r}")
    number = float(digits)
    return -number if negative else number


def to_usd(value_reported, scale: Optional[str]) -> Optional[float]:
    """Convert a reported value to raw USD. Returns None if either part is missing."""
    number = parse_number(value_reported)
    if number is None or scale is None:
        return None
    if scale not in SCALE_FACTOR:
        raise ValueError(f"unknown scale: {scale!r}")
    return number * SCALE_FACTOR[scale]


def enrich_items(items: dict) -> dict:
    """Add ``concept`` and ``value_usd`` to every item (input name -> item dict)."""
    out = {}
    for name, item in items.items():
        enriched = dict(item)
        enriched["concept"] = INPUT_CONCEPT.get(name, name)
        try:
            enriched["value_usd"] = to_usd(item.get("value_reported"), item.get("scale"))
        except ValueError:
            enriched["value_usd"] = None
        out[name] = enriched
    return out
