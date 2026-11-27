"""Version D: pipeline correctness check (XBRL ground-truth inputs, same downstream path as C).

D fills the extraction schema with the ground-truth input values and runs them
through exactly the code C uses after extraction (gate -> normalise ->
calculator -> prediction record).

What D does NOT show: that the calculator is correct. The ground-truth ratios
in data/ground_truth.json were themselves computed with agent/calculator.py,
so a calculator bug would appear in both and D would still score 100%.
Calculator correctness is checked against hand-computed values instead
(tests/test_calculator_handcheck.py: the 12 ratios hand-checked in
docs/ground_truth_verification.md, plus the toy filings).

D is the only version that reads ground truth.
"""

import json
from pathlib import Path

from agent.records import prediction
from agent.schemas import FORMULAS
from agent.version_c import compute

VERSION = "D"
GROUND_TRUTH_PATH = Path(__file__).resolve().parent.parent / "data" / "ground_truth.json"


def load_ground_truth(path: Path = GROUND_TRUTH_PATH) -> dict:
    """{(filing_id, ratio): record} from data/ground_truth.json."""
    records = json.loads(Path(path).read_text(encoding="utf-8"))
    return {(r["filing_id"], r["ratio"]): r for r in records}


def items_from_ground_truth(record: dict) -> dict:
    """Ground-truth inputs in the extraction schema (raw USD, scale ``units``)."""
    items = {}
    for inp in record["inputs"]:
        value = inp.get("value")
        items[inp["input"]] = {
            "value_reported": value,
            "scale": "units",
            "period_end": inp.get("period_end"),
            "source_statement": None,
            "source_label": f"xbrl:{inp['tag_used']}" if inp.get("tag_used") else None,
            "status": "extracted" if value is not None else "not_found",
        }
    return items


def run(items: dict, meta: dict, ratio: str) -> dict:
    """``items`` come from ``items_from_ground_truth`` (real filings) or a toy filing's answer key."""
    status, value, enriched = compute(ratio, items)
    return prediction(VERSION, meta, ratio, status=status, predicted_ratio=value,
                      formula_used=FORMULAS[ratio], items=enriched)
