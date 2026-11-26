# Ground truth manual verification record (Apple POC + NVDA + HD)

Apple FY2025 is the required POC. NVDA and HD are extra spot checks chosen
because their fiscal years end in January/February (period matching) and HD
FY2024 is a 53-week year with a very small beginning equity (ROE 385%).

Purpose: prove the Ground Truth Builder is trustworthy. Ground truth and Agent
version D share `agent/calculator.py`, so a wrong formula would make both wrong
in the same way and no automated comparison would catch it. Comparing against
the filing by hand is the only check that catches that.

Filing: Apple Inc. 10-K for fiscal year ended 2025-09-27
URL: https://www.sec.gov/Archives/edgar/data/320193/000032019325000079/aapl-20250927.htm
Accession number: 0000320193-25-000079

## Automated checks (`python -m data.validate_gt`)

- [x] Calculator known-answer tests pass (toy inputs, zero denominator, None input, negative equity)
- [x] All 56 (filing x ratio) records are `available`
- [x] Revenue minus COGS equals the filing's own `GrossProfit` tag for all 14 filings
- [x] Negative test: WMT (excluded from sample) correctly reports `conflicting_tags`
      for revenue (Total revenues 713,163 vs Net sales 706,413, USD millions)

## Manual comparison against the 10-K text (to be filled in by hand)

Values below come from XBRL via the pipeline. Open the 10-K at the URL above,
find each line item, and tick it only after you have seen the same number.

| Statement | Line item in 10-K | Period | XBRL value (USD millions) | Matches 10-K? |
|---|---|---|---|---|
| Balance Sheet | Total current assets | 2025-09-27 | 147,957 | [x] |
| Balance Sheet | Total current liabilities | 2025-09-27 | 165,631 | [x] |
| Balance Sheet | Total assets | 2025-09-27 | 359,241 | [x] |
| Balance Sheet | Total assets | 2024-09-28 | 364,980 | [x] |
| Balance Sheet | Total shareholders' equity | 2025-09-27 | 73,733 | [x] |
| Balance Sheet | Total shareholders' equity | 2024-09-28 | 56,950 | [x] |
| Income Statement | Total net sales | FY2025 | 416,161 | [x] |
| Income Statement | Total cost of sales | FY2025 | 220,960 | [x] |
| Income Statement | Net income | FY2025 | 112,010 | [x] |

Hand calculation (do on paper or a calculator, not with our code):

| Ratio | Hand result | Pipeline result | Match? |
|---|---|---|---|
| Current ratio = 147,957 / 165,631 | 0.8933 | 0.8933 | [x] |
| Gross profit margin = (416,161 - 220,960) / 416,161 | 0.4691 | 0.4691 | [x] |
| ROA = 112,010 / ((364,980 + 359,241) / 2) | 0.3093 | 0.3093 | [x] |
| ROE = 112,010 / ((56,950 + 73,733) / 2) | 1.7142 | 1.7142 | [x] |

---

## Spot check 2: NVIDIA 10-K, fiscal year 2026 (ended 2026-01-25)

URL: https://www.sec.gov/Archives/edgar/data/1045810/000104581026000021/nvda-20260125.htm
Accession number: 0001045810-26-000021
Columns in the 10-K: "Jan 25, 2026" (current) and "Jan 26, 2025" (prior)

| Statement | Line item in 10-K | Period | XBRL value (USD millions) | Matches 10-K? |
|---|---|---|---|---|
| Balance Sheet | Total current assets | 2026-01-25 | 125,605 | [x] |
| Balance Sheet | Total current liabilities | 2026-01-25 | 32,163 | [x] |
| Balance Sheet | Total assets | 2026-01-25 | 206,803 | [x] |
| Balance Sheet | Total assets | 2025-01-26 | 111,601 | [x] |
| Balance Sheet | Total shareholders' equity | 2026-01-25 | 157,293 | [x] |
| Balance Sheet | Total shareholders' equity | 2025-01-26 | 79,327 | [x] |
| Income Statement | Revenue | FY2026 | 215,938 | [x] |
| Income Statement | Cost of revenue | FY2026 | 62,475 | [x] |
| Income Statement | Net income | FY2026 | 120,067 | [x] |

| Ratio | Hand result | Pipeline result | Match? |
|---|---|---|---|
| Current ratio = 125,605 / 32,163 | 3.9053 | 3.9053 | [x] |
| Gross profit margin = (215,938 - 62,475) / 215,938 | 0.7107 | 0.7107 | [x] |
| ROA = 120,067 / ((111,601 + 206,803) / 2) | 0.7542 | 0.7542 | [x] |
| ROE = 120,067 / ((79,327 + 157,293) / 2) | 1.0149 | 1.0149 | [x] |

---

## Spot check 3: Home Depot 10-K, fiscal year 2024 (ended 2025-02-02, 53 weeks)

URL: https://www.sec.gov/Archives/edgar/data/354950/000035495025000085/hd-20250202.htm
Accession number: 0000354950-25-000085
Columns in the 10-K: "February 2, 2025" (current) and "January 28, 2024" (prior).
Home Depot calls this year "Fiscal 2024" even though it ends in 2025.

| Statement | Line item in 10-K | Period | XBRL value (USD millions) | Matches 10-K? |
|---|---|---|---|---|
| Balance Sheet | Total current assets | 2025-02-02 | 31,683 | [x] |
| Balance Sheet | Total current liabilities | 2025-02-02 | 28,661 | [x] |
| Balance Sheet | Total assets | 2025-02-02 | 96,119 | [x] |
| Balance Sheet | Total assets | 2024-01-28 | 76,530 | [x] |
| Balance Sheet | Total stockholders' equity | 2025-02-02 | 6,640 | [x] |
| Balance Sheet | Total stockholders' equity | 2024-01-28 | 1,044 | [x] |
| Income Statement | Net sales | Fiscal 2024 | 159,514 | [x] |
| Income Statement | Cost of sales | Fiscal 2024 | 106,206 | [x] |
| Income Statement | Net earnings | Fiscal 2024 | 14,806 | [x] |

| Ratio | Hand result | Pipeline result | Match? |
|---|---|---|---|
| Current ratio = 31,683 / 28,661 | 1.1054 | 1.1054 | [x] |
| Gross profit margin = (159,514 - 106,206) / 159,514 | 0.3342 | 0.3342 | [x] |
| ROA = 14,806 / ((76,530 + 96,119) / 2) | 0.1715 | 0.1715 | [x] |
| ROE = 14,806 / ((1,044 + 6,640) / 2) | 3.8537 | 3.8537 | [x] |
