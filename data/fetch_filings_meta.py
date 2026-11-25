"""Step 1: build the filing manifest (which 10-K filings are in the sample).

For each target ticker:
  1. look up its CIK in https://www.sec.gov/files/company_tickers.json
  2. call the Submissions API to get SIC code and filing history
  3. drop financial-sector companies (SIC 6000-6799)
  4. keep the most recent N_FILINGS_PER_COMPANY filings with form == "10-K"

Output: manifests/filing_manifest.json (small metadata, safe to commit).
The manifest is shared with the data pipeline (HTML download) and the agent.

Usage (from repo root):
    python -m data.fetch_filings_meta
"""

import json

from data.sec_client import MANIFEST_PATH, get_json

# Final sample: 7 non-financial companies across 7 industries with different fiscal year ends.
# All were checked to match every required XBRL concept without ambiguity.
TICKERS = ["AAPL", "MSFT", "NVDA", "KO", "HD", "TSLA", "NKE"]
N_FILINGS_PER_COMPANY = 2
FINANCIAL_SIC_RANGE = range(6000, 6800)


def load_cik_map() -> dict:
    """Return {ticker: 10-digit zero-padded CIK} from SEC's public company list."""
    data = get_json("https://www.sec.gov/files/company_tickers.json")
    return {row["ticker"]: str(row["cik_str"]).zfill(10) for row in data.values()}


def filing_url(cik10: str, accession_number: str, primary_document: str) -> str:
    """Build the EDGAR URL of a filing's main HTML document."""
    return (f"https://www.sec.gov/Archives/edgar/data/{int(cik10)}/"
            f"{accession_number.replace('-', '')}/{primary_document}")


def recent_10k_filings(cik10: str) -> tuple:
    """Return (submissions_json, list of the most recent 10-K filings for this CIK)."""
    sub = get_json(f"https://data.sec.gov/submissions/CIK{cik10}.json")
    rec = sub["filings"]["recent"]
    filings = []
    for i, form in enumerate(rec["form"]):
        if form != "10-K":          # excludes 10-K/A amendments and 10-Q
            continue
        filings.append({
            "accession_number": rec["accessionNumber"][i],
            "report_date": rec["reportDate"][i],      # fiscal period end date
            "filing_date": rec["filingDate"][i],
            "url": filing_url(cik10, rec["accessionNumber"][i], rec["primaryDocument"][i]),
        })
    return sub, filings[:N_FILINGS_PER_COMPANY]


def build_manifest(tickers=TICKERS) -> list:
    """Build manifest rows for all target tickers."""
    cik_map = load_cik_map()
    manifest = []
    for ticker in tickers:
        cik10 = cik_map[ticker]
        sub, filings = recent_10k_filings(cik10)
        sic = int(sub.get("sic") or 0)
        if sic in FINANCIAL_SIC_RANGE:
            print(f"[skip] {ticker}: financial-sector SIC {sic}")
            continue
        for f in filings:
            manifest.append({
                "filing_id": f"{ticker}_{f['report_date']}_10K",
                "company": ticker,
                "company_name": sub.get("name"),
                "cik": cik10,
                "sic": sic,
                "sic_description": sub.get("sicDescription"),
                "form": "10-K",
                **f,
            })
        print(f"[ok] {ticker}: {len(filings)} filing(s), SIC {sic}")
    return manifest


def main():
    manifest = build_manifest()
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Wrote {len(manifest)} filings to {MANIFEST_PATH.relative_to(MANIFEST_PATH.parents[1])}")


if __name__ == "__main__":
    main()
