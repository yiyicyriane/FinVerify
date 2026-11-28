"""Freeze the current prompt versions into manifests/experiment_config.json.

Run once, after prompt development on the toy filings and the dev filing and
before the first LLM call on a test filing. ``experiments.run`` refuses to run
LLM versions on test filings unless the frozen versions equal the current
prompt files, so prompts cannot change silently after the formal run starts.

  python -m experiments.freeze_prompts            # freeze (fails if already frozen differently)
  python -m experiments.freeze_prompts --check    # only report whether the prompts match
"""

import argparse
import json
import sys
from datetime import datetime, timezone

from agent import prompt_loader
from experiments.run import CONFIG_PATH


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--refreeze", action="store_true",
                        help="overwrite an existing freeze (only before any test-filing LLM call)")
    args = parser.parse_args(argv)

    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    current = prompt_loader.all_versions()
    frozen = config.get("frozen_prompts")
    if args.check:
        print("frozen:  ", frozen)
        print("current: ", current)
        print("MATCH" if frozen == current else "DIFFERENT")
        return 0 if frozen == current else 1
    if frozen and frozen != current and not args.refreeze:
        print("Prompts were already frozen with different versions. Use --refreeze only if no test filing "
              "has been run yet.", file=sys.stderr)
        return 1
    config["frozen_prompts"] = current
    config["frozen_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    CONFIG_PATH.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Frozen {current} at {config['frozen_at']}. Commit manifests/experiment_config.json before the formal run.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
