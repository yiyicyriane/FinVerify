"""JSON schemas for every LLM step, generated per ratio from ``calculator.RATIOS``.

The list of required inputs per ratio lives only in ``agent/calculator.py``.
Everything here is derived from it, so a ratio can never be extracted with a
different input list than the one the calculator (and the ground truth) uses.

``items`` is an object keyed by input name, not an array. Every input name is
listed in ``required`` and ``additionalProperties`` is false, so structured
outputs guarantee that each required input appears exactly once and that no
extra keys appear. Every object in every schema sets
``additionalProperties: false`` (required by strict mode).

Nullable fields use ``anyOf: [<type>, {"type": "null"}]``.
"""

from agent.calculator import RATIOS

SCALES = ["units", "thousands", "millions", "billions"]
SCALE_FACTOR = {"units": 1, "thousands": 1_000, "millions": 1_000_000, "billions": 1_000_000_000}
STATEMENTS = ["balance_sheet", "income_statement", "cash_flow_statement"]
ITEM_STATUSES = ["extracted", "not_found", "unit_unclear"]

# Logical concept behind each calculator input (same names as data/ground_truth.json).
INPUT_CONCEPT = {
    "current_assets": "current_assets",
    "current_liabilities": "current_liabilities",
    "revenue": "revenue",
    "cost_of_revenue": "cost_of_revenue",
    "net_income": "net_income",
    "total_assets_begin": "total_assets",
    "total_assets_end": "total_assets",
    "equity_begin": "equity",
    "equity_end": "equity",
}

# What each input means. Shown to the model in every prompt (A, extraction, B, C_tool).
# Generic wording only: no company names, no row labels copied from filings, no numbers.
INPUT_DESCRIPTION = {
    "current_assets": "total current assets at the end of the target fiscal year",
    "current_liabilities": "total current liabilities at the end of the target fiscal year",
    "revenue": "total revenue for the target fiscal year",
    "cost_of_revenue": "total cost of revenue for the target fiscal year",
    "net_income": "net income attributable to the parent company for the target fiscal year",
    "total_assets_begin": "total assets at the end of the previous fiscal year",
    "total_assets_end": "total assets at the end of the target fiscal year",
    "equity_begin": "stockholders' equity attributable to the parent company at the end of the previous fiscal year",
    "equity_end": "stockholders' equity attributable to the parent company at the end of the target fiscal year",
}

# Formula strings recorded for the deterministic versions (C, C_tool, D).
FORMULAS = {
    "current_ratio": "current_assets / current_liabilities",
    "gross_profit_margin": "(revenue - cost_of_revenue) / revenue",
    "roa": "net_income / ((total_assets_begin + total_assets_end) / 2)",
    "roe": "net_income / ((equity_begin + equity_end) / 2)",
}


def ratio_inputs(ratio: str) -> list:
    """Ordered required inputs of a ratio, straight from the calculator registry."""
    return list(RATIOS[ratio][1])


def _nullable(schema: dict) -> dict:
    return {"anyOf": [schema, {"type": "null"}]}


def item_schema() -> dict:
    """One extracted line item (the evidence trail for one calculator input)."""
    props = {
        "value_reported": _nullable({"type": "number"}),
        "scale": _nullable({"type": "string", "enum": SCALES}),
        "period_end": _nullable({"type": "string"}),
        "source_statement": _nullable({"type": "string", "enum": STATEMENTS}),
        "source_label": _nullable({"type": "string"}),
        "status": {"type": "string", "enum": ITEM_STATUSES},
    }
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


def items_schema(ratio: str) -> dict:
    inputs = ratio_inputs(ratio)
    return {
        "type": "object",
        "properties": {name: item_schema() for name in inputs},
        "required": inputs,
        "additionalProperties": False,
    }


def extraction_schema(ratio: str) -> dict:
    """Step 1 of B / C / C_tool: line items only, no arithmetic."""
    return {
        "type": "object",
        "properties": {"items": items_schema(ratio)},
        "required": ["items"],
        "additionalProperties": False,
    }


def direct_schema(ratio: str) -> dict:
    """Version A: items used + formula + steps + result in one call. ``result`` may be null (abstain)."""
    props = {
        "items": items_schema(ratio),
        "formula_used": {"type": "string"},
        "calculation_steps": {"type": "string"},
        "result": _nullable({"type": "number"}),
    }
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


def calc_schema() -> dict:
    """Step 2 of B: the LLM does the arithmetic on the extracted values."""
    props = {
        "formula_used": {"type": "string"},
        "calculation_steps": {"type": "string"},
        "result": _nullable({"type": "number"}),
    }
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


def calculator_tool(ratio: str) -> dict:
    """Strict tool definition used by C_tool: one number per required input, as reported."""
    inputs = ratio_inputs(ratio)
    return {
        "name": "calculate_ratio",
        "description": (
            f"Deterministic calculator for {ratio}. Pass each input exactly as reported in the "
            "extracted line items (all inputs in the same scale). Returns the ratio and a status."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {name: {"type": "number", "description": INPUT_DESCRIPTION[name]} for name in inputs},
            "required": inputs,
            "additionalProperties": False,
        },
    }


# ---------------------------------------------------------------------------
# Minimal validator for the schema subset used above. Structured outputs should
# already guarantee validity; this catches anything that slips through (and
# cached responses produced before a schema change).
# ---------------------------------------------------------------------------

_TYPES = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
}


def validate(value, schema: dict, path: str = "$") -> list:
    """Return a list of error strings (empty when ``value`` matches ``schema``)."""
    if "anyOf" in schema:
        if any(not validate(value, option, path) for option in schema["anyOf"]):
            return []
        return [f"{path}: matches no anyOf option"]
    errors = []
    expected = schema.get("type")
    if expected and not _TYPES[expected](value):
        return [f"{path}: expected {expected}, got {type(value).__name__}"]
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: {value!r} not in {schema['enum']}")
    if expected == "object":
        props = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{path}: missing required key {key!r}")
        for key, sub in value.items():
            if key in props:
                errors.extend(validate(sub, props[key], f"{path}.{key}"))
            elif schema.get("additionalProperties") is False:
                errors.append(f"{path}: unexpected key {key!r}")
    if expected == "array" and "items" in schema:
        for i, sub in enumerate(value):
            errors.extend(validate(sub, schema["items"], f"{path}[{i}]"))
    return errors
