"""Version B: structured extraction + LLM calculation.

Step 1 (``extract``) is shared with C and C_tool: it produces one extraction
record per (filing, ratio, repeat), which the other versions read instead of
calling the model again.

Step 2 (``calc``) runs only if the shared gate passes. The model sees the
values exactly as reported plus their scale (not 12-digit USD numbers); all
four ratios divide like units, so the scale does not change the result.
"""

from agent import prompt_loader
from agent.gate import apply_gate
from agent.leak_guard import LeakDetected, check_prompt
from agent.normalize import enrich_items
from agent.records import STEP_FAILURE_STATUS, doc_meta, prediction
from agent.schemas import calc_schema, extraction_schema
from data.clean_filing import to_text

VERSION = "B"


def extraction_id(filing_id: str, ratio: str, repeat_idx: int) -> str:
    return f"ext_{filing_id}_{ratio}_r{repeat_idx}"


def extract(doc: dict, ratio: str, llm, repeat_idx: int) -> dict:
    """Shared extraction step. Returns an extraction record (also saved by the runner)."""
    record = {
        "extraction_id": extraction_id(doc["filing_id"], ratio, repeat_idx),
        "filing_id": doc["filing_id"],
        "ratio": ratio,
        "repeat_idx": repeat_idx,
        "prompt_version": prompt_loader.version("extract"),
        "outcome": None,
        "items": None,
        "llm_calls": [],
    }
    system, user = prompt_loader.render(
        "extract",
        ratio=ratio,
        target_period_end=doc["period_end"],
        inputs_block=prompt_loader.inputs_block(ratio),
        statements=to_text(doc),
    )
    try:
        check_prompt(system, user)
    except LeakDetected as exc:
        record.update(outcome="leak_check_failed", leak_hits=exc.hits)
        return record
    result = llm.structured("extract", system, user, extraction_schema(ratio),
                            repeat_idx, record["prompt_version"])
    record["outcome"] = result.outcome
    record["llm_calls"] = result.calls
    if result.outcome == "ok":
        record["items"] = result.data["items"]
    return record


def failed_extraction_prediction(version: str, extraction: dict, meta: dict, ratio: str) -> dict:
    """Prediction for B / C / C_tool when the shared extraction step itself failed."""
    extra = {"leak_hits": extraction["leak_hits"]} if "leak_hits" in extraction else {}
    return prediction(version, meta, ratio, status=STEP_FAILURE_STATUS[extraction["outcome"]],
                      extraction_id=extraction["extraction_id"], llm_calls=extraction["llm_calls"], **extra)


def calc(extraction: dict, doc: dict, ratio: str, llm, repeat_idx: int) -> dict:
    meta = doc_meta(doc)
    if extraction["outcome"] != "ok":
        return failed_extraction_prediction(VERSION, extraction, meta, ratio)

    blocked, items = apply_gate(ratio, extraction["items"])
    common = {"extraction_id": extraction["extraction_id"], "items": enrich_items(items)}
    if blocked:  # same status as C and C_tool; the calculation LLM is not called
        return prediction(VERSION, meta, ratio, status=blocked, llm_calls=extraction["llm_calls"], **common)

    system, user = prompt_loader.render("b_calc", ratio=ratio,
                                        items_json=prompt_loader.items_payload(ratio, items))
    check_prompt(system, user)
    result = llm.structured(f"{VERSION}:calc", system, user, calc_schema(),
                            repeat_idx, prompt_loader.version("b_calc"))
    calls = extraction["llm_calls"] + result.calls
    if result.outcome != "ok":
        return prediction(VERSION, meta, ratio, status=STEP_FAILURE_STATUS[result.outcome],
                          llm_calls=calls, **common)
    value = result.data["result"]
    return prediction(
        VERSION, meta, ratio,
        status="ok" if value is not None else "missing_input",
        predicted_ratio=value,
        formula_used=result.data["formula_used"],
        calculation_steps=result.data["calculation_steps"],
        llm_calls=calls,
        **common,
    )
