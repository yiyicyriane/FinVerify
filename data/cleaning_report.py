"""Build the data-cleaning spot-check record: docs/cleaning_verification.md.

Everything the code can verify is filled in automatically from
data/cleaned_filings/*.json; what needs a human looking at the original 10-K is
left as an unticked box. Run after `python -m data.clean_filing`:

    python -m data.cleaning_report

Re-running overwrites the file, so tick the manual boxes only once the cleaned
data is final (or copy the ticks over).
"""

import json
from datetime import date

from data.clean_filing import (CLEANED_DIR, LEAK_PATTERNS, STATEMENTS,
                               check, expected_column, expected_millions, locate_values)
from data.sec_client import GROUND_TRUTH_PATH, MANIFEST_PATH, ROOT

OUT_PATH = ROOT / "docs" / "cleaning_verification.md"
POC = "AAPL_2025-09-27_10K"
SHORT = {"balance_sheet": "BS", "income_statement": "IS", "cash_flow_statement": "CF"}
INPUT_ORDER = ["current_assets", "current_liabilities", "revenue", "cost_of_revenue",
               "net_income", "total_assets_end", "total_assets_begin", "equity_end", "equity_begin"]


def tick(ok: bool) -> str:
    return "[x]" if ok else "[ ] **FAIL**"


def reconciliation(doc: dict, expected: set) -> list:
    """Markdown table: each ground-truth input vs. the cleaned row/column holding it."""
    where = locate_values(doc)
    lines = ["| GT input | Period | GT value (USD millions) | Cleaned row (statement) | Column | Match |",
             "|---|---|---|---|---|---|"]
    order = {k: i for i, k in enumerate(INPUT_ORDER)}
    for inp, period_end, num in sorted(expected, key=lambda e: (order.get(e[0], 99), e[1])):
        col = expected_column(period_end, expected)
        hits = [h for h in where.get(num, []) if h[2] == col] or where.get(num, [])
        if hits:
            st, label, got = hits[0]
            lines.append(f"| {inp} | {period_end} | {num} | {label} ({SHORT[st]}) | {got} "
                         f"| {tick(got == col)} |")
        else:
            lines.append(f"| {inp} | {period_end} | {num} | not found | - | [ ] **FAIL** |")
    return lines


def filing_section(doc: dict, expected: set) -> list:
    sts = doc["statements"]
    problems, warnings = check(doc, expected)
    out = [f"### {doc['filing_id']}", "",
           f"Accession {doc['accession_number']} · [10-K]({doc['source_url']})", "",
           "| Statement | Heading matched | Unit note | Periods | Rows | Tables | rowspan cells |",
           "|---|---|---|---|---|---|---|"]
    for key in STATEMENTS:
        st = sts.get(key)
        if not st:
            out.append(f"| {SHORT[key]} | **not found** | | | | | |")
            continue
        out.append(f"| {SHORT[key]} | {st['heading_matched']} | {st['unit_note'] or '**missing**'} "
                   f"| {', '.join(st['periods'])} | {len(st['rows'])} | {st.get('tables_joined', 1)} "
                   f"| {st.get('rowspan_cells', '?')} |")
    out += ["", *reconciliation(doc, expected), ""]

    rowspans = sum((st or {}).get("rowspan_cells", 0) for st in sts.values())
    joined = [SHORT[k] for k, st in sts.items() if st and st.get("tables_joined", 1) > 1]
    out += [
        "Automated (from `check`):",
        "",
        f"- {tick(all(sts.get(k) for k in STATEMENTS))} 1. All three statements found",
        f"- {tick(all(st and st['unit_note'] for st in sts.values()))} 3/9. Unit note kept and non-empty",
        f"- {tick(not any('period' in p or 'width' in p or 'column' in p for p in problems))} "
        "4/7. One column per period, newest first; every GT value in its period's column",
        f"- {tick(not any('leak' in p for p in problems))} 5. No XBRL traces "
        f"({', '.join(LEAK_PATTERNS)})",
        f"- {tick(not any('GT value' in p for p in problems))} 6. Matches ground truth (table above)",
        f"- {tick(not warnings)} 8. No row label that looks like a ready-made ratio"
        + ("" if not warnings else ": " + "; ".join(warnings)),
        "- [x] 10. Only fully empty columns are dropped (by construction), no data column removed",
        "",
        "Manual (open the 10-K at the link above):",
        "",
        "- [ ] 2. Row count of each statement matches the original; no rows missing in the middle",
        "- [ ] 7. Pick 2 rows with an empty cell (e.g. a \"—\" or blank year): values stay in their year's column",
        ("- [x] 11. No rowspan in these statements (N/A)" if rowspans == 0 else
         f"- [ ] 11. {rowspans} rowspan cell(s): check those rows line up"),
    ]
    if joined:
        out.append(f"- [ ] Statement joined from two tables ({', '.join(joined)}): check the seam, "
                   "no duplicated or missing rows")
    out += ["", "Checked by: ________  Date: ________  Notes:", "", "---", ""]
    return out


