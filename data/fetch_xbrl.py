"""Step 2: download XBRL Company Facts for every company in the manifest.

Calls https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json once per company
and caches the raw response under data/raw/companyfacts/ (gitignored, large).
Matching against specific filings happens later in match_ground_truth.py.

Usage (from repo root):
    python -m data.fetch_xbrl            # uses cache when present
    python -m data.fetch_xbrl --refresh  # force re-download
"""

import argparse
import json

from data.sec_client import MANIFEST_PATH, companyfacts_cache_path, get_json


def load_company_facts(cik10: str, refresh: bool = False) -> dict:
    """Return the Company Facts JSON for one CIK, downloading it if not cached."""
    path = companyfacts_cache_path(cik10)
    if refresh and path.exists():
        path.unlink()
    return get_json(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik10}.json", cache_path=path)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--refresh", action="store_true", help="re-download even if cached")
    args = parser.parse_args()

    manifest = json.loads(MANIFEST_PATH.read_text())
    ciks = sorted({row["cik"] for row in manifest})
    for cik10 in ciks:
        facts = load_company_facts(cik10, refresh=args.refresh)
        n_tags = len(facts.get("facts", {}).get("us-gaap", {}))
        print(f"[ok] CIK{cik10} {facts.get('entityName')}: {n_tags} us-gaap concepts")


if __name__ == "__main__":
    main()
