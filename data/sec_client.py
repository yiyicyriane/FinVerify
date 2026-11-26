"""Minimal SEC EDGAR HTTP client (standard library only).

SEC requires every request to carry a User-Agent that names the application and
a contact email, otherwise requests are throttled or rejected. Set it with:

    export SEC_USER_AGENT="FinVerify-GroundTruth your_team_email@example.com"

Responses can be cached on disk so repeated runs do not hit the API again.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "raw"          # gitignored: large raw SEC responses
MANIFEST_PATH = ROOT / "manifests" / "filing_manifest.json"
GROUND_TRUTH_PATH = ROOT / "data" / "ground_truth.json"

_DEFAULT_UA = "FinVerify-GroundTruth research@example.com"
_SLEEP_SECONDS = 0.2                     # well under SEC's 10 requests/second limit
_warned = False


def user_agent() -> str:
    """Return the User-Agent from SEC_USER_AGENT, warning once if it is unset."""
    global _warned
    ua = os.environ.get("SEC_USER_AGENT")
    if not ua:
        if not _warned:
            print("[warn] SEC_USER_AGENT not set; using a placeholder. "
                  "Set it to 'AppName contact@email' before real runs.", file=sys.stderr)
            _warned = True
        return _DEFAULT_UA
    return ua


def get_json(url: str, cache_path: Path = None, retries: int = 3) -> dict:
    """GET a JSON document from SEC, optionally reading/writing a local cache file."""
    if cache_path is not None and cache_path.exists():
        return json.loads(cache_path.read_text())

    last_err = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": user_agent()})
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read())
            time.sleep(_SLEEP_SECONDS)
            if cache_path is not None:
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                cache_path.write_text(json.dumps(data))
            return data
        except (urllib.error.URLError, TimeoutError) as err:
            last_err = err
            time.sleep(1 + attempt)
    raise RuntimeError(f"SEC request failed after {retries} attempts: {url} ({last_err})")


def companyfacts_cache_path(cik10: str) -> Path:
    """Local cache location for one company's Company Facts JSON."""
    return RAW_DIR / "companyfacts" / f"CIK{cik10}.json"
