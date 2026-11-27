"""Run the A / B / C / C_tool / D experiment (one command, from the repo root).

Examples:
  # pipeline check, no API key needed
  python -m experiments.run --run-id d-check --versions D --filings all,toy

  # prompt development: toy filings + the dev filing only
  python -m experiments.run --run-id dev-01 --filings dev,toy

  # freeze the prompts, then the formal run (r1 is the primary result)
  python -m experiments.freeze_prompts
  python -m experiments.run --run-id main --filings all,toy --repeats 1
  python -m experiments.run --run-id main --filings all,toy --repeats 2,3 --resume

``--filings`` takes a comma-separated list of: all (14 real filings), test
(real minus dev), dev, toy, or explicit filing ids.

Outputs:
  runs/<run_id>/cache/          every API response (gitignored); --resume replays it
  runs/<run_id>/extractions/    shared extraction records (B / C / C_tool)
  runs/<run_id>/predictions/    one file per (filing, ratio, repeat, version)
  runs/<run_id>/predictions.jsonl
  results/<run_id>/predictions.jsonl + run_meta.json   compact copy meant for the analysis

The cache lives inside the run directory, so a new run_id always starts from
an empty cache and can never overwrite another run's responses. An existing
run_id is only reopened with --resume.
"""

import argparse
import json
import os
import platform
import subprocess
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from agent import prompt_loader, version_a, version_b, version_c, version_c_tool, version_d
from agent.llm_client import LLMClient
from agent.records import doc_meta

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "manifests" / "experiment_config.json"
MANIFEST_PATH = ROOT / "manifests" / "filing_manifest.json"
CLEANED_DIR = ROOT / "data" / "cleaned_filings"
TOY_DIR = ROOT / "experiments" / "toy_filings"
VERSION_ORDER = ["A", "B", "C", "C_tool", "D"]
LLM_VERSIONS = {"A", "B", "C", "C_tool"}
SPLIT_ORDER = {"dev": 0, "test": 1, "toy": 2}


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_dotenv(path: Path = ROOT / ".env") -> list:
    """Read KEY=VALUE lines from the gitignored .env into os.environ (existing variables win).

    Returns the names that were set (never the values)."""
    if not Path(path).exists():
        return []
    loaded = []
    for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
            loaded.append(key)
    return loaded


def write_json(path, obj):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(path).with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def load_real_filings() -> dict:
    """{filing_id: cleaned doc} in manifest order."""
    ids = [row["filing_id"] for row in load_json(MANIFEST_PATH)]
    return {fid: load_json(CLEANED_DIR / f"{fid}.json") for fid in ids}


def load_toys() -> tuple:
    docs = {}
    for path in sorted(TOY_DIR.glob("TOY-*.json")):
        doc = load_json(path)
        docs[doc["filing_id"]] = doc
    return docs, load_json(TOY_DIR / "expected.json")


def split_of(filing_id: str, config: dict, toys: dict) -> str:
    if filing_id in toys:
        return "toy"
    return "dev" if filing_id in config["dev_filings"] else "test"


def select_filings(spec: str, config: dict, real: dict, toys: dict) -> list:
    chosen = []
    for token in [t.strip() for t in spec.split(",") if t.strip()]:
        if token == "all":
            chosen += list(real)
        elif token == "test":
            chosen += [f for f in real if f not in config["dev_filings"]]
        elif token == "dev":
            chosen += list(config["dev_filings"])
        elif token == "toy":
            chosen += list(toys)
        elif token in real or token in toys:
            chosen.append(token)
        else:
            raise SystemExit(f"unknown filing selector: {token}")
    return list(dict.fromkeys(chosen))  # de-duplicate, keep order


def git_state() -> dict:
    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
        dirty = bool(subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True).stdout.strip())
        return {"commit": commit or None, "dirty": dirty}
    except OSError:
        return {"commit": None, "dirty": None}


def parse_args(argv):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--run-id", required=True)
    p.add_argument("--versions", default="A,B,C,C_tool,D", help="comma-separated subset of A,B,C,C_tool,D")
    p.add_argument("--filings", default="all,toy")
    p.add_argument("--ratios", default="all", help="'all' or comma-separated ratio names")
    p.add_argument("--repeats", default=None, help="comma-separated repeat indices (default: primary repeat)")
    p.add_argument("--resume", action="store_true", help="reopen an existing run_id and replay its cache")
    p.add_argument("--concurrency", type=int, default=None)
    p.add_argument("--model", default=None, help="override config model (dev runs only)")
    p.add_argument("--skip-key-check", action="store_true", help="credentials come from an ant profile, not env vars")
    p.add_argument("--runs-dir", default=str(ROOT / "runs"), help=argparse.SUPPRESS)
    p.add_argument("--results-dir", default=str(ROOT / "results"), help=argparse.SUPPRESS)
    return p.parse_args(argv)


