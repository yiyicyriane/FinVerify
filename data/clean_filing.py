"""Data cleaning: 10-K HTML -> the three financial statements as visible-text JSON.

This produces the only input Agent versions A/B/C ever read. It keeps the balance
sheet, income statement and cash flow statement (rows/columns, unit note, both
periods) and nothing else: no MD&A, no narrative with pre-computed ratios, no
Inline XBRL.

Layer 1, Inline XBRL removal (while parsing):
  * <ix:header> and anything styled display:none / visibility:hidden are dropped
    with their content (this is where hidden facts, contexts and units live)
  * every other ix:* tag is unwrapped: visible text kept, attributes gone
  * only visible text is ever read; no attribute value reaches the output
  * tables are located by heading text and table structure only, never by XBRL
    tag names (that would be indirect leakage)

Layer 2, statement extraction:
  * headings are matched loosely: "Consolidated" optional (MSFT: "INCOME STATEMENTS"),
    "Statements of Earnings" (HD), "Financial Position", comprehensive income excluded
  * the candidate table must be plausible: >= 5 rows, statement keywords, >= 2 years
    in the header rows; a big non-matching table or another statement heading stops
    the search, so a table of contents or index never leads to the wrong table
  * a statement split over two <table>s is joined when the first part is incomplete
  * unit note ("In millions ...") is searched in the heading, the table's own top
    rows, the text between heading and table, then just before the heading
  * "$" / ")" / "%" cells are merged into the adjacent number, and header cells that
    span several columns (e.g. a year over "$ | 1,234 | )") are moved onto the
    column that holds the numbers, so years and values line up
  * Workiva's self-closing <td /> spacers are kept, and adjacent columns that never hold
    values in the same row are merged, so each period ends up in exactly one column
  * sentence-like headings and "% of revenue" tables (NVDA MD&A) are rejected
  * only columns that are completely empty are dropped (never a single empty cell)

Output: data/cleaned_filings/<filing_id>.json (filing_id as in data/ground_truth.json)
Raw HTML cache: data/raw/filings/<filing_id>.htm (gitignored)

Checks per filing (non-zero exit if any fails):
  * all three statements found, unit_note non-empty, >= 2 periods in each header
  * no XBRL traces: us-gaap, ix:, xbrli, dei:, contextref, unitref, xmlns all 0
  * every ground-truth input value, in millions (e.g. 147,957), appears in the
    right column: current period in column 1, prior period (beginning assets /
    equity) in column 2; every row has label + one column per period, newest first
  * warning (not failure) if a row label looks like a ready-made ratio

Usage (from repo root):
    export SEC_USER_AGENT="FinVerify-GroundTruth your_team_email@example.com"
    python -m data.clean_filing                          # all 14 filings
    python -m data.clean_filing --only AAPL_2025-09-27_10K --preview
    python -m data.clean_filing --check-only             # re-check existing JSON
    python -m data.clean_filing --refresh                # re-download raw HTML

Standard library only.
"""

import argparse
import json
import re
import sys
from html.parser import HTMLParser

from data.sec_client import (GROUND_TRUTH_PATH, MANIFEST_PATH, ROOT,
                             filing_html_cache_path, get_bytes)

CLEANED_DIR = ROOT / "data" / "cleaned_filings"
STATEMENTS = ("balance_sheet", "income_statement", "cash_flow_statement")

# ---------------------------------------------------------------------------
# Layer 1: parse visible content into a flat sequence of text blocks and tables
# ---------------------------------------------------------------------------

DROP_WITH_CONTENT = {"ix:header", "head", "script", "style", "noscript", "title", "svg"}
VOID_TAGS = {"br", "hr", "img", "meta", "link", "input", "col", "area", "base", "wbr", "source"}
BLOCK_TAGS = {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "br", "hr",
              "table", "tr", "center", "section", "body"}
HIDDEN_STYLE = re.compile(r"display\s*:\s*none|visibility\s*:\s*hidden", re.I)


