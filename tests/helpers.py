"""Offline test helpers: a fake Anthropic SDK and responders. No network, no API key."""

import copy
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOY_DIR = ROOT / "experiments" / "toy_filings"
CLEANED_DIR = ROOT / "data" / "cleaned_filings"


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def message(blocks, stop_reason="end_turn", model="mock-model"):
    return {"id": "msg_mock", "type": "message", "role": "assistant", "model": model,
            "content": blocks, "stop_reason": stop_reason,
            "usage": {"input_tokens": 100, "output_tokens": 20}}


def json_message(obj, **kwargs):
    """A structured-output style response: an (empty) thinking block first, then the JSON text."""
    return message([{"type": "thinking", "thinking": "", "signature": "sig-mock"},
                    {"type": "text", "text": json.dumps(obj), "citations": None}], **kwargs)


class FakeMessage:
    def __init__(self, data):
        self._data = data

    def to_dict(self):
        return copy.deepcopy(self._data)


class FakeMessages:
    def __init__(self, responder):
        self.responder = responder
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(copy.deepcopy(kwargs))
        result = self.responder(kwargs)
        if isinstance(result, Exception):
            raise result
        return FakeMessage(result)


class FakeSDK:
    def __init__(self, responder):
        self.messages = FakeMessages(responder)


def step_of(request: dict) -> str:
    """Which step a request belongs to, from its schema / tools."""
    if request.get("tools"):
        return "c_tool"
    props = request["output_config"]["format"]["schema"]["properties"]
    if "items" in props and "result" in props:
        return "a_direct"
    if "items" in props:
        return "extract"
    return "b_calc"


def answer_responder(items: dict, ratio_value):
    """Responder that answers every step correctly for one (filing, ratio)."""

    def respond(request):
        step = step_of(request)
        if step == "a_direct":
            return json_message({"items": items, "formula_used": "f", "calculation_steps": "s",
                                 "result": ratio_value})
        if step == "extract":
            return json_message({"items": items})
        if step == "b_calc":
            return json_message({"formula_used": "f", "calculation_steps": "s", "result": ratio_value})
        last = request["messages"][-1]["content"]
        if isinstance(last, list) and last and last[0].get("type") == "tool_result":
            return message([{"type": "text", "text": "done", "citations": None}])
        args = {name: item["value_reported"] for name, item in items.items()}
        return message([{"type": "thinking", "thinking": "", "signature": "sig-tool"},
                        {"type": "tool_use", "id": "toolu_1", "name": "calculate_ratio", "input": args}],
                       stop_reason="tool_use")

    return respond


def not_found_responder(request):
    """Responder that reports every input as not found (for plumbing tests on any filing)."""
    schema = request.get("output_config", {}).get("format", {}).get("schema")
    if schema is None:
        return message([{"type": "text", "text": "no tool needed", "citations": None}])
    props = schema["properties"]
    blank = {"value_reported": None, "scale": None, "period_end": None, "source_statement": None,
             "source_label": None, "status": "not_found"}
    out = {}
    if "items" in props:
        out["items"] = {name: dict(blank) for name in props["items"]["required"]}
    if "result" in props:
        out.update(formula_used="", calculation_steps="", result=None)
    return json_message(out)
