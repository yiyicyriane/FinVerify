"""Step 3: match XBRL facts to each target filing and build ground_truth.json.

Fully deterministic, no LLM involved. For every (filing x ratio) pair:
  1. resolve each required input concept against that filing's XBRL facts
  2. call the shared calculator (agent/calculator.py)
  3. write one record with inputs, matched keys, per-input status and the ratio

Matching key: accession_number + concept + period_end + unit
  * instant concepts (balance sheet): record has no "start"
  * duration concepts (income statement): record must span a full fiscal year
    (350-380 days between start and end). This replaces passing an exact
    period_start, which is hard to know in advance for 52/53-week fiscal years,
    and it excludes quarterly records that share the same end date.
  * only records with form == "10-K" are used (10-Q facts live in the same arrays)

Hit-count rule (per candidate tag):
  0 hits                    -> mapping_unavailable (try the next candidate tag)
  >=1 hits, one value       -> available (duplicates of the same number are not ambiguity)
  >=2 hits, different values -> ambiguous_context (never guess which one is right)

Beginning-of-period values (ROA/ROE) come from the SAME accession number: a
10-K reports the prior year-end as a comparative column. The prior period end
is the latest instant date earlier than the current period end in that filing.

Do NOT use the "fy" field to pick periods: every record in a filing carries the
filing's fy, including comparative prior-year records.

Values are kept in raw USD (as reported in XBRL). Scale normalisation to the
units shown in the HTML filing (e.g. "in millions") is the verifier's job.

Usage (from repo root):
    python -m data.match_ground_truth
"""

import json
from datetime import date
from typing import Optional

from agent.calculator import RATIOS
from data.fetch_xbrl import load_company_facts
from data.sec_client import GROUND_TRUTH_PATH, MANIFEST_PATH

UNIT = "USD"
ANNUAL_DAYS = range(350, 381)

# Logical concept -> ordered candidate us-gaap tags + period type.
# Order matters: the first tag that fully matches the target filing is used.
# Revenue prefers "Revenues" (total revenues line) over the ASC 606 contract-revenue tag.
CONCEPTS = {
    "current_assets": {"tags": ["AssetsCurrent"], "period_type": "instant"},
    "current_liabilities": {"tags": ["LiabilitiesCurrent"], "period_type": "instant"},
    "revenue": {
        "tags": ["Revenues",
                 "RevenueFromContractWithCustomerExcludingAssessedTax",
                 "SalesRevenueNet"],
        "period_type": "duration",
    },
    "cost_of_revenue": {
        "tags": ["CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold"],
        "period_type": "duration",
    },
    "net_income": {"tags": ["NetIncomeLoss"], "period_type": "duration"},
    "total_assets": {"tags": ["Assets"], "period_type": "instant"},
    "equity": {"tags": ["StockholdersEquity"], "period_type": "instant"},
}

# Calculator input name -> (logical concept, which period).
INPUTS = {
    "current_assets": ("current_assets", "end"),
    "current_liabilities": ("current_liabilities", "end"),
    "revenue": ("revenue", "end"),
    "cost_of_revenue": ("cost_of_revenue", "end"),
    "net_income": ("net_income", "end"),
    "total_assets_end": ("total_assets", "end"),
    "total_assets_begin": ("total_assets", "begin"),
    "equity_end": ("equity", "end"),
    "equity_begin": ("equity", "begin"),
}


def flatten_concept_facts(company_facts: dict, tag: str) -> list:
    """Flatten facts["us-gaap"][tag]["units"][unit] into one list.

    In the raw API response the unit is the outer dict key, not a field of each
    record, so it is written into every record explicitly here.
    """
    concept_data = company_facts.get("facts", {}).get("us-gaap", {}).get(tag)
    if concept_data is None:
        return []
    flat = []
    for unit_key, records in concept_data.get("units", {}).items():
        for r in records:
            flat.append({**r, "concept": tag, "unit": unit_key})
    return flat


def _is_annual(record: dict) -> bool:
    """True if a duration record spans roughly one fiscal year."""
    if "start" not in record:
        return False
    days = (date.fromisoformat(record["end"]) - date.fromisoformat(record["start"])).days
    return days in ANNUAL_DAYS


def match_concept(flat_facts: list, accession_number: str, period_end: str,
                  unit: str, period_type: str) -> tuple:
    """Match one tag's flattened facts to a filing/period. Returns (value, status, record)."""
    candidates = [
        f for f in flat_facts
        if f["accn"] == accession_number
        and f["end"] == period_end
        and f["unit"] == unit
        and f.get("form") == "10-K"
        and (("start" not in f) if period_type == "instant" else _is_annual(f))
    ]
    if not candidates:
        return None, "mapping_unavailable", None
    values = {c["val"] for c in candidates}
    if len(values) == 1:
        return candidates[0]["val"], "available", candidates[0]
    return None, "ambiguous_context", None


