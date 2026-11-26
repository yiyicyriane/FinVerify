# Ground Truth Builder

Builds the reference answers ("ground truth") that all four experiment versions
(A/B/C/D) are scored against. Fully deterministic: SEC XBRL data plus Python,
no LLM anywhere in this pipeline.

## Run

Standard library only (Python 3.9+), no `pip install` needed.

```bash
export SEC_USER_AGENT="FinVerify-GroundTruth your_team_email@example.com"
python -m data.build_ground_truth
```

Run from the repo root. SEC rejects requests without a real contact in the
User-Agent. Raw API responses are cached in `data/raw/` (about 30 MB, gitignored);
delete that folder or pass `--refresh` to `data.fetch_xbrl` to re-download.

## Pipeline

| Step | Script | Output |
|---|---|---|
| 1 | `data/fetch_filings_meta.py` | `manifests/filing_manifest.json`: ticker, CIK, SIC, accession number, period end, filing URL |
| 2 | `data/fetch_xbrl.py` | `data/raw/companyfacts/CIK*.json` (cached Company Facts API responses) |
| 3 | `data/match_ground_truth.py` | `data/ground_truth.json`: one record per (filing x ratio) |
| 4 | `data/validate_gt.py` | known-answer tests, consistency checks, Apple POC printout |

Formulas live only in `agent/calculator.py`, which Agent versions C and D import directly.

## Sample

7 non-financial companies, latest 2 annual 10-K filings each, 14 filings, 56 (filing x ratio) records.

| Ticker | Industry | Fiscal year end |
|---|---|---|
| AAPL | Computer hardware | late September |
| MSFT | Software | June 30 |
| NVDA | Semiconductors | late January |
| KO | Beverages | December 31 |
| HD | Home improvement retail | early February |
| TSLA | Automobiles | December 31 |
| NKE | Footwear and apparel | May 31 |

Excluded after testing: JNJ, PG (no `StockholdersEquity` tag), CAT (no `NetIncomeLoss`
or `StockholdersEquity`), WMT (Total revenues and Net sales disagree, so "revenue" for
gross margin is itself ambiguous). Backups that also match cleanly: PEP, INTC, COST.

## Matching rules

* Key: `accession_number + concept + period_end + unit`, only `form == "10-K"` records.
* Instant concepts (balance sheet): record has no `start`.
* Duration concepts (income statement): `start` to `end` must be 350 to 380 days,
  which excludes quarterly records sharing the same end date and handles 52/53-week years.
* Several hits with one value: `available`. Several hits with different values: `ambiguous_context`.
* Candidate tags are tried in order with a full match each, not just "does the tag exist":

| Concept | Candidate tags (in order) |
|---|---|
| revenue | `Revenues`, `RevenueFromContractWithCustomerExcludingAssessedTax`, `SalesRevenueNet` |
| cost_of_revenue | `CostOfRevenue`, `CostOfGoodsAndServicesSold`, `CostOfGoodsSold` |
| current_assets / current_liabilities | `AssetsCurrent` / `LiabilitiesCurrent` |
| net_income | `NetIncomeLoss` |
| total_assets / equity | `Assets` / `StockholdersEquity` |

* If a later candidate tag also matches with a different value, the input gets
  `conflicting_tags` and `validate_gt` fails, so it gets reviewed by hand.
* Beginning-of-period values (ROA, ROE) come from the same accession number:
  the latest instant date before the current period end (the comparative column).
* Do not use the XBRL `fy` field to select periods: every record in a filing
  carries the filing's `fy`, including the prior-year comparatives.

## Output format (`data/ground_truth.json`)

A list of records, one per (filing x ratio):

```json
{
  "filing_id": "AAPL_2025-09-27_10K",
  "company": "AAPL",
  "cik": "0000320193",
  "fiscal_year": 2025,
  "accession_number": "0000320193-25-000079",
  "period_end": "2025-09-27",
  "ratio": "current_ratio",
  "inputs": [
    {"input": "current_assets", "concept": "current_assets", "tag_used": "AssetsCurrent",
     "value": 147957000000, "unit": "USD", "period_type": "instant",
     "period_start": null, "period_end": "2025-09-27", "status": "available"}
  ],
  "ground_truth_status": "available",
  "ground_truth_ratio": 0.8933,
  "calculator_status": "ok"
}
```

* `value` is raw USD as tagged in XBRL. The HTML filing usually shows "in millions",
  so the verifier must normalise scale before comparing extracted values.
* `ground_truth_status`: `available`, `mapping_unavailable`, `ambiguous_context`, or `fetch_failed`.
  Records that are not `available` count against Dataset Coverage, not against agent accuracy.
* `calculator_status`: `ok`, `missing_input`, `undefined_division`, `low_confidence_negative_equity`.
* `concept` uses the same logical names as the extraction JSON schema (`current_assets`, ...).

## Calculator interface (shared with Agent C/D)

```python
from agent.calculator import current_ratio, gross_profit_margin, roa, roe, RATIOS

current_ratio(current_assets, current_liabilities)      # -> (value, status)
gross_profit_margin(revenue, cost_of_revenue)
roa(net_income, total_assets_begin, total_assets_end)    # average of begin and end
roe(net_income, equity_begin, equity_end)                # average of begin and end
```

## Validation status

Automated checks pass. Manual check against the 10-K text is done for Apple FY2025,
NVDA FY2026 and HD FY2024: all 27 line items match, and Apple's 4 ratios were
recomputed by hand. Details in `docs/ground_truth_verification.md`.

## Note on HD FY2024 ROE (385%)

Beginning equity was only 1.0B against 6.6B at year end, so average-equity ROE is
about 385% while end-of-year ROE would be about 225%. The number is correct; it is
also a useful case for detecting formula errors.
