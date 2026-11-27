"""Version A: direct LLM. One call reads the statements, picks the inputs and computes the ratio.

A has no gate: whatever result the model gives is the prediction. Because A
uses the same definitions block ("if a required line item does not appear,
use null"), it can abstain: ``result = null`` is recorded as ``missing_input``.
Note that A is not "reasoning-free": the model runs with adaptive thinking at
the configured effort.
"""

from agent import prompt_loader
from agent.gate import apply_gate
from agent.leak_guard import LeakDetected, check_prompt
from agent.normalize import enrich_items
from agent.records import STEP_FAILURE_STATUS, doc_meta, prediction
from agent.schemas import direct_schema
from data.clean_filing import to_text

VERSION = "A"


def run(doc: dict, ratio: str, llm, repeat_idx: int) -> dict:
    meta = doc_meta(doc)
    system, user = prompt_loader.render(
        "a_direct",
        ratio=ratio,
        target_period_end=doc["period_end"],
        inputs_block=prompt_loader.inputs_block(ratio),
        statements=to_text(doc),
    )
    try:
        check_prompt(system, user)
    except LeakDetected as exc:
        return prediction(VERSION, meta, ratio, status="leak_check_failed", leak_hits=exc.hits)

    result = llm.structured(f"{VERSION}:direct", system, user, direct_schema(ratio),
                            repeat_idx, prompt_loader.version("a_direct"))
    if result.outcome != "ok":
        return prediction(VERSION, meta, ratio, status=STEP_FAILURE_STATUS[result.outcome],
                          llm_calls=result.calls)

    data = result.data
    _, items = apply_gate(ratio, data["items"])  # normalise + flag only; A is never blocked
    value = data["result"]
    return prediction(
        VERSION, meta, ratio,
        status="ok" if value is not None else "missing_input",
        predicted_ratio=value,
        formula_used=data["formula_used"],
        calculation_steps=data["calculation_steps"],
        items=enrich_items(items),
        llm_calls=result.calls,
    )
