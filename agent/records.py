"""Prediction records shared by all versions (one row of predictions.jsonl).

Prediction-level ``status`` values (item-level statuses live in agent/schemas.py):
  ok                              ratio produced
  missing_input                   a required input was not found, or an LLM returned result = null (abstention)
  unit_mismatch                   an input's scale could not be determined
  undefined_division              denominator is zero (calculator)
  low_confidence_negative_equity  ROE computed but average equity < 0 (calculator; value still reported)
  parse_failure                   output still not valid JSON / schema after one retry
  truncated                       stop_reason == max_tokens
  refusal                         stop_reason == refusal
  api_error                       the API call failed after SDK retries
  no_tool_call                    C_tool: the model never called the calculator
  leak_check_failed               the prompt contained XBRL traces and was not sent (preprocessing failure)
"""

from typing import Optional

# Outcome of a failed LLM step -> prediction status.
STEP_FAILURE_STATUS = {
    "parse_error": "parse_failure",
    "truncated": "truncated",
    "refusal": "refusal",
    "api_error": "api_error",
    "leak_check_failed": "leak_check_failed",
}


def doc_meta(doc: dict) -> dict:
    return {
        "filing_id": doc["filing_id"],
        "company": doc.get("company"),
        "fiscal_year": doc.get("fiscal_year"),
        "period_end": doc.get("period_end"),
    }


def prediction(version: str, meta: dict, ratio: str, *, status: str,
               predicted_ratio: Optional[float] = None, formula_used: Optional[str] = None,
               calculation_steps: Optional[str] = None, extraction_id: Optional[str] = None,
               items: Optional[dict] = None, llm_calls: Optional[list] = None, **extra) -> dict:
    record = {
        "version": version,
        **meta,
        "ratio": ratio,
        "status": status,
        "predicted_ratio": predicted_ratio,
        "formula_used": formula_used,
        "calculation_steps": calculation_steps,
        "extraction_id": extraction_id,
        "items": items,
        "llm_calls": llm_calls or [],
    }
    record.update(extra)
    return record
