"""Thin wrapper around the Anthropic Messages API for the experiment.

Responsibilities:
  * build every request the same way for every version (same model, effort,
    max_tokens; structured outputs for every structured step; no sampling
    parameters, which the model does not accept; no refusal fallback, which
    would switch models mid-experiment);
  * classify each response in a fixed order:
        API failure -> stop_reason (max_tokens -> truncated, refusal) -> JSON parse + schema check
    A truncated structured output always fails to parse, so truncation must be
    decided first and is never retried;
  * retry a parse failure exactly once (attempt 2, same rule for every version);
  * cache every attempt, failures included, under
        runs/<run_id>/cache/<sha256(request)>_r<repeat>_a<attempt>.json
    so ``--resume`` replays a run exactly and a retry is always a new key.
    API errors produce no response and are never cached;
  * read content by block type (never ``content[0]``: with thinking on, the
    first block is usually a thinking block);
  * in a tool loop, send the previous assistant content back unchanged,
    including thinking blocks (empty ones too). Edited, reordered or dropped
    thinking blocks are rejected by the API.
"""

import hashlib
import json
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import anthropic

from agent.schemas import validate

RETRY_NOTE = (
    "Your previous answer could not be parsed. Return only JSON that matches the "
    "required schema, with no other text."
)


@dataclass
class StepResult:
    """Outcome of one structured step (after at most one parse retry)."""

    outcome: str  # ok | parse_error | truncated | refusal | api_error
    data: Optional[dict] = None
    calls: list = field(default_factory=list)


def canonical_json(obj) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def request_hash(request: dict) -> str:
    return hashlib.sha256(canonical_json(request).encode("utf-8")).hexdigest()[:32]


def text_of(response: dict) -> str:
    """Concatenate the text blocks of a response (by block type)."""
    return "".join(b.get("text", "") for b in response.get("content", []) if b.get("type") == "text")


def tool_uses(response: dict) -> list:
    return [b for b in response.get("content", []) if b.get("type") == "tool_use"]


def stop_outcome(response: dict) -> Optional[str]:
    """Outcome decided by stop_reason alone, before any parsing."""
    reason = response.get("stop_reason")
    if reason == "max_tokens":
        return "truncated"
    if reason == "refusal":
        return "refusal"
    return None


def replayable_content(response: dict) -> list:
    """Assistant content to send back in a tool loop: every block, in order, unchanged.

    Only keys whose value is None are dropped (e.g. ``citations: null`` on text
    blocks); thinking blocks keep their text (even when empty) and signature.
    """
    return [{k: v for k, v in block.items() if v is not None} for block in response.get("content", [])]


def _relpath(path: Path) -> str:
    try:
        return Path(os.path.relpath(path)).as_posix()
    except ValueError:  # different drive on Windows
        return path.as_posix()


