"""One command to rebuild the ground truth end to end.

Runs, in order:
  1. data.fetch_filings_meta   -> manifests/filing_manifest.json
  2. data.fetch_xbrl           -> data/raw/companyfacts/*.json (cached)
  3. data.match_ground_truth   -> data/ground_truth.json
  4. data.validate_gt          -> automated checks + Apple POC printout

Usage (from repo root):
    export SEC_USER_AGENT="FinVerify-GroundTruth your_team_email@example.com"
    python -m data.build_ground_truth
"""

import sys

from data import fetch_filings_meta, fetch_xbrl, match_ground_truth, validate_gt


def main():
    for step in (fetch_filings_meta, fetch_xbrl, match_ground_truth, validate_gt):
        print(f"\n===== {step.__name__} =====")
        sys.argv = [step.__name__]   # sub-steps parse their own (empty) CLI args
        step.main()


if __name__ == "__main__":
    main()
