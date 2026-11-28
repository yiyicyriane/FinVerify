"""Provider-neutral LLM client for the experiment, with two backends.

* ``OllamaBackend`` (default, free): a local model served by Ollama
  (``http://127.0.0.1:11434``). Greedy decoding (temperature 0) with a fixed
  seed, thinking disabled, JSON-schema constrained output via ``format``.
* ``AnthropicBackend`` (optional, paid): Claude through the Messages API.

``LLMClient`` does everything that must be identical for every version and
every provider:
  * classify each response in a fixed order:
        API failure -> stop reason (max_tokens -> truncated, refusal) -> JSON parse + schema check
    A truncated structured output always fails to parse, so truncation is
    decided first and is never retried;
  * retry a parse failure exactly once (attempt 2, same rule for every version);
  * cache every attempt, failures included, under
        runs/<run_id>/cache/<sha256(request)>_r<repeat>_a<attempt>.json
    so ``--resume`` replays a run exactly and a retry is always a new key.
    API failures produce no response and are never cached;
  * work on a normalised response (content blocks of type text / tool_use, a
    stop_reason and token usage), read by block type, never by position;
  * in a tool loop, send the previous assistant turn back unchanged (for
    Anthropic this includes thinking blocks, even empty ones).
"""

import hashlib
import json
import os
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from agent.schemas import validate

RETRY_NOTE = (
    "Your previous answer could not be parsed. Return only JSON that matches the "
    "required schema, with no other text."
)


class BackendError(Exception):
    """The provider returned no usable response (connection, HTTP or SDK error)."""


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
    """Concatenate the text blocks of a normalised response (by block type)."""
    return "".join(b.get("text", "") for b in response.get("content", []) if b.get("type") == "text")


def tool_uses(response: dict) -> list:
    return [b for b in response.get("content", []) if b.get("type") == "tool_use"]


def stop_outcome(response: dict) -> Optional[str]:
    """Outcome decided by the stop reason alone, before any parsing."""
    reason = response.get("stop_reason")
    if reason == "max_tokens":
        return "truncated"
    if reason == "refusal":
        return "refusal"
    return None


def replayable_content(response: dict) -> list:
    """Anthropic: assistant content to send back in a tool loop, every block in order, unchanged.

    Only keys whose value is None are dropped (e.g. ``citations: null``); thinking
    blocks keep their text (even when empty) and signature.
    """
    return [{k: v for k, v in block.items() if v is not None} for block in response.get("content", [])]


def _relpath(path: Path) -> str:
    try:
        return Path(os.path.relpath(path)).as_posix()
    except ValueError:  # different drive on Windows
        return path.as_posix()


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------

class AnthropicBackend:
    """Claude via the Messages API. No sampling parameters (the model rejects them),
    no refusal fallback (it would switch models mid-experiment)."""

    provider = "anthropic"

    def __init__(self, model: str, effort: str, max_tokens: int, sdk=None,
                 api_max_retries: int = 4, timeout: float = 600.0):
        self.model, self.effort, self.max_tokens = model, effort, max_tokens
        self._sdk, self._api_max_retries, self._timeout = sdk, api_max_retries, timeout
        self._lock = threading.Lock()

    @property
    def sdk(self):
        with self._lock:
            if self._sdk is None:
                import anthropic
                self._sdk = anthropic.Anthropic(max_retries=self._api_max_retries, timeout=self._timeout)
            return self._sdk

    def settings(self) -> dict:
        return {"provider": self.provider, "model": self.model, "effort": self.effort, "max_tokens": self.max_tokens}

    def describe(self) -> dict:
        info = self.settings()
        try:
            import anthropic
            info["sdk_version"] = anthropic.__version__
        except Exception:  # pragma: no cover
            pass
        return info

    def user_message(self, text: str) -> dict:
        return {"role": "user", "content": text}

    def build_request(self, system: str, messages: list, schema=None, tools=None) -> dict:
        output_config = {"effort": self.effort}
        if schema is not None:
            output_config["format"] = {"type": "json_schema", "schema": schema}
        request = {"model": self.model, "max_tokens": self.max_tokens, "system": system,
                   "messages": messages, "output_config": output_config}
        if tools:
            request["tools"] = tools
        return request

    def call(self, request: dict) -> dict:
        import anthropic
        try:
            return self.sdk.messages.create(**request).to_dict()
        except anthropic.APIError as exc:
            raise BackendError(f"{type(exc).__name__}: {exc}") from exc

    def normalize(self, raw: dict) -> dict:
        return raw

    def followup_messages(self, raw: dict, results: list) -> list:
        blocks = []
        for r in results:
            block = {"type": "tool_result", "tool_use_id": r["tool_use_id"], "content": json.dumps(r["output"])}
            if r["is_error"]:
                block["is_error"] = True
            blocks.append(block)
        return [{"role": "assistant", "content": replayable_content(raw)}, {"role": "user", "content": blocks}]