def _norm(text: str) -> str:
    return re.sub(r"[\s\u00a0\u200b]+", " ", text).strip()


class _Linearizer(HTMLParser):
    """Turns the document into [("text", str) | ("table", [[cell, ...], ...])].

    A cell is (text, colspan, rowspan). Nested tables are flattened into the
    enclosing cell's text (rare in 10-K statements).
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.items = []
        self.skip = 0
        self.buf = []                 # text outside tables
        self.tables = []              # stack of tables being built
        self.row = None
        self.cell = None              # [text_parts, colspan, rowspan]

    # -- helpers --
    def _flush_text(self):
        text = _norm("".join(self.buf))
        if text:
            self.items.append(("text", text))
        self.buf = []

    def _emit(self, s):
        if self.cell is not None:
            self.cell[0].append(s)
        elif not self.tables:
            self.buf.append(s)

    @staticmethod
    def _span(attrs, name):
        try:
            return max(1, int(dict(attrs).get(name) or 1))
        except ValueError:
            return 1

    # -- parser callbacks --
    def handle_starttag(self, tag, attrs):
        if self.skip:
            if tag not in VOID_TAGS:
                self.skip += 1
            return
        if tag in DROP_WITH_CONTENT or HIDDEN_STYLE.search(dict(attrs).get("style") or ""):
            if tag not in VOID_TAGS:
                self.skip = 1
            return
        if tag == "table":
            if self.cell is not None:          # nested table: keep as cell text
                self.tables.append(None)
                return
            if not self.tables:
                self._flush_text()
            self.tables.append([])
        elif tag == "tr" and self.tables and self.tables[-1] is not None:
            self.row = []
        elif tag in ("td", "th") and self.row is not None and self.tables[-1] is not None:
            self.cell = [[], self._span(attrs, "colspan"), self._span(attrs, "rowspan")]
        elif tag in BLOCK_TAGS:
            self._emit(" " if self.cell is not None else "\n")

    def handle_startendtag(self, tag, attrs):
        if self.skip:
            return
        if tag in ("td", "th"):
            # Workiva writes empty spacer cells as <td />; dropping them shifts columns
            if self.row is not None and self.cell is None and self.tables and self.tables[-1] is not None:
                self.row.append(("", self._span(attrs, "colspan"), self._span(attrs, "rowspan")))
        elif tag in ("br", "hr"):
            self._emit(" " if self.cell is not None else "\n")

    def handle_endtag(self, tag):
        if self.skip:
            if tag not in VOID_TAGS:
                self.skip -= 1
            return
        if tag in ("td", "th") and self.cell is not None and self.tables[-1] is not None:
            parts, cs, rs = self.cell
            self.row.append((_norm("".join(parts)), cs, rs))
            self.cell = None
        elif tag == "tr" and self.row is not None and self.tables and self.tables[-1] is not None:
            self.tables[-1].append(self.row)
            self.row = None
        elif tag == "table" and self.tables:
            t = self.tables.pop()
            if t is None:
                return
            if self.row:                       # unterminated last row
                t.append(self.row)
                self.row = None
            if not self.tables:
                self.items.append(("table", t))
        elif tag in BLOCK_TAGS and not self.tables:
            self.buf.append("\n")
            if tag in ("p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li"):
                self._flush_text()

    def handle_data(self, data):
        if not self.skip:
            self._emit(data)

    def close(self):
        super().close()
        self._flush_text()


def linearize(html_text: str) -> list:
    p = _Linearizer()
    p.feed(html_text)
    p.close()
    return p.items


# ---------------------------------------------------------------------------
# Table -> clean rows
# ---------------------------------------------------------------------------

PREFIX_SYMBOL = re.compile(r"^[$€£¥]$")
SUFFIX_SYMBOL = re.compile(r"^\)?\s*%?\)?$")       # ")", "%", ")%"
NUMERIC = re.compile(r"^[$€£¥]?\s*\(?-?\s*[\d,]*\.?\d+\s*\)?%?$|^[—–-]+$")


def table_to_rows(table: list) -> list:
    # 1. expand colspan/rowspan into a grid, remembering spanned cells
    grid, spans, carry = [], [], {}            # carry: col -> (rows_left, text)
    for r, tr in enumerate(table):
        row, col, ci = [], 0, 0
        while ci < len(tr) or col in carry:
            if col in carry:
                left, text = carry[col]
                row.append(text)
                if left <= 1:
                    del carry[col]
                else:
                    carry[col] = (left - 1, text)
                col += 1
                continue
            text, cs, rs = tr[ci]
            for i in range(cs):
                row.append(text if i == 0 else "")
                if rs > 1:
                    carry[col + i] = (rs - 1, text if i == 0 else "")
            if cs > 1 and text:
                spans.append((r, col, cs))
            col += cs
            ci += 1
        grid.append(row)
    if not grid:
        return []
    width = max(len(r) for r in grid)
    grid = [r + [""] * (width - len(r)) for r in grid]

    # 2. merge "$" into the next non-empty cell and ")" / "%" into the previous one
    for row in grid:
        for c, v in enumerate(row):
            if PREFIX_SYMBOL.match(v):
                nxt = next((k for k in range(c + 1, width) if row[k]), None)
                if nxt is not None and NUMERIC.match(row[nxt]):
                    row[nxt] = v + row[nxt]
                    row[c] = ""
            elif v and SUFFIX_SYMBOL.match(v):
                prv = next((k for k in range(c - 1, -1, -1) if row[k]), None)
                if prv is not None and NUMERIC.match(row[prv] + v):
                    row[prv] = row[prv] + v
                    row[c] = ""

    # 3. move spanned header text onto the column (within its span) holding the data
    filled = [sum(1 for row in grid if row[c]) for c in range(width)]
    for r, c0, cs in spans:
        text = grid[r][c0]
        if not text or (NUMERIC.match(text) and not YEAR_RE.fullmatch(text.strip())):
            continue
        best = max(range(c0, c0 + cs), key=lambda c: (filled[c] - (1 if c == c0 else 0), -c))
        if best != c0 and not grid[r][best]:
            grid[r][best], grid[r][c0] = text, ""

    # 4. merge adjacent columns that never hold values in the same row. Workiva puts
    #    "$ | 10,605" in two cells on some rows and "51,951" in one colspan=2 cell on
    #    others, so the same period would otherwise be split over two columns.
    groups = []
    for c in range(width):
        if groups and not any(row[c] and any(row[g] for g in groups[-1]) for row in grid):
            groups[-1].append(c)
        else:
            groups.append([c])
    rows = [[next((row[g] for g in grp if row[g]), "") for grp in groups] for row in grid]

    # 5. drop columns that are completely empty, then fully empty rows
    keep = [c for c in range(len(groups)) if any(row[c] for row in rows)]
    rows = [[row[c] for c in keep] for row in rows]
    return [r for r in rows if any(r)]


# ---------------------------------------------------------------------------
# Layer 2: locate the three statements
# ---------------------------------------------------------------------------

HEADING_PATTERNS = {
    "balance_sheet": re.compile(
        r"\bbalance\s+sheets?\b|\bstatements?\s+of\s+financial\s+(position|condition)\b", re.I),
    "income_statement": re.compile(
        r"\bstatements?\s+of\s+(operations|income|earnings)\b|\bincome\s+statements?\b", re.I),
    "cash_flow_statement": re.compile(
        r"\bstatements?\s+of\s+cash\s+flows?\b|\bcash\s+flows?\s+statements?\b", re.I),
}
# Other statements: their heading ends the search for a table (but is never a match).
OTHER_STATEMENT = re.compile(
    r"comprehensive\s+income|statements?\s+of\s+(stockholders|shareholders|changes\s+in)"
    r"|(stockholders|shareholders)['’]?\s+equity\s+statements?", re.I)
REJECT_HEADING = re.compile(r"\bparent\b|\bregistrant\b|\bcondensed\b|\bschedule\b|\bnotes?\s+to\b", re.I)

KEYWORDS = {
    "balance_sheet": ("total assets", "total liabilities", "stockholders", "shareholders"),
    "income_statement": ("net income", "net earnings", "net loss", "revenue", "net sales", "cost of"),
    # MSFT labels its sections "Operations / Financing / Investing", not "... activities"
    "cash_flow_statement": ("operating activities", "investing", "financing"),
}
# A statement missing all of these is treated as incomplete (split across two tables).
COMPLETE_IF = {
    "balance_sheet": ("stockholders", "shareholders", "total equity"),
    "income_statement": ("net income", "net earnings", "net loss"),
    "cash_flow_statement": ("financing",),
}
UNIT_RE = re.compile(r"\(?\s*(?:\$|u\.?s\.?\s+dollars\s+|dollars\s+)?in\s+(?:millions|thousands|billions)\b[^)]{0,200}\)?", re.I)
YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
MAX_HEADING_LEN = 120
# Sentences that merely mention a statement (NVDA MD&A: "The following table sets forth ...
# certain items in our Consolidated Statements of Income expressed as a percentage of revenue.")
SENTENCE = re.compile(r"[.:;]$|\bthe following\b|\bexpressed as\b|\bpercentage\b|\bselected\b"
                      r"|\bsummary\b|\bhighlights\b|\binclude[sd]?\b|\bwe\b|\bour\b", re.I)


def statement_heading(text: str):
    """Return the statement key a short text block is the heading of, or None."""
    if len(text) > MAX_HEADING_LEN or REJECT_HEADING.search(text) or SENTENCE.search(text):
        return None
    if OTHER_STATEMENT.search(text):
        return "other"
    for key, pat in HEADING_PATTERNS.items():
        if pat.search(text):
            return key
    return None


def header_rows(rows: list) -> list:
    """Rows above the first row that carries a number outside the label column."""
    out = []
    for r in rows:
        if any(NUMERIC.match(c) and re.search(r"\d", c) and not YEAR_RE.fullmatch(c.strip())
               for c in r[1:]):
            break
        out.append(r)
    return out


def periods(rows: list) -> list:
    years = []
    for r in header_rows(rows):
        for y in YEAR_RE.findall(" ".join(r)):
            if y not in years:
                years.append(y)
    return years


PERCENT_CELL = re.compile(r"%|^\(?-?\d{1,3}\.\d\)?$")       # "55.6%", "28.9", "(0.1)"


def mostly_percent(rows: list) -> bool:
    """True for "% of revenue" style tables, which hand the LLM a ready-made margin."""
    nums = [c for r in rows for c in r[1:] if c and NUMERIC.match(c) and re.search(r"\d", c)
            and not YEAR_RE.fullmatch(c.strip())]
    return bool(nums) and sum(bool(PERCENT_CELL.search(c)) for c in nums) > len(nums) / 2


def plausible(rows: list, key: str) -> bool:
    if len(rows) < 5 or mostly_percent(rows):
        return False
    text = " ".join(" ".join(r) for r in rows).lower()
    return any(k in text for k in KEYWORDS[key]) and len(periods(rows)) >= 2


def complete(rows: list, key: str) -> bool:
    text = " ".join(" ".join(r) for r in rows).lower()
    return any(k in text for k in COMPLETE_IF[key])


UNIT_STOP = re.compile(r"\s(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d"
                       r"|\s(?:fiscal|year|years|as of|at)\b|\s(?:19|20)\d{2}\b", re.I)


def find_unit(heading: str, rows: list, between: list, before: list):
    for source in ([heading], [" ".join(r) for r in rows[:4]], between, list(reversed(before))):
        for text in source:
            m = UNIT_RE.search(text)
            if m:
                unit = m.group(0)
                stop = UNIT_STOP.search(unit)      # HD: "in millions, ... February 1, 2026 ..."
                return _norm(unit[:stop.start()] if stop else unit)
    return None


def locate_statements(items: list) -> dict:
    found = {}
    for i, (kind, text) in enumerate(items):
        if kind != "text":
            continue
        key = statement_heading(text)
        if key in (None, "other") or key in found:
            continue
        between, tables_seen = [], 0
        for j in range(i + 1, min(len(items), i + 40)):
            kind_j, val_j = items[j]
            if kind_j == "text":
                if statement_heading(val_j):
                    break                     # next statement starts: wrong place
                between.append(val_j)
                continue
            tables_seen += 1
            rows = table_to_rows(val_j)
            if plausible(rows, key):
                raw_tables = [val_j]
                # join a continuation table if the statement is split
                if not complete(rows, key):
                    for k in range(j + 1, min(len(items), j + 6)):
                        if items[k][0] == "text":
                            if statement_heading(items[k][1]) not in (None, key):
                                break
                            continue
                        more = table_to_rows(items[k][1])
                        if len(more) >= 3:
                            rows = rows + more
                            raw_tables.append(items[k][1])
                        break
                found[key] = {
                    "heading_matched": text,
                    "unit_note": find_unit(text, rows, between,
                                           [t for kd, t in items[max(0, i - 3):i] if kd == "text"]),
                    "periods": periods(rows),
                    "tables_joined": len(raw_tables),
                    "rowspan_cells": sum(1 for t in raw_tables for tr in t for _, _, rs in tr if rs > 1),
                    "rows": rows,
                }
                break
            if len(rows) >= 5 or tables_seen >= 5:
                break                         # big unrelated table: stop looking here
    return found


# ---------------------------------------------------------------------------
# Output, checks, CLI
# ---------------------------------------------------------------------------

LEAK_PATTERNS = ["us-gaap", "ix:", "xbrli", "dei:", "contextref", "unitref", "xmlns"]
RATIO_LABEL = re.compile(r"\bratio\b|margin\s*%|margin\s+percentage|return\s+on\s+(assets|equity)", re.I)


def _decode(raw: bytes) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace")


def clean_filing(raw: bytes, row: dict, fiscal_year) -> dict:
    statements = locate_statements(linearize(_decode(raw)))
    return {
        "filing_id": row["filing_id"],
        "company": row["company"],
        "cik": row["cik"],
        "fiscal_year": fiscal_year,
        "accession_number": row["accession_number"],
        "period_end": row["report_date"],
        "source_url": row["url"],
        "statements": {k: statements.get(k) for k in STATEMENTS},
    }


def to_text(doc: dict) -> str:
    """Plain-text rendering of the three statements (what a prompt would contain)."""
    out = []
    for key in STATEMENTS:
        st = doc["statements"].get(key)
        if not st:
            continue
        out.append(f"## {st['heading_matched']}")
        if st["unit_note"]:
            out.append(st["unit_note"])
        out.extend(" | ".join(r) for r in st["rows"])
        out.append("")
    return "\n".join(out)


def expected_millions(ground_truth: list) -> tuple:
    """({filing_id: {(input, period_end, "147,957")}}, {filing_id: fiscal_year})"""
    vals, fy = {}, {}
    for rec in ground_truth:
        fy[rec["filing_id"]] = rec.get("fiscal_year")
        for inp in rec["inputs"]:
            v = inp.get("value")
            if v is None or abs(v) % 1_000_000:
                continue
            vals.setdefault(rec["filing_id"], set()).add(
                (inp["input"], inp["period_end"], f"{abs(v) // 1_000_000:,}"))
    return vals, fy


def _num(cell: str) -> str:
    return re.sub(r"[$€£¥()%\s]", "", cell)


def locate_values(doc: dict) -> dict:
    """{"147,957": [(statement, row_label, column_index), ...]} for every value cell."""
    where = {}
    for key in STATEMENTS:
        st = doc["statements"].get(key)
        for r in (st or {}).get("rows", []):
            for col, c in enumerate(r[1:], start=1):
                if c:
                    where.setdefault(_num(c), []).append((key, r[0], col))
    return where


def expected_column(period_end: str, expected: set) -> int:
    """1 = current-period column, 2 = prior-period column (filings list newest first)."""
    current = max(p for _, p, _ in expected)
    return 1 if period_end == current else 2


def check(doc: dict, expected: set) -> tuple:
    problems, warnings = [], []
    low = json.dumps(doc["statements"]).lower()
    for pat in LEAK_PATTERNS:
        if pat in low:
            problems.append(f"leak: '{pat}' appears {low.count(pat)} times")
    for key in STATEMENTS:
        st = doc["statements"].get(key)
        if not st:
            problems.append(f"{key}: not found")
            continue
        if not st["unit_note"]:
            problems.append(f"{key}: unit_note missing")
        if len(st["periods"]) < 2:
            problems.append(f"{key}: fewer than 2 periods in header {st['periods']}")
        elif st["periods"] != sorted(st["periods"], reverse=True):
            problems.append(f"{key}: periods not newest-first {st['periods']}")
        widths = {len(r) for r in st["rows"]}
        if widths != {len(st["periods"]) + 1}:
            problems.append(f"{key}: row widths {sorted(widths)}, expected label + "
                            f"{len(st['periods'])} period columns")
        for r in st["rows"]:
            if RATIO_LABEL.search(r[0]):
                warnings.append(f"{key}: row looks like a ratio: {r[0]!r}")
    where = locate_values(doc)
    for inp, period_end, num in sorted(expected):
        hits = where.get(num, [])
        col = expected_column(period_end, expected)
        if not hits:
            problems.append(f"GT value not in statement rows: {inp} @ {period_end} = {num} (millions)")
        elif not any(c == col for _, _, c in hits):
            got = sorted({c for _, _, c in hits})
            problems.append(f"GT value in wrong column: {inp} @ {period_end} = {num} "
                            f"expected column {col}, found in {got}")
    return problems, warnings


def main():
    ap = argparse.ArgumentParser(description="10-K HTML -> three-statement JSON.")
    ap.add_argument("--only", nargs="*", help="filing_id(s) to process")
    ap.add_argument("--refresh", action="store_true", help="re-download raw HTML")
    ap.add_argument("--check-only", action="store_true", help="only check existing JSON")
    ap.add_argument("--preview", action="store_true", help="print the plain-text rendering")
    args = ap.parse_args()

    manifest = json.loads(MANIFEST_PATH.read_text())
    expected, fiscal_years = expected_millions(json.loads(GROUND_TRUTH_PATH.read_text()))
    if args.only:
        unknown = set(args.only) - {m["filing_id"] for m in manifest}
        if unknown:
            sys.exit(f"unknown filing_id(s): {sorted(unknown)}")
        manifest = [m for m in manifest if m["filing_id"] in set(args.only)]

    CLEANED_DIR.mkdir(parents=True, exist_ok=True)
    failed = 0
    for row in manifest:
        fid = row["filing_id"]
        out_path = CLEANED_DIR / f"{fid}.json"
        if args.check_only:
            if not out_path.exists():
                print(f"[MISS] {fid}: {out_path.relative_to(ROOT)} not found")
                failed += 1
                continue
            doc = json.loads(out_path.read_text())
        else:
            cache = filing_html_cache_path(fid)
            if args.refresh and cache.exists():
                cache.unlink()
            doc = clean_filing(get_bytes(row["url"], cache_path=cache), row, fiscal_years.get(fid))
            out_path.write_text(json.dumps(doc, indent=1, ensure_ascii=False))

        problems, warnings = check(doc, expected.get(fid, set()))
        sts = doc["statements"]
        summary = ", ".join(f"{k.split('_')[0]}:{len(sts[k]['rows']) if sts.get(k) else '-'}"
                            for k in STATEMENTS)
        print(f"[{'FAIL' if problems else ' OK '}] {fid} (rows {summary})")
        for p in problems:
            print(f"       - {p}")
        for w in warnings:
            print(f"       ! {w}")
        failed += bool(problems)
        if args.preview:
            print(to_text(doc))

    print(f"\n{len(manifest) - failed}/{len(manifest)} filings passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
