"""Decision order, retry, cache replay and tool-loop replay of agent/llm_client.py (mock providers)."""

import json
import tempfile
import unittest
import urllib.error
from pathlib import Path

import anthropic

from agent.llm_client import RETRY_NOTE, LLMClient, OllamaBackend, make_backend
from agent.schemas import calc_schema
from tests.helpers import FakeOllama, anthropic_backend, json_message, message, ollama_chat

GOOD = {"formula_used": "a / b", "calculation_steps": "2 / 1", "result": 2.0}


class Tmp(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()


class ClientRulesTests(Tmp):
    """Provider-independent rules, exercised through the Anthropic backend with a fake SDK."""

    def client(self, responder):
        backend = anthropic_backend(responder)
        return LLMClient(self.tmp / "cache", backend), backend.sdk

    def test_anthropic_request_shape(self):
        llm, sdk = self.client(lambda r: json_message(GOOD))
        llm.structured("B:calc", "sys", "user", calc_schema(), 1, "b_calc.v1@x")
        req = sdk.messages.calls[0]
        self.assertEqual(req["output_config"]["effort"], "medium")
        self.assertEqual(req["output_config"]["format"]["type"], "json_schema")
        self.assertEqual(req["max_tokens"], 16000)
        for forbidden in ("temperature", "top_p", "top_k", "thinking", "fallbacks"):
            self.assertNotIn(forbidden, req)

    def test_reads_text_by_block_type(self):
        llm, _ = self.client(lambda r: json_message(GOOD))  # thinking block comes first
        result = llm.structured("B:calc", "sys", "user", calc_schema(), 1, "v")
        self.assertEqual((result.outcome, result.data), ("ok", GOOD))

    def test_truncation_decided_before_parsing_and_not_retried(self):
        truncated = message([{"type": "text", "text": '{"formula_used": "a'}], stop_reason="max_tokens")
        llm, sdk = self.client(lambda r: truncated)
        result = llm.structured("B:calc", "sys", "user", calc_schema(), 1, "v")
        self.assertEqual(result.outcome, "truncated")
        self.assertEqual(len(sdk.messages.calls), 1)
        self.assertEqual(result.calls[0]["outcome"], "truncated")

    def test_refusal_not_retried(self):
        llm, sdk = self.client(lambda r: message([], stop_reason="refusal"))
        self.assertEqual(llm.structured("B:calc", "s", "u", calc_schema(), 1, "v").outcome, "refusal")
        self.assertEqual(len(sdk.messages.calls), 1)

    def test_parse_retry_uses_new_key_then_replays_from_cache(self):
        replies = iter([message([{"type": "text", "text": "not json"}]), json_message(GOOD)])
        llm, sdk = self.client(lambda r: next(replies))
        result = llm.structured("B:calc", "sys", "user", calc_schema(), 1, "v")
        self.assertEqual(result.outcome, "ok")
        self.assertEqual([c["attempt"] for c in result.calls], [1, 2])
        self.assertEqual([c["outcome"] for c in result.calls], ["parse_error", "ok"])
        self.assertTrue(result.calls[0]["cache_key"].endswith("_r1_a1"))
        self.assertTrue(result.calls[1]["cache_key"].endswith("_r1_a2"))
        self.assertIn(RETRY_NOTE, sdk.messages.calls[1]["messages"][0]["content"])

        # --resume: a fresh client on the same cache replays both attempts without any API call
        replay, replay_sdk = self.client(lambda r: AssertionError("no API call expected on replay"))
        again = replay.structured("B:calc", "sys", "user", calc_schema(), 1, "v")
        self.assertEqual(again.outcome, "ok")
        self.assertEqual([c["outcome"] for c in again.calls], ["parse_error", "ok"])
        self.assertTrue(all(c["cached"] for c in again.calls))
        self.assertEqual(replay_sdk.messages.calls, [])

    def test_two_parse_failures(self):
        llm, sdk = self.client(lambda r: message([{"type": "text", "text": "{}"}]))
        result = llm.structured("B:calc", "sys", "user", calc_schema(), 1, "v")
        self.assertEqual(result.outcome, "parse_error")
        self.assertEqual(len(sdk.messages.calls), 2)

    def test_repeat_index_is_part_of_the_key(self):
        llm, sdk = self.client(lambda r: json_message(GOOD))
        llm.structured("B:calc", "sys", "user", calc_schema(), 1, "v")
        llm.structured("B:calc", "sys", "user", calc_schema(), 2, "v")
        self.assertEqual(len(sdk.messages.calls), 2)  # r2 does not hit r1's cache

    def test_api_error_is_not_cached(self):
        llm, _ = self.client(lambda r: anthropic.APIConnectionError(request=None))
        result = llm.structured("B:calc", "sys", "user", calc_schema(), 1, "v")
        self.assertEqual(result.outcome, "api_error")
        self.assertEqual(list((self.tmp / "cache").glob("*.json")), [])
        retry, sdk = self.client(lambda r: json_message(GOOD))
        self.assertEqual(retry.structured("B:calc", "sys", "user", calc_schema(), 1, "v").outcome, "ok")
        self.assertEqual(len(sdk.messages.calls), 1)

    def test_anthropic_tool_loop_sends_assistant_content_back_unchanged(self):
        first_content = [
            {"type": "thinking", "thinking": "", "signature": "sig-1"},
            {"type": "text", "text": "Calling the tool.", "citations": None},
            {"type": "tool_use", "id": "toolu_1", "name": "calculate_ratio", "input": {"a": 2, "b": 1}},
        ]
        replies = iter([message(first_content, stop_reason="tool_use"),
                        message([{"type": "text", "text": "The ratio is 2."}])])
        llm, sdk = self.client(lambda r: next(replies))
        out = llm.tool_loop("C_tool", "sys", "user", [{"name": "calculate_ratio"}],
                            lambda name, args: {"value": args["a"] / args["b"], "status": "ok"}, 1, "v")
        self.assertEqual(out["outcome"], "ok")
        self.assertEqual(out["tool_runs"][0]["output"]["value"], 2.0)
        second = sdk.messages.calls[1]["messages"]
        self.assertEqual(second[1]["role"], "assistant")
        expected = [{k: v for k, v in b.items() if v is not None} for b in first_content]
        self.assertEqual(second[1]["content"], expected)  # empty thinking block kept, order kept
        self.assertEqual(second[2]["content"][0]["type"], "tool_result")
        self.assertEqual(second[2]["content"][0]["tool_use_id"], "toolu_1")

    def test_tool_loop_without_tool_call(self):
        llm, _ = self.client(lambda r: message([{"type": "text", "text": "2.0"}]))
        out = llm.tool_loop("C_tool", "sys", "user", [{"name": "calculate_ratio"}], lambda n, a: {}, 1, "v")
        self.assertEqual(out["outcome"], "no_tool_call")


class OllamaBackendTests(Tmp):
    def client(self, responder):
        fake = FakeOllama(responder)
        backend = OllamaBackend("qwen3:8b", 4096, temperature=0, seed=42, num_ctx=8192, think=False, transport=fake)
        return LLMClient(self.tmp / "cache", backend), fake

    def test_request_is_greedy_seeded_schema_constrained_and_thinking_off(self):
        llm, fake = self.client(lambda r: ollama_chat(json.dumps(GOOD)))
        result = llm.structured("B:calc", "sys", "user", calc_schema(), 1, "v")
        self.assertEqual((result.outcome, result.data), ("ok", GOOD))
        req = fake.requests[0]
        self.assertEqual(req["options"], {"temperature": 0, "seed": 42, "num_ctx": 8192, "num_predict": 4096})
        self.assertIs(req["think"], False)
        self.assertIs(req["stream"], False)
        self.assertEqual(req["format"], calc_schema())
        self.assertEqual(req["messages"][0], {"role": "system", "content": "sys"})
        self.assertEqual(result.calls[0]["provider"], "ollama")
        self.assertEqual(result.calls[0]["input_tokens"], 100)

    def test_length_stop_is_truncated(self):
        llm, fake = self.client(lambda r: ollama_chat('{"formula_used": "a', done_reason="length"))
        result = llm.structured("B:calc", "sys", "user", calc_schema(), 1, "v")
        self.assertEqual(result.outcome, "truncated")
        self.assertEqual(len(fake.requests), 1)

    def test_connection_error_is_api_error_and_not_cached(self):
        llm, _ = self.client(lambda r: urllib.error.URLError("connection refused"))
        result = llm.structured("B:calc", "sys", "user", calc_schema(), 1, "v")
        self.assertEqual(result.outcome, "api_error")
        self.assertEqual(list((self.tmp / "cache").glob("*.json")), [])

    def test_tool_loop_message_format(self):
        call = {"function": {"name": "calculate_ratio", "arguments": {"a": 3, "b": 2}}}
        replies = iter([ollama_chat("", tool_calls=[call]), ollama_chat("The ratio is 1.5.")])
        llm, fake = self.client(lambda r: next(replies))
        tool = {"name": "calculate_ratio", "description": "d",
                "input_schema": {"type": "object", "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
                                 "required": ["a", "b"], "additionalProperties": False}}
        out = llm.tool_loop("C_tool", "sys", "user", [tool],
                            lambda name, args: {"value": args["a"] / args["b"], "status": "ok"}, 1, "v")
        self.assertEqual(out["outcome"], "ok")
        self.assertEqual(out["tool_runs"][0]["output"]["value"], 1.5)
        first = fake.requests[0]
        self.assertEqual(first["tools"][0]["function"]["name"], "calculate_ratio")
        self.assertEqual(first["tools"][0]["function"]["parameters"], tool["input_schema"])
        second = fake.requests[1]["messages"]
        self.assertEqual(second[2], {"role": "assistant", "content": "", "tool_calls": [call]})
        self.assertEqual(second[3]["role"], "tool")
        self.assertEqual(second[3]["tool_name"], "calculate_ratio")
        self.assertEqual(json.loads(second[3]["content"])["value"], 1.5)

    def test_check_available_and_describe(self):
        backend = OllamaBackend("qwen3:8b", 4096, transport=FakeOllama(lambda r: None))
        self.assertIsNone(backend.check_available())
        self.assertEqual(backend.describe()["model_digest"], "sha256:mock")
        missing = OllamaBackend("other:1b", 4096, transport=FakeOllama(lambda r: None))
        self.assertIn("ollama pull other:1b", missing.check_available())

    def test_make_backend_from_config(self):
        config = json.loads((Path(__file__).resolve().parent.parent / "manifests" / "experiment_config.json")
                            .read_text(encoding="utf-8"))
        backend = make_backend(config)
        self.assertEqual(backend.provider, config["provider"])
        self.assertEqual(make_backend(config, "anthropic").provider, "anthropic")


if __name__ == "__main__":
    unittest.main()