def main(argv=None, sdk=None) -> int:
    args = parse_args(argv)
    config = load_json(CONFIG_PATH)
    versions = [v.strip() for v in args.versions.split(",") if v.strip()]
    unknown = set(versions) - set(VERSION_ORDER)
    if unknown:
        raise SystemExit(f"unknown versions: {sorted(unknown)}")
    ratios = config["ratios"] if args.ratios == "all" else [r.strip() for r in args.ratios.split(",")]
    repeats = [int(r) for r in args.repeats.split(",")] if args.repeats else [config["primary_repeat"]]

    real = load_real_filings()
    toys, toy_expected = load_toys()
    filing_ids = select_filings(args.filings, config, real, toys)
    docs = {**real, **toys}
    uses_llm = bool(LLM_VERSIONS & set(versions))
    model = args.model or config["model"]

    # Guards ---------------------------------------------------------------
    run_dir = Path(args.runs_dir) / args.run_id
    if run_dir.exists() and not args.resume:
        raise SystemExit(f"{run_dir} already exists. Use --resume to continue it, or choose a new --run-id.")
    if args.resume and not run_dir.exists():
        raise SystemExit(f"--resume given but {run_dir} does not exist.")
    splits = {split_of(f, config, toys) for f in filing_ids}
    if uses_llm and "test" in splits:
        if config.get("frozen_prompts") != prompt_loader.all_versions():
            raise SystemExit("Prompts are not frozen (or changed since freezing). Develop on --filings dev,toy, "
                             "then run `python -m experiments.freeze_prompts` before touching test filings.")
        if args.model and args.model != config["model"]:
            raise SystemExit("--model override is only allowed on dev/toy filings.")
    if uses_llm and sdk is None:
        load_dotenv()
        if not args.skip_key_check and not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
            raise SystemExit("No ANTHROPIC_API_KEY found. Set it in your environment or in the gitignored .env "
                             "file at the repo root (ANTHROPIC_API_KEY=...), never in code, "
                             "or pass --skip-key-check if you use an `ant auth login` profile.")

    run_dir.mkdir(parents=True, exist_ok=True)
    llm = None
    if uses_llm:
        llm = LLMClient(run_dir / "cache", model=model, effort=config["effort"],
                        max_tokens=config["max_tokens"], sdk=sdk, api_max_retries=config["api_max_retries"])
    ground_truth = version_d.load_ground_truth() if "D" in versions else {}

    meta_path = run_dir / "run_meta.json"
    run_meta = load_json(meta_path) if meta_path.exists() else {"run_id": args.run_id, "invocations": []}
    try:
        import anthropic
        sdk_version = anthropic.__version__
    except Exception:  # pragma: no cover
        sdk_version = None
    run_meta["invocations"].append({
        "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "argv": argv if argv is not None else sys.argv[1:],
        "versions": versions, "filings": filing_ids, "ratios": ratios, "repeats": repeats,
        "model": model, "effort": config["effort"], "max_tokens": config["max_tokens"],
        "prompt_versions": prompt_loader.all_versions(),
        "frozen_prompts": config.get("frozen_prompts"),
        "anthropic_sdk": sdk_version, "python": platform.python_version(), "git": git_state(),
    })
    write_json(meta_path, run_meta)

    # Work ------------------------------------------------------------------
    def run_task(task):
        filing_id, ratio, repeat_idx = task
        doc = docs[filing_id]
        meta = doc_meta(doc)
        preds = []
        if "A" in versions:
            preds.append(version_a.run(doc, ratio, llm, repeat_idx))
        if {"B", "C", "C_tool"} & set(versions):
            extraction = version_b.extract(doc, ratio, llm, repeat_idx)
            write_json(run_dir / "extractions" / f"{extraction['extraction_id']}.json", extraction)
            if "B" in versions:
                preds.append(version_b.calc(extraction, doc, ratio, llm, repeat_idx))
            if "C" in versions:
                preds.append(version_c.run(extraction, doc, ratio))
            if "C_tool" in versions:
                preds.append(version_c_tool.run(extraction, doc, ratio, llm, repeat_idx))
        if "D" in versions:
            if filing_id in toys:
                items = toy_expected[filing_id][ratio]["items"]
            else:
                items = version_d.items_from_ground_truth(ground_truth[(filing_id, ratio)])
            preds.append(version_d.run(items, meta, ratio))
        for pred in preds:
            pred.update(run_id=args.run_id, repeat_idx=repeat_idx,
                        is_primary=repeat_idx == config["primary_repeat"],
                        split=split_of(filing_id, config, toys))
            write_json(run_dir / "predictions" / f"{filing_id}__{ratio}__r{repeat_idx}__{pred['version']}.json", pred)
        return preds

    tasks = [(f, r, k) for k in repeats for f in filing_ids for r in ratios]
    workers = args.concurrency or config["concurrency"]
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for preds in pool.map(run_task, tasks):
            done += 1
            if done % 10 == 0 or done == len(tasks):
                print(f"  {done}/{len(tasks)} tasks", flush=True)

    # Collect every prediction of this run (earlier invocations included) ------
    all_preds = [load_json(p) for p in sorted((run_dir / "predictions").glob("*.json"))]
    all_preds.sort(key=lambda p: (SPLIT_ORDER.get(p["split"], 9), p["filing_id"], p["ratio"],
                                  p["repeat_idx"], VERSION_ORDER.index(p["version"])))
    lines = "".join(json.dumps(p, ensure_ascii=False) + "\n" for p in all_preds)
    (run_dir / "predictions.jsonl").write_text(lines, encoding="utf-8")
    results_dir = Path(args.results_dir) / args.run_id
    results_dir.mkdir(parents=True, exist_ok=True)
    (results_dir / "predictions.jsonl").write_text(lines, encoding="utf-8")
    write_json(results_dir / "run_meta.json", run_meta)

    counts = Counter((p["version"], p["status"]) for p in all_preds)
    print(f"\n{len(all_preds)} predictions in {run_dir} (copy in {results_dir})")
    for version in VERSION_ORDER:
        row = {s: n for (v, s), n in counts.items() if v == version}
        if row:
            print(f"  {version:7s} " + ", ".join(f"{s}={n}" for s, n in sorted(row.items())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