def _http_post_json(host: str, timeout: float):
    def post(path: str, payload: Optional[dict]) -> dict:
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(host.rstrip("/") + path, data=data,
                                     headers={"Content-Type": "application/json"},
                                     method="GET" if payload is None else "POST")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    return post


class OllamaBackend:
    """A local model served by Ollama. Free, no rate limits, data never leaves the machine.

    Decoding is greedy (temperature 0) with a fixed seed; thinking is disabled so
    version A has no hidden reasoning; ``format`` constrains output to the JSON schema.
    """

    provider = "ollama"

    def __init__(self, model: str, max_tokens: int, host: str = "http://127.0.0.1:11434",
                 temperature: float = 0.0, seed: int = 42, num_ctx: int = 8192, think: bool = False,
                 timeout: float = 900.0, transport: Optional[Callable] = None):
        self.model, self.max_tokens, self.host = model, max_tokens, host
        self.temperature, self.seed, self.num_ctx, self.think = temperature, seed, num_ctx, think
        self._transport = transport or _http_post_json(host, timeout)

    def settings(self) -> dict:
        return {"provider": self.provider, "model": self.model, "temperature": self.temperature,
                "seed": self.seed, "num_ctx": self.num_ctx, "max_tokens": self.max_tokens, "think": self.think}

    def describe(self) -> dict:
        """Settings plus the server version and the exact model digest (for reproducibility)."""
        info = self.settings()
        try:
            info["server_version"] = self._transport("/api/version", None).get("version")
            for m in self._transport("/api/tags", None).get("models", []):
                if m.get("name") == self.model or m.get("model") == self.model:
                    info["model_digest"] = m.get("digest")
                    info["model_details"] = m.get("details")
        except Exception as exc:  # describe must never break a run
            info["describe_error"] = str(exc)[:200]
        return info

    def check_available(self) -> Optional[str]:
        """Return an error message if the server or the model is missing, else None."""
        try:
            names = {m.get("name") for m in self._transport("/api/tags", None).get("models", [])}
        except Exception as exc:
            return f"Ollama server not reachable at {self.host} ({exc}). Start the Ollama app first."
        if self.model not in names and f"{self.model}:latest" not in names:
            return f"Model {self.model} is not installed. Run: ollama pull {self.model}"
        return None

    def user_message(self, text: str) -> dict:
        return {"role": "user", "content": text}

    def build_request(self, system: str, messages: list, schema=None, tools=None) -> dict:
        request = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}] + messages,
            "stream": False,
            "think": self.think,
            "options": {"temperature": self.temperature, "seed": self.seed,
                        "num_ctx": self.num_ctx, "num_predict": self.max_tokens},
        }
        if schema is not None:
            request["format"] = schema
        if tools:
            request["tools"] = [{"type": "function", "function": {
                "name": t["name"], "description": t["description"], "parameters": t["input_schema"]}}
                for t in tools]
        return request

    def call(self, request: dict) -> dict:
        try:
            raw = self._transport("/api/chat", request)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")[:300] if hasattr(exc, "read") else ""
            raise BackendError(f"HTTP {exc.code}: {body}") from exc
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError, ValueError) as exc:
            raise BackendError(f"{type(exc).__name__}: {exc}") from exc
        if raw.get("error"):
            raise BackendError(str(raw["error"])[:300])
        return raw

    def normalize(self, raw: dict) -> dict:
        message = raw.get("message") or {}
        content = []
        if message.get("content"):
            content.append({"type": "text", "text": message["content"]})
        calls = message.get("tool_calls") or []
        for i, call in enumerate(calls):
            fn = call.get("function") or {}
            args = fn.get("arguments")
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {"_unparsed": args}
            content.append({"type": "tool_use", "id": call.get("id") or f"call_{i}",
                            "name": fn.get("name"), "input": args or {}})
        if raw.get("done_reason") == "length":
            stop = "max_tokens"
        elif calls:
            stop = "tool_use"
        else:
            stop = "end_turn"
        return {"id": None, "model": raw.get("model"), "content": content, "stop_reason": stop,
                "usage": {"input_tokens": raw.get("prompt_eval_count"), "output_tokens": raw.get("eval_count")}}

    def followup_messages(self, raw: dict, results: list) -> list:
        message = raw.get("message") or {}
        assistant = {"role": "assistant", "content": message.get("content", "")}
        if message.get("tool_calls"):
            assistant["tool_calls"] = message["tool_calls"]
        tool_msgs = [{"role": "tool", "content": json.dumps(r["output"]), "tool_name": r["name"]} for r in results]
        return [assistant] + tool_msgs


