"""Load, version and render the prompt templates in ``agent/prompts/``.

Each template has a ``### SYSTEM`` and a ``### USER`` section and uses
``<<name>>`` placeholders (not ``str.format``, so braces in filing text are safe).
Every template includes the shared definitions block, so the prompt version of
a template hashes the template *and* ``definitions.v1.txt`` together.
"""

import hashlib
import json
import re
from pathlib import Path

from agent.schemas import INPUT_DESCRIPTION, ratio_inputs

PROMPT_DIR = Path(__file__).resolve().parent / "prompts"
PROMPT_FILES = {
    "definitions": "definitions.v1.txt",
    "a_direct": "a_direct.v1.txt",
    "extract": "extract.v1.txt",
    "b_calc": "b_calc.v1.txt",
    "c_tool": "c_tool.v1.txt",
}


def load(name: str) -> str:
    return (PROMPT_DIR / PROMPT_FILES[name]).read_text(encoding="utf-8")


def version(name: str) -> str:
    """``<file stem>@<8 hex>``; the hash covers the template plus the definitions block."""
    text = load(name) if name == "definitions" else load(name) + "\n" + load("definitions")
    stem = PROMPT_FILES[name].rsplit(".", 1)[0]
    return f"{stem}@{hashlib.sha256(text.encode('utf-8')).hexdigest()[:8]}"


def all_versions() -> dict:
    return {name: version(name) for name in PROMPT_FILES if name != "definitions"}


def inputs_block(ratio: str) -> str:
    return "\n".join(f"- {name}: {INPUT_DESCRIPTION[name]}" for name in ratio_inputs(ratio))


def items_payload(ratio: str, items: dict) -> str:
    """What B and C_tool see: value as reported + scale per input, nothing normalised."""
    payload = {name: {"value_reported": items[name].get("value_reported"), "scale": items[name].get("scale")}
               for name in ratio_inputs(ratio)}
    return json.dumps(payload, indent=2)


def render(name: str, **fields) -> tuple:
    """Return (system, user) for template ``name`` with placeholders filled in."""
    text = load(name).replace("<<definitions>>", load("definitions").strip())
    missing = set(re.findall(r"<<([a-z_]+)>>", text)) - set(fields)
    if missing:
        raise KeyError(f"unfilled placeholders in {name}: {sorted(missing)}")
    for key, value in fields.items():
        text = text.replace(f"<<{key}>>", str(value))
    system_part, user_part = text.split("### USER", 1)
    system = system_part.replace("### SYSTEM", "", 1).strip()
    return system, user_part.strip()