class LLMClient:
    """One client per run. ``sdk`` can be replaced by a mock in tests."""

    def __init__(self, cache_dir, model: str, effort: str, max_tokens: int,
                 sdk=None, api_max_retries: int = 4, timeout: float = 600.0):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.model = model
        self.effort = effort
        self.max_tokens = max_tokens
        self._sdk = sdk
        self._api_max_retries = api_max_retries
        self._timeout = timeout
        self._lock = threading.Lock()

    @property
    def sdk(self):
        with self._lock:
            if self._sdk is None:
                self._sdk = anthropic.Anthropic(max_retries=self._api_max_retries, timeout=self._timeout)
            return self._sdk

    # -- request building ---------------------------------------------------

    def build_request(self, system: str, messages: list, schema: Optional[dict] = None,
                      tools: Optional[list] = None) -> dict:
        output_config = {"effort": self.effort}
        if schema is not None:
            output_config["format"] = {"type": "json_schema", "schema": schema}
        request = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": system,
            "messages": messages,
            "output_config": output_config,
        }
        if tools:
            request["tools"] = tools
        return request

    # -- one API call (cached) ------------------------------------------------

    def send(self, request: dict, repeat_idx: int, attempt: int, step: str, prompt_version: str):
        """Return (response_dict_or_None, call_record)."""
        key = f"{request_hash(request)}_r{repeat_idx}_a{attempt}"
        path = self.cache_dir / f"{key}.json"
        record = {
            "step": step,
            "attempt": attempt,
            "prompt_version": prompt_version,
            "effort": self.effort,
            "max_tokens": self.max_tokens,
            "cache_key": key,
        }
        if path.exists():
            response = json.loads(path.read_text(encoding="utf-8"))["response"]
            record["cached"] = True
        else:
            try:
                message = self.sdk.messages.create(**request)
            except anthropic.APIError as exc:  # status errors, connection errors, timeouts
                record.update(model=None, cached=False, raw_path=None, stop_reason=None,
                              outcome="api_error", error=f"{type(exc).__name__}: {exc}"[:500])
                return None, record
            response = message.to_dict()
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"request": request, "response": response}, ensure_ascii=False, indent=1),
                           encoding="utf-8")
            os.replace(tmp, path)
            record["cached"] = False
        usage = response.get("usage") or {}
        record.update(
            model=response.get("model"),
            message_id=response.get("id"),
            stop_reason=response.get("stop_reason"),
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
            raw_path=_relpath(path),
        )
        return response, record

    # -- structured step ------------------------------------------------------

    def structured(self, step: str, system: str, user: str, schema: dict,
                   repeat_idx: int, prompt_version: str) -> StepResult:
        """One structured-output step with at most one parse retry."""
        calls = []
        for attempt in (1, 2):
            content = user if attempt == 1 else f"{user}\n\n{RETRY_NOTE}"
            request = self.build_request(system, [{"role": "user", "content": content}], schema=schema)
            response, record = self.send(request, repeat_idx, attempt, step, prompt_version)
            calls.append(record)
            if response is None:
                return StepResult("api_error", None, calls)
            stopped = stop_outcome(response)
            if stopped:
                record["outcome"] = stopped
                return StepResult(stopped, None, calls)
            try:
                data = json.loads(text_of(response))
                errors = validate(data, schema)
            except json.JSONDecodeError as exc:
                data, errors = None, [f"invalid JSON: {exc}"]
            if not errors:
                record["outcome"] = "ok"
                return StepResult("ok", data, calls)
            record["outcome"] = "parse_error"
            record["parse_errors"] = errors[:5]
        return StepResult("parse_error", None, calls)

    # -- tool loop (C_tool) ---------------------------------------------------

    def tool_loop(self, step: str, system: str, user: str, tools: list,
                  execute: Callable[[str, dict], dict], repeat_idx: int,
                  prompt_version: str, max_rounds: int = 3) -> dict:
        """Run a manual tool loop. Returns outcome, tool_runs, final_text and call records.

        outcome: ok | no_tool_call | truncated | refusal | api_error
        """
        messages = [{"role": "user", "content": user}]
        calls, tool_runs = [], []
        for round_idx in range(1, max_rounds + 1):
            request = self.build_request(system, messages, tools=tools)
            response, record = self.send(request, repeat_idx, 1, f"{step}:round{round_idx}", prompt_version)
            calls.append(record)
            if response is None:
                return {"outcome": "api_error", "tool_runs": tool_runs, "final_text": None, "calls": calls}
            stopped = stop_outcome(response)
            if stopped:
                record["outcome"] = stopped
                return {"outcome": stopped, "tool_runs": tool_runs, "final_text": None, "calls": calls}
            uses = tool_uses(response)
            if not uses:
                record["outcome"] = "ok"
                return {"outcome": "ok" if tool_runs else "no_tool_call", "tool_runs": tool_runs,
                        "final_text": text_of(response), "calls": calls}
            results = []
            for use in uses:
                try:
                    output = execute(use["name"], use["input"])
                    is_error = False
                except Exception as exc:  # report the error to the model instead of crashing
                    output, is_error = {"error": str(exc)}, True
                tool_runs.append({"name": use["name"], "input": use["input"], "output": output, "is_error": is_error})
                result = {"type": "tool_result", "tool_use_id": use["id"], "content": json.dumps(output)}
                if is_error:
                    result["is_error"] = True
                results.append(result)
            record["outcome"] = "tool_use"
            messages = messages + [
                {"role": "assistant", "content": replayable_content(response)},
                {"role": "user", "content": results},
            ]
        return {"outcome": "ok" if tool_runs else "no_tool_call", "tool_runs": tool_runs,
                "final_text": None, "calls": calls}
