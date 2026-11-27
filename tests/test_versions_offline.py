"""All versions end to end on toy and real filings with a mock SDK (no API key, no network)."""

import json
import math
import tempfile
import unittest
from pathlib import Path

from agent import version_a, version_b, version_c, version_c_tool, version_d
from agent.llm_client import LLMClient
from agent.records import doc_meta
from experiments import run as runner
from tests.helpers import CLEANED_DIR, TOY_DIR, FakeSDK, answer_responder, load_json, not_found_responder


def toy_docs():
    return {p.stem: load_json(p) for p in sorted(TOY_DIR.glob("TOY-*.json"))}


class OfflineVersionTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def llm(self, responder, name="cache"):
        return LLMClient(self.tmp / name, model="mock-model", effort="medium", max_tokens=16000,
                         sdk=FakeSDK(responder))

    def test_d_equals_ground_truth_on_all_56_records(self):
        gt = version_d.load_ground_truth()
        self.assertEqual(len(gt), 56)
        for (filing_id, ratio), record in gt.items():
            doc = load_json(CLEANED_DIR / f"{filing_id}.json")
            pred = version_d.run(version_d.items_from_ground_truth(record), doc_meta(doc), ratio)
            self.assertEqual(pred["status"], "ok")
            self.assertTrue(math.isclose(pred["predicted_ratio"], record["ground_truth_ratio"], rel_tol=1e-12))

    def test_all_versions_on_toys_with_correct_answers(self):
        expected = load_json(TOY_DIR / "expected.json")
        for filing_id, doc in toy_docs().items():
            for ratio, answer in expected[filing_id].items():
                with self.subTest(f"{filing_id} {ratio}"):
                    llm = self.llm(answer_responder(answer["items"], answer["expected_ratio"]),
                                   f"{filing_id}_{ratio}")
                    a = version_a.run(doc, ratio, llm, 1)
                    ext = version_b.extract(doc, ratio, llm, 1)
                    self.assertEqual(ext["outcome"], "ok")
                    b = version_b.calc(ext, doc, ratio, llm, 1)
                    c = version_c.run(ext, doc, ratio)
                    ct = version_c_tool.run(ext, doc, ratio, llm, 1)
                    d = version_d.run(answer["items"], doc_meta(doc), ratio)
                    self.assertEqual(c["status"], answer["expected_status"])
                    self.assertEqual(d["status"], answer["expected_status"])
                    for pred in (a, b, c, ct, d):
                        if answer["expected_ratio"] is None:
                            self.assertIsNone(pred["predicted_ratio"], pred["version"])
                        else:
                            self.assertTrue(math.isclose(pred["predicted_ratio"], answer["expected_ratio"],
                                                         rel_tol=1e-9), pred["version"])
                    if answer["expected_status"] == "missing_input":  # gate blocked: no B / C_tool calc call
                        self.assertEqual([c["step"] for c in b["llm_calls"]], ["extract"])
                        self.assertEqual(b["status"], c["status"])
                        self.assertEqual(ct["status"], c["status"])
                    if ct["status"] in ("ok", "low_confidence_negative_equity", "undefined_division"):
                        self.assertTrue(ct["tool_args_match_extraction"])
                    self.assertEqual(c["extraction_id"], b["extraction_id"])
                    self.assertEqual(ct["extraction_id"], b["extraction_id"])

    def test_a_null_result_is_missing_input(self):
        doc = load_json(CLEANED_DIR / "AAPL_2025-09-27_10K.json")
        a = version_a.run(doc, "roe", self.llm(not_found_responder), 1)
        self.assertEqual((a["status"], a["predicted_ratio"]), ("missing_input", None))

    def test_runner_end_to_end_and_existing_run_guard(self):
        args = ["--run-id", "e2e", "--versions", "A,B,C,C_tool,D", "--filings", "toy",
                "--runs-dir", str(self.tmp / "runs"), "--results-dir", str(self.tmp / "results"),
                "--concurrency", "2"]
        self.assertEqual(runner.main(args, sdk=FakeSDK(not_found_responder)), 0)
        lines = (self.tmp / "results" / "e2e" / "predictions.jsonl").read_text(encoding="utf-8").splitlines()
        preds = [json.loads(line) for line in lines]
        self.assertEqual(len(preds), 6 * 4 * 5)
        self.assertTrue(all(p["split"] == "toy" and p["repeat_idx"] == 1 and p["is_primary"] for p in preds))
        self.assertTrue(all(p["status"] == "missing_input" for p in preds if p["version"] in ("A", "B", "C", "C_tool")))
        self.assertTrue((self.tmp / "runs" / "e2e" / "cache").is_dir())
        with self.assertRaises(SystemExit):  # same run_id again without --resume
            runner.main(args, sdk=FakeSDK(not_found_responder))
        # --resume replays the cache: no API call at all
        def no_call(request):
            raise AssertionError("resume must replay from cache")
        self.assertEqual(runner.main(args + ["--resume"], sdk=FakeSDK(no_call)), 0)

    def test_runner_refuses_unfrozen_prompts_on_test_filings(self):
        config = load_json(runner.CONFIG_PATH)
        if config.get("frozen_prompts"):
            self.skipTest("prompts are frozen")
        with self.assertRaises(SystemExit):
            runner.main(["--run-id", "t", "--versions", "A", "--filings", "test",
                         "--runs-dir", str(self.tmp / "runs"), "--results-dir", str(self.tmp / "results")],
                        sdk=FakeSDK(not_found_responder))


if __name__ == "__main__":
    unittest.main()
