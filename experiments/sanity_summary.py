"""Health check of one run. NOT the official metrics (those belong to eval/).

  python -m experiments.sanity_summary --run-id <run_id>

Prints:
  * status counts per version and split (crashes, truncations, leaks, parse failures);
  * D on real filings must equal the ground-truth ratio exactly (pipeline check);
  * toy filings: every version against the hand-computed answers;
  * dev filing: every version against ground truth (used for prompt development).
Accuracy on the test split is deliberately not printed, so prompt development
cannot look at test results.
"""

import argparse
import json
import math
from collections import Counter, defaultdict
from pathlib import Path

from agent.version_d import load_ground_truth
from experiments.run import ROOT, TOY_DIR, VERSION_ORDER, load_json

TOLERANCE = 0.01  # same as the proposed analysis tolerance (relative error)


def close(pred, expected) -> bool:
    if expected is None:
        return pred is None
    if pred is None:
        return False
    return math.isclose(pred, expected, rel_tol=TOLERANCE, abs_tol=1e-9)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--runs-dir", default=str(ROOT / "runs"))
    args = parser.parse_args(argv)

    path = Path(args.runs_dir) / args.run_id / "predictions.jsonl"
    preds = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    print(f"{len(preds)} predictions from {path}\n")

    counts = defaultdict(Counter)
    for p in preds:
        counts[(p["split"], p["version"])][p["status"]] += 1
    print("Status counts (split / version):")
    for (split, version) in sorted(counts, key=lambda k: (k[0], VERSION_ORDER.index(k[1]))):
        print(f"  {split:5s} {version:7s} " + ", ".join(f"{s}={n}" for s, n in sorted(counts[(split, version)].items())))

    gt = load_ground_truth()
    d_real = [p for p in preds if p["version"] == "D" and p["split"] != "toy"]
    if d_real:
        ok = sum(1 for p in d_real
                 if p["predicted_ratio"] is not None
                 and math.isclose(p["predicted_ratio"], gt[(p["filing_id"], p["ratio"])]["ground_truth_ratio"],
                                  rel_tol=1e-12))
        print(f"\nD pipeline check on real filings: {ok}/{len(d_real)} equal to ground truth"
              + ("" if ok == len(d_real) else "  <-- BUG in schema/gate/normalize/calculator path"))

    expected = load_json(TOY_DIR / "expected.json")
    for split, label in (("toy", "Toy filings vs hand-computed answers"), ("dev", "Dev filing vs ground truth")):
        rows = [p for p in preds if p["split"] == split]
        if not rows:
            continue
        print(f"\n{label} (relative tolerance {TOLERANCE:.0%}):")
        per_version = defaultdict(lambda: [0, 0])
        failures = []
        for p in rows:
            if split == "toy":
                exp = expected[p["filing_id"]][p["ratio"]]["expected_ratio"]
            else:
                exp = gt[(p["filing_id"], p["ratio"])]["ground_truth_ratio"]
            hit = close(p["predicted_ratio"], exp)
            per_version[p["version"]][0] += hit
            per_version[p["version"]][1] += 1
            if not hit:
                failures.append(f"    {p['version']:7s} {p['filing_id']} {p['ratio']}: got {p['predicted_ratio']} "
                                f"({p['status']}), expected {exp}")
        for version in VERSION_ORDER:
            if version in per_version:
                hit, total = per_version[version]
                print(f"  {version:7s} {hit}/{total}")
        if failures:
            print("  Misses:")
            print("\n".join(failures))

    if any(p["split"] == "test" for p in preds):
        print("\nTest split: accuracy intentionally not shown here (analysis owner computes it).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