def make_backend(config: dict, provider: Optional[str] = None, model: Optional[str] = None):
    """Build the backend named in manifests/experiment_config.json (``provider`` / ``providers``)."""
    name = provider or config["provider"]
    settings = dict(config["providers"][name])
    if model:
        settings["model"] = model
    if name == "ollama":
        return OllamaBackend(model=settings["model"], max_tokens=settings["max_tokens"],
                             host=settings.get("host", "http://127.0.0.1:11434"),
                             temperature=settings["temperature"], seed=settings["seed"],
                             num_ctx=settings["num_ctx"], think=settings["think"],
                             timeout=settings.get("timeout", 900.0))
    if name == "anthropic":
        return AnthropicBackend(model=settings["model"], effort=settings["effort"],
                                max_tokens=settings["max_tokens"],
                                api_max_retries=settings.get("api_max_retries", 4))
    raise ValueError(f"unknown provider {name}")


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class LLMClient:
    """One client per run; all caching and outcome rules live here, not in the backends."""

    def __init__(self, cache_dir, backend):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.backend = backend

    def send(self, request: dict, repeat_idx: int, attempt: int, step: str, prompt_version: str):
        """Return (normalised_response_or_None, raw_response_or_None, call_record)."""
        key = f"{request_hash(request)}_r{repeat_idx}_a{attempt}"
        path = self.cache_dir / f"{key}.json"
        record = {"step": step, "attempt": attempt, "prompt_version": prompt_version,
                  **self.backend.settings(), "cache_key": key}
        if path.exists():
            raw = json.loads(path.read_text(encoding="utf-8"))["response"]
            record["cached"] = True
        else:
            try:
                raw = self.backend.call(request)
            except BackendError as exc:
                record.update(cached=False, raw_path=None, stop_reason=None,
                              outcome="api_error", error=str(exc)[:500])
                return None, None, record
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"provider": self.backend.provider, "request": request, "response": raw},
                                      ensure_ascii=False, indent=1), encoding="utf-8")
            os.replace(tmp, path)
            record["cached"] = False
        response = self.backend.normalize(raw)
        usage = response.get("usage") or {}
        record.update(model=response.get("model"), message_id=response.get("id"),
                      stop_reason=response.get("stop_reason"), input_tokens=usage.get("input_tokens"),
                      output_tokens=usage.get("output_tokens"), raw_path=_relpath(path))
        return response, raw, record

    def structured(self, step: str, system: str, user: str, schema: dict,
                   repeat_idx: int, prompt_version: str) -> StepResult:
        """One structured-output step with at most one parse retry."""
        calls = []
        for attempt in (1, 2):
            content = user if attempt == 1 else f"{user}\n\n{RETRY_NOTE}"
            request = self.backend.build_request(system, [self.backend.user_message(content)], schema=schema)
            response, _, record = self.send(request, repeat_idx, attempt, step, prompt_version)
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

    def tool_loop(self, step: str, system: str, user: str, tools: list,
                  execute: Callable[[str, dict], dict], repeat_idx: int,
                  prompt_version: str, max_rounds: int = 3) -> dict:
        """Run a manual tool loop. outcome: ok | no_tool_call | truncated | refusal | api_error."""
        messages = [self.backend.user_message(user)]
        calls, tool_runs = [], []
        for round_idx in range(1, max_rounds + 1):
            request = self.backend.build_request(system, messages, tools=tools)
            response, raw, record = self.send(request, repeat_idx, 1, f"{step}:round{round_idx}", prompt_version)
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
                    output, is_error = {"error": f"{type(exc).__name__}: {exc}"}, True
                tool_runs.append({"name": use["name"], "input": use["input"], "output": output, "is_error": is_error})
                results.append({"tool_use_id": use["id"], "name": use["name"], "output": output, "is_error": is_error})
            record["outcome"] = "tool_use"
            messages = messages + self.backend.followup_messages(raw, results)
        return {"outcome": "ok" if tool_runs else "no_tool_call", "tool_runs": tool_runs,
                "final_text": None, "calls": calls}