def match_with_fallback(company_facts: dict, concept_key: str, accession_number: str,
                        period_end: str) -> dict:
    """Try each candidate tag of a logical concept with a full match, in order.

    An ambiguous_context result is kept and not overwritten by a later tag's
    mapping_unavailable. If a later tag also matches with a DIFFERENT value,
    it is recorded under "conflicting_tags" for manual review.
    """
    spec = CONCEPTS[concept_key]
    chosen = None
    fallback_status = "mapping_unavailable"
    conflicts = {}
    for tag in spec["tags"]:
        flat = flatten_concept_facts(company_facts, tag)
        value, status, record = match_concept(flat, accession_number, period_end,
                                              UNIT, spec["period_type"])
        if status == "available":
            if chosen is None:
                chosen = {"tag_used": tag, "value": value,
                          "period_start": record.get("start")}
            elif value != chosen["value"]:
                conflicts[tag] = value
        elif status == "ambiguous_context" and chosen is None:
            fallback_status = "ambiguous_context"

    result = {
        "concept": concept_key,
        "tag_used": None,
        "value": None,
        "unit": UNIT,
        "period_type": spec["period_type"],
        "period_start": None,
        "period_end": period_end,
        "status": fallback_status,
    }
    if chosen is not None:
        result.update(chosen, status="available")
    if conflicts:
        result["conflicting_tags"] = conflicts
    return result


def find_prior_period_end(company_facts: dict, tag: str, accession_number: str,
                          period_end: str) -> Optional[str]:
    """Latest instant date < period_end reported for this tag inside the same 10-K."""
    ends = {
        f["end"] for f in flatten_concept_facts(company_facts, tag)
        if f["accn"] == accession_number and f.get("form") == "10-K"
        and "start" not in f and f["end"] < period_end
    }
    return max(ends) if ends else None


def fiscal_year_of(company_facts: dict, accession_number: str) -> Optional[int]:
    """Fiscal year label of the filing itself (e.g. NVDA FY ending Jan 2026 -> 2026)."""
    for tag in ("AssetsCurrent", "Assets"):
        for f in flatten_concept_facts(company_facts, tag):
            if f["accn"] == accession_number and f.get("form") == "10-K":
                return f.get("fy")
    return None


def resolve_inputs(company_facts: dict, accession_number: str, period_end: str) -> dict:
    """Resolve every calculator input for one filing. Returns {input_name: input_record}."""
    resolved = {}
    for input_name, (concept_key, which) in INPUTS.items():
        if which == "end":
            rec = match_with_fallback(company_facts, concept_key, accession_number, period_end)
        else:
            # Beginning value: prior year-end inside the same filing, same tag as the end value.
            end_rec = resolved.get(f"{concept_key}_end") or match_with_fallback(
                company_facts, concept_key, accession_number, period_end)
            tags = [end_rec["tag_used"]] if end_rec["tag_used"] else CONCEPTS[concept_key]["tags"]
            prior_end = None
            for tag in tags:
                prior_end = find_prior_period_end(company_facts, tag, accession_number, period_end)
                if prior_end:
                    break
            if prior_end is None:
                rec = {**end_rec, "tag_used": None, "value": None, "period_end": None,
                       "status": "mapping_unavailable"}
            else:
                rec = match_with_fallback(company_facts, concept_key, accession_number, prior_end)
        rec["input"] = input_name
        resolved[input_name] = rec
    return resolved


def build_records(filing: dict, company_facts: Optional[dict]) -> list:
    """Build one ground-truth record per ratio for a single filing."""
    records = []
    if company_facts is None:
        resolved, fy = {}, None
    else:
        resolved = resolve_inputs(company_facts, filing["accession_number"], filing["report_date"])
        fy = fiscal_year_of(company_facts, filing["accession_number"])

    for ratio_name, (func, input_names) in RATIOS.items():
        if company_facts is None:
            inputs = [{"input": n, "status": "fetch_failed"} for n in input_names]
        else:
            inputs = [resolved[n] for n in input_names]
        statuses = [i["status"] for i in inputs]
        gt_status = next((s for s in statuses if s != "available"), "available")
        if gt_status == "available":
            value, calc_status = func(*[i["value"] for i in inputs])
        else:
            value, calc_status = None, "missing_input"
        records.append({
            "filing_id": filing["filing_id"],
            "company": filing["company"],
            "cik": filing["cik"],
            "fiscal_year": fy,
            "accession_number": filing["accession_number"],
            "period_end": filing["report_date"],
            "ratio": ratio_name,
            "inputs": inputs,
            "ground_truth_status": gt_status,
            "ground_truth_ratio": value,
            "calculator_status": calc_status,
        })
    return records


def main():
    manifest = json.loads(MANIFEST_PATH.read_text())
    all_records = []
    for filing in manifest:
        try:
            facts = load_company_facts(filing["cik"])
        except RuntimeError as err:
            print(f"[fetch_failed] {filing['filing_id']}: {err}")
            facts = None
        records = build_records(filing, facts)
        all_records.extend(records)
        summary = "  ".join(
            f"{r['ratio']}={r['ground_truth_ratio']:.4f}" if r["ground_truth_ratio"] is not None
            else f"{r['ratio']}={r['ground_truth_status']}"
            for r in records)
        print(f"{filing['filing_id']:24} {summary}")

    GROUND_TRUTH_PATH.write_text(json.dumps(all_records, indent=2) + "\n")
    n_ok = sum(r["ground_truth_status"] == "available" for r in all_records)
    print(f"\nWrote {len(all_records)} records ({n_ok} available) to data/ground_truth.json")


if __name__ == "__main__":
    main()
