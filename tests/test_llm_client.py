"""Decision order, retry, cache replay and tool-loop replay of agent/llm_client.py (mock SDK)."""

import tempfile
import unittest
from pathlib import Path

import anthropic

from agent.llm_client import RETRY_NOTE, LLMClient
from agent.schemas import calc_schema
from tests.helpers import FakeSDK, json_message, message

GOOD = {"formula_used": "a / b", "calculation_steps": "2 / 1", "result": 2.0}


def client(tmp, responder):
    sdk = FakeSDK(responder)
    return LLMClient(Path(tmp) / "cache", model="mock-model", effort="medium", max_tokens=16000, sdk=sdk), sdk


class LLMClientTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_request_shape(self):
        llm, sdk = client(self.tmp, lambda r: json_message(GOOD))
        llm.structured("B:calc", "sys", "user", calc_schema(), 1, "b_calc.v1@x")
        req = sdk.messages.calls[0]
        self.assertEqual(req["output_config"]["effort"], "medium")
        self.assertEqual(req["output_config"]["format"]["type"], "json_schema")
        self.assertEqual(req["max_tokens"], 16000)
        for forbidden in ("temperature", "top_p", "top_k", "thinking", "fallbacks"):
            self.assertNotIn(forbidden, req)

    def test_reads_text_by_block_type(self):
        llm, _ = client(self.tmp, lambda r: json_message(GOOD))  # thinking block comes first
        result = llm.structured("B:calc", "sys", "user", calc_schema(), 1, "v")
        self.assertEqual((result.outcome, result.data), ("ok", GOOD))

    def test_truncation_decided_before_parsing_and_not_retried(self):
        truncated = message([{"type": "text", "text": '{"formula_used": "a'}], stop_reason="max_tokens")
        llm, sdk = client(self.tmp, lambda r: truncated)
        result = llm.structured("B:calc", "sys", "user", calc_schema(), 1, "v")
        self.assertEqual(result.outcome, "truncated")
        self.assertEqual(len(sdk.messages.calls), 1)
        self.assertEqual(result.calls[0]["outcome"], "truncated")

    def test_refusal_not_retried(self):
        llm, sdk = client(self.tmp, lambda r: message([], stop_reason="refusal"))
        self.assertEqual(llm.structured("B:calc", "s", "u", calc_schema(), 1, "v").outcome, "refusal")
        self.assertEqual(len(sdk.messages.calls), 1)

    def test_parse_retry_uses_new_key_then_replays_from_cache(self):
        replies = iter([message([{"type": "text", "text": "not json"}]), json_message(GOOD)])
        llm, sdk = client(self.tmp, lambda r: next(replies))
        result = llm.structured("B:calc", "sys", "user", calc_schema(), 1, "v")
        self.assertEqual(result.outcome, "ok")
        self.assertEqual([c["attempt"] for c in result.calls], [1, 2])
        self.assertEqual([c["outcome"] for c in result.calls], ["parse_error", "ok"])
        self.assertTrue(result.calls[0]["cache_key"].endswith("_r1_a1"))
        self.assertTrue(result.calls[1]["cache_key"].endswith("_r1_a2"))
        self.assertIn(RETRY_NOTE, sdk.messages.calls[1]["messages"][0]["content"])

        # --resume: a fresh client on the same cache replays both attempts without any API call
        replay, replay_sdk = client(self.tmp, lambda r: AssertionError("no API call expected on replay"))
        again = replay.structured("B:calc", "sys", "user", calc_schema(), 1, "v")
        self.assertEqual(again.outcome, "ok")
        self.assertEqual([c["outcome"] for c in again.calls], ["parse_error", "ok"])
        self.assertTrue(all(c["cached"] for c in again.calls))
        self.assertEqual(replay_sdk.messages.calls, [])

    def test_two_parse_failures(self):
        llm, sdk = client(self.tmp, lambda r: message([{"type": "text", "text": "{}"}]))
        result = llm.structured("B:calc", "sys", "user", calc_schema(), 1, "v")
        self.assertEqual(result.outcome, "parse_error")
        self.assertEqual(len(sdk.messages.calls), 2)

    def test_repeat_index_is_part_of_the_key(self):
        llm, sdk = client(self.tmp, lambda r: json_message(GOOD))
        llm.structured("B:calc", "sys", "user", calc_schema(), 1, "v")
        llm.structured("B:calc", "sys", "user", calc_schema(), 2, "v")
        self.assertEqual(len(sdk.messages.calls), 2)  # r2 does not hit r1's cache

    def test_api_error_is_not_cached(self):
        error = anthropic.APIConnectionError(request=None)
        llm, _ = client(self.tmp, lambda r: error)
        result = llm.structured("B:calc", "sys", "user", calc_schema(), 1, "v")
        self.assertEqual(result.outcome, "api_error")
        self.assertEqual(list((Path(self.tmp) / "cache").glob("*.json")), [])
        retry, sdk = client(self.tmp, lambda r: json_message(GOOD))
        self.assertEqual(retry.structured("B:calc", "sys", "user", calc_schema(), 1, "v").outcome, "ok")
        self.assertEqual(len(sdk.messages.calls), 1)

    def test_tool_loop_sends_assistant_content_back_unchanged(self):
        first_content = [
            {"type": "thinking", "thinking": "", "signature": "sig-1"},
            {"type": "text", "text": "Calling the tool.", "citations": None},
            {"type": "tool_use", "id": "toolu_1", "name": "calculate_ratio", "input": {"a": 2, "b": 1}},
        ]
        replies = iter([message(first_content, stop_reason="tool_use"),
                        message([{"type": "text", "text": "The ratio is 2."}])])
        llm, sdk = client(self.tmp, lambda r: next(replies))
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
        llm, _ = client(self.tmp, lambda r: message([{"type": "text", "text": "2.0"}]))
        out = llm.tool_loop("C_tool", "sys", "user", [{"name": "calculate_ratio"}], lambda n, a: {}, 1, "v")
        self.assertEqual(out["outcome"], "no_tool_call")


if __name__ == "__main__":
    unittest.main()
