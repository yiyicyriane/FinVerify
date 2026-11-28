"""Version C_tool (P1): shared extraction + the model calls the calculator as a tool.

Same number of LLM calls as B, and the same input (values as reported +
scale); the only difference from B is that the arithmetic is done by
``agent/calculator.py`` through a strict tool call. The prediction is the tool's
output, not the model's restatement of it. Whether the tool arguments equal the
extracted values is recorded in ``tool_args_match_extraction``.
"""

from agent import prompt_loader
from agent.calculator import RATIOS
from agent.gate import apply_gate
from agent.leak_guard import check_prompt
from agent.normalize import enrich_items, parse_number
from agent.records import STEP_FAILURE_STATUS, doc_meta, prediction
from agent.schemas import FORMULAS, calculator_tool
from agent.version_b import failed_extraction_prediction

VERSION = "C_tool"


def run(extraction: dict, doc: dict, ratio: str, llm, repeat_idx: int) -> dict:
    meta = doc_meta(doc)
    if extraction["outcome"] != "ok":
        return failed_extraction_prediction(VERSION, extraction, meta, ratio)

    blocked, items = apply_gate(ratio, extraction["items"])
    common = {"extraction_id": extraction["extraction_id"], "items": enrich_items(items)}
    if blocked:
        return prediction(VERSION, meta, ratio, status=blocked, llm_calls=extraction["llm_calls"], **common)

    func, names = RATIOS[ratio]

    def execute(name: str, args: dict) -> dict:
        if name != "calculate_ratio":
            raise ValueError(f"unknown tool {name}")
        value, status = func(*[args[n] for n in names])
        return {"ratio": ratio, "value": value, "status": status}

    system, user = prompt_loader.render("c_tool", ratio=ratio,
                                        items_json=prompt_loader.items_payload(ratio, items))
    check_prompt(system, user)
    out = llm.tool_loop(VERSION, system, user, [calculator_tool(ratio)], execute,
                        repeat_idx, prompt_loader.version("c_tool"))
    calls = extraction["llm_calls"] + out["calls"]
    runs = [r for r in out["tool_runs"] if not r["is_error"]]
    if out["outcome"] in STEP_FAILURE_STATUS and not runs:
        return prediction(VERSION, meta, ratio, status=STEP_FAILURE_STATUS[out["outcome"]],
                          llm_calls=calls, tool_runs=out["tool_runs"], **common)
    if not runs:
        return prediction(VERSION, meta, ratio, status="no_tool_call", llm_calls=calls,
                          tool_runs=out["tool_runs"], final_text=out["final_text"], **common)

    last = runs[-1]
    expected_args = {n: parse_number(items[n]["value_reported"]) for n in names}
    return prediction(
        VERSION, meta, ratio,
        status=last["output"]["status"],
        predicted_ratio=last["output"]["value"],
        formula_used=FORMULAS[ratio],
        llm_calls=calls,
        tool_runs=out["tool_runs"],
        tool_args_match_extraction=all(last["input"].get(n) == expected_args[n] for n in names),
        final_text=out["final_text"],
        loop_outcome=out["outcome"],
        **common,
    )
