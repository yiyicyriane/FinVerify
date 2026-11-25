"""Step 4: validate the Ground Truth Builder before anyone builds on it.

Three layers of checks:
  1. Known-answer tests on agent/calculator.py (toy inputs with obvious answers
     and edge cases: missing input, zero denominator, negative equity).
  2. Consistency checks on data/ground_truth.json:
       * every record has a ground_truth_status
       * no candidate-tag conflicts (two tags matched with different values)
       * Revenue - COGS equals the filing's own GrossProfit tag, where reported
       * ratios fall in a loose plausibility range
  3. Apple POC printout: the exact numbers to compare by hand against the
     Apple 10-K balance sheet / income statement (see docs/ground_truth_verification.md).

Exit code is non-zero if any automated check fails.

Usage (from repo root):
    python -m data.validate_gt
"""

import json
import math
import sys

from agent.calculator import current_ratio, gross_profit_margin, roa, roe
from data.fetch_xbrl import load_company_facts
from data.match_ground_truth import flatten_concept_facts, match_concept
from data.sec_client import GROUND_TRUTH_PATH

PLAUSIBLE = {
    "current_ratio": (0.1, 20.0),
    "gross_profit_margin": (-1.0, 1.0),
    "roa": (-1.0, 2.0),
    "roe": (-5.0, 10.0),
}

failures = []


def check(condition: bool, message: str):
    """Record a failed check without stopping the run."""
    if not condition:
        failures.append(message)
        print(f"  FAIL  {message}")


def close(a, b, tol=1e-9):
    return a is not None and b is not None and math.isclose(a, b, rel_tol=tol, abs_tol=tol)


def known_answer_tests():
    print("[1] Calculator known-answer tests")
    check(current_ratio(200, 100) == (2.0, "ok"), "current_ratio(200, 100) should be 2.0")
    check(close(gross_profit_margin(100, 60)[0], 0.4), "gross_profit_margin(100, 60) should be 0.4")
    check(close(roa(10, 90, 110)[0], 0.1), "roa(10, 90, 110) should use average assets = 100")
    check(close(roe(30, 100, 200)[0], 0.2), "roe(30, 100, 200) should use average equity = 150")
    check(current_ratio(200, 0) == (None, "undefined_division"), "zero liabilities -> undefined_division")
    check(gross_profit_margin(0, 10) == (None, "undefined_division"), "zero revenue -> undefined_division")
    check(roa(10, 0, 0) == (None, "undefined_division"), "zero average assets -> undefined_division")
    check(current_ratio(None, 100) == (None, "missing_input"), "None input -> missing_input")
    check(roe(10, None, 50) == (None, "missing_input"), "None equity -> missing_input")
    check(roe(10, -100, -50)[1] == "low_confidence_negative_equity", "negative equity is flagged")
    print(f"  done ({len(failures)} failure(s) so far)")


def consistency_checks(records: list):
    print("[2] ground_truth.json consistency checks")
    for r in records:
        tag = f"{r['filing_id']} {r['ratio']}"
        check("ground_truth_status" in r, f"{tag}: missing ground_truth_status")
        for i in r["inputs"]:
            check("conflicting_tags" not in i,
                  f"{tag}: {i['input']} candidate tags disagree {i.get('conflicting_tags')}")
        if r["ground_truth_ratio"] is not None:
            lo, hi = PLAUSIBLE[r["ratio"]]
            check(lo <= r["ground_truth_ratio"] <= hi,
                  f"{tag}: ratio {r['ground_truth_ratio']:.4f} outside plausible range [{lo}, {hi}]")

    # Cross-check Revenue - COGS against the company's own GrossProfit tag.
    n_checked = 0
    for r in records:
        if r["ratio"] != "gross_profit_margin" or r["ground_truth_status"] != "available":
            continue
        rev, cogs = (i["value"] for i in r["inputs"])
        facts = load_company_facts(r["cik"])
        gp, status, _ = match_concept(flatten_concept_facts(facts, "GrossProfit"),
                                      r["accession_number"], r["period_end"], "USD", "duration")
        if status == "available":
            n_checked += 1
            check(rev - cogs == gp,
                  f"{r['filing_id']}: revenue - cogs = {rev - cogs} but GrossProfit tag = {gp}")
    print(f"  GrossProfit cross-check ran on {n_checked} filing(s)")
    n_avail = sum(r["ground_truth_status"] == "available" for r in records)
    print(f"  coverage: {n_avail}/{len(records)} (filing x ratio) records available")


def apple_poc_printout(records: list):
    print("[3] Apple POC values to verify by hand against the 10-K (see docs/ground_truth_verification.md)")
    for r in records:
        if r["company"] != "AAPL":
            continue
        ratio = r["ground_truth_ratio"]
        print(f"  {r['filing_id']}  {r['ratio']:20} = "
              f"{ratio:.4f}" if ratio is not None else f"  {r['filing_id']}  {r['ratio']}: {r['ground_truth_status']}")
        for i in r["inputs"]:
            v = i["value"]
            shown = f"{v / 1e6:,.0f} (USD millions)" if v is not None else i["status"]
            print(f"      {i['input']:20} {i['period_end']}  {shown}   [{i['tag_used']}]")


def main():
    known_answer_tests()
    records = json.loads(GROUND_TRUTH_PATH.read_text())
    consistency_checks(records)
    apple_poc_printout(records)
    if failures:
        print(f"\n{len(failures)} check(s) FAILED")
        sys.exit(1)
    print("\nAll automated checks passed. Manual 10-K check record: docs/ground_truth_verification.md")


if __name__ == "__main__":
    main()
