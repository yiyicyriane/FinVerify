"""Version C: structured extraction (shared with B) + deterministic Python calculation.

C reads B's extraction record for the same (filing, ratio, repeat); it never
calls the model itself. ``compute`` is also the downstream path of version D.
"""

from agent.calculator import RATIOS
from agent.gate import apply_gate
from agent.normalize import enrich_items
from agent.records import doc_meta, prediction
from agent.schemas import FORMULAS
from agent.version_b import failed_extraction_prediction

VERSION = "C"


def compute(ratio: str, items: dict) -> tuple:
    """Gate -> normalise to USD -> calculator. Returns (status, value, enriched_items)."""
    blocked, normalised = apply_gate(ratio, items)
    enriched = enrich_items(normalised)
    if blocked:
        return blocked, None, enriched
    func, names = RATIOS[ratio]
    value, status = func(*[enriched[name]["value_usd"] for name in names])
    return status, value, enriched


def run(extraction: dict, doc: dict, ratio: str) -> dict:
    meta = doc_meta(doc)
    if extraction["outcome"] != "ok":
        return failed_extraction_prediction(VERSION, extraction, meta, ratio)
    status, value, items = compute(ratio, extraction["items"])
    return prediction(
        VERSION, meta, ratio,
        status=status,
        predicted_ratio=value,
        formula_used=FORMULAS[ratio] if status != "missing_input" and status != "unit_mismatch" else None,
        extraction_id=extraction["extraction_id"],
        items=items,
        llm_calls=extraction["llm_calls"],
    )