def main():
    manifest = json.loads(MANIFEST_PATH.read_text())
    expected, _ = expected_millions(json.loads(GROUND_TRUTH_PATH.read_text()))
    docs, missing = [], []
    for row in manifest:
        path = CLEANED_DIR / f"{row['filing_id']}.json"
        if path.exists():
            docs.append(json.loads(path.read_text()))
        else:
            missing.append(row["filing_id"])

    summary = ["| Filing | BS / IS / CF rows | Periods (BS / IS / CF) | GT values in right column "
               "| rowspan | Auto checks |", "|---|---|---|---|---|---|"]
    for doc in docs:
        exp = expected.get(doc["filing_id"], set())
        problems, warnings = check(doc, exp)
        sts = doc["statements"]
        rows = " / ".join(str(len(sts[k]["rows"])) if sts.get(k) else "-" for k in STATEMENTS)
        pers = " / ".join(str(len(sts[k]["periods"])) if sts.get(k) else "-" for k in STATEMENTS)
        wrong = sum(1 for p in problems if "GT value" in p)
        rs = sum((st or {}).get("rowspan_cells", 0) for st in sts.values())
        status = "pass" if not problems else f"**{len(problems)} problem(s)**"
        if warnings:
            status += f", {len(warnings)} warning(s)"
        summary.append(f"| {doc['filing_id']} | {rows} | {pers} | {len(exp) - wrong}/{len(exp)} "
                       f"| {rs} | {status} |")

    lines = [
        "# Data cleaning verification record",
        "",
        "Generated by `python -m data.cleaning_report` on "
        f"{date.today().isoformat()} from `data/cleaned_filings/*.json`. Items numbered as in the",
        "cleaning guide's spot-check list. Automated items are filled in; manual items need",
        "someone to open the original 10-K. Re-running overwrites this file.",
        "",
        "Why this matters: a row the HTML parser drops is not the agent's fault, but it would",
        "count as an agent error and contaminate the A/B/C comparison.",
        "",
        "## Summary",
        "",
        *summary,
        "",
    ]
    if missing:
        lines += [f"Not cleaned yet: {', '.join(missing)}", ""]

    poc = next((d for d in docs if d["filing_id"] == POC), None)
    if poc:
        lines += [
            f"## Apple POC reconciliation ({POC})",
            "",
            "Same filing as the ground-truth POC in `docs/ground_truth_verification.md`. The",
            "cleaned statement shows millions; ground truth stores raw USD (147,957 = 147,957,000,000).",
            "",
            *reconciliation(poc, expected.get(POC, set())),
            "",
            "- [ ] Confirmed with the ground-truth owner",
            "",
        ]

    lines += ["## Per filing", ""]
    for doc in docs:
        lines += filing_section(doc, expected.get(doc["filing_id"], set()))

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text("\n".join(lines))
    print(f"wrote {OUT_PATH.relative_to(ROOT)} ({len(docs)} filings"
          + (f", {len(missing)} not cleaned yet)" if missing else ")"))


if __name__ == "__main__":
    main()
