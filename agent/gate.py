"""Input gate shared by versions B, C and C_tool (one implementation, no copies).

Structured outputs guarantee the *structure* of the extraction (every required
input present exactly once). The gate checks *consistency* between fields and
decides whether a ratio can be computed at all.

Step 1, normalise each item (conservative interpretation, flagged for analysis):
  * status "extracted" but value_reported is null  -> treated as "not_found"
  * status "extracted" but scale is null           -> treated as "unit_unclear"
  * status "not_found" but a number was given      -> treated as "not_found", number ignored
  * required input missing entirely (only possible if the schema guarantee is
    unavailable and the array fallback is used)   -> treated as "not_found"

Step 2, prediction status by priority (identical for B, C and C_tool):
  1. any input not_found     -> "missing_input"  (also when unit_unclear is present too)
  2. any input unit_unclear  -> "unit_mismatch"
  3. otherwise the gate passes and B's LLM and C's calculator see the same numbers.

When the gate blocks, the B / C_tool calculation LLM is not called, so the
three versions always share the same denominator.
"""

from typing import Optional, Tuple

from agent.normalize import parse_number
from agent.schemas import ratio_inputs


def normalise_item(item: Optional[dict]) -> Tuple[str, bool]:
    """Return (effective_status, inconsistent) for one item."""
    if item is None:
        return "not_found", True
    status = item.get("status")
    try:
        value = parse_number(item.get("value_reported"))
    except ValueError:
        value = None
        if status == "extracted":
            return "not_found", True
    if status == "extracted":
        if value is None:
            return "not_found", True
        if item.get("scale") is None:
            return "unit_unclear", True
        return "extracted", False
    if status == "not_found":
        return "not_found", value is not None
    if status == "unit_unclear":
        return "unit_unclear", False
    return "not_found", True  # unknown status: be conservative


def apply_gate(ratio: str, items: dict) -> Tuple[Optional[str], dict]:
    """Normalise ``items`` and return (blocking_status_or_None, normalised_items).

    ``normalised_items`` keeps every field the model returned and adds
    ``status`` (effective) and ``inconsistent``; ``status_reported`` keeps the
    model's original status when it was changed.
    """
    normalised = {}
    statuses = []
    for name in ratio_inputs(ratio):
        raw = items.get(name) if isinstance(items, dict) else None
        effective, inconsistent = normalise_item(raw)
        item = dict(raw or {})
        if raw is not None and raw.get("status") != effective:
            item["status_reported"] = raw.get("status")
        item["status"] = effective
        item["inconsistent"] = inconsistent
        normalised[name] = item
        statuses.append(effective)
    if "not_found" in statuses:
        return "missing_input", normalised
    if "unit_unclear" in statuses:
        return "unit_mismatch", normalised
    return None, normalised
