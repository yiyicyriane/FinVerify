"""Gate cases: B, C and C_tool must always end with the same status for the same extraction."""

import math
import unittest

from agent import version_b, version_c, version_c_tool
from agent.gate import apply_gate

DOC = {"filing_id": "T_2025-12-31_10K", "company": "T", "fiscal_year": 2025, "period_end": "2025-12-31"}


def item(value, scale="millions", status="extracted"):
    return {"value_reported": value, "scale": scale, "period_end": "2025-12-31",
            "source_statement": "balance_sheet", "source_label": "x", "status": status}


class NoCallLLM:
    """Fails the test if the calculation LLM is called while the gate should block."""

    def structured(self, *a, **k):
        raise AssertionError("B calculation LLM must not be called when the gate blocks")

    def tool_loop(self, *a, **k):
        raise AssertionError("C_tool LLM must not be called when the gate blocks")


def extraction(items):
    return {"extraction_id": "ext_test", "outcome": "ok", "items": items, "llm_calls": []}


class GateTests(unittest.TestCase):
    def assert_blocked_everywhere(self, items, ratio, expected):
        blocked, _ = apply_gate(ratio, items)
        self.assertEqual(blocked, expected)
        ext = extraction(items)
        b = version_b.calc(ext, DOC, ratio, NoCallLLM(), 1)
        c = version_c.run(ext, DOC, ratio)
        ct = version_c_tool.run(ext, DOC, ratio, NoCallLLM(), 1)
        self.assertEqual({b["status"], c["status"], ct["status"]}, {expected})
        return b, c

    def test_all_extracted_passes(self):
        items = {"current_assets": item(200), "current_liabilities": item(100)}
        blocked, normalised = apply_gate("current_ratio", items)
        self.assertIsNone(blocked)
        self.assertFalse(any(i["inconsistent"] for i in normalised.values()))
        c = version_c.run(extraction(items), DOC, "current_ratio")
        self.assertEqual((c["status"], c["predicted_ratio"]), ("ok", 2.0))

    def test_one_not_found(self):
        items = {"current_assets": item(200), "current_liabilities": item(None, None, "not_found")}
        self.assert_blocked_everywhere(items, "current_ratio", "missing_input")

    def test_unit_unclear_only(self):
        items = {"current_assets": item(200), "current_liabilities": item(100, None, "unit_unclear")}
        self.assert_blocked_everywhere(items, "current_ratio", "unit_mismatch")

    def test_not_found_wins_over_unit_unclear(self):
        items = {"net_income": item(None, None, "not_found"),
                 "equity_begin": item(5, None, "unit_unclear"), "equity_end": item(6)}
        self.assert_blocked_everywhere(items, "roe", "missing_input")

    def test_extracted_with_null_value_is_not_found(self):
        items = {"current_assets": item(None), "current_liabilities": item(100)}
        b, _ = self.assert_blocked_everywhere(items, "current_ratio", "missing_input")
        self.assertTrue(b["items"]["current_assets"]["inconsistent"])
        self.assertEqual(b["items"]["current_assets"]["status_reported"], "extracted")

    def test_extracted_with_null_scale_is_unit_unclear(self):
        items = {"current_assets": item(200, None), "current_liabilities": item(100)}
        b, _ = self.assert_blocked_everywhere(items, "current_ratio", "unit_mismatch")
        self.assertTrue(b["items"]["current_assets"]["inconsistent"])

    def test_not_found_with_number_ignores_number(self):
        items = {"current_assets": item(200, "millions", "not_found"), "current_liabilities": item(100)}
        b, _ = self.assert_blocked_everywhere(items, "current_ratio", "missing_input")
        self.assertTrue(b["items"]["current_assets"]["inconsistent"])

    def test_missing_key_is_not_found(self):
        self.assert_blocked_everywhere({"current_assets": item(200)}, "current_ratio", "missing_input")

    def test_mixed_scales_are_normalised_by_c(self):
        items = {"current_assets": item(4500, "thousands"), "current_liabilities": item(3, "millions")}
        c = version_c.run(extraction(items), DOC, "current_ratio")
        self.assertEqual(c["status"], "ok")
        self.assertTrue(math.isclose(c["predicted_ratio"], 1.5))

    def test_failed_extraction_gives_same_status(self):
        ext = {"extraction_id": "e", "outcome": "truncated", "items": None, "llm_calls": []}
        statuses = {version_b.calc(ext, DOC, "roe", NoCallLLM(), 1)["status"],
                    version_c.run(ext, DOC, "roe")["status"],
                    version_c_tool.run(ext, DOC, "roe", NoCallLLM(), 1)["status"]}
        self.assertEqual(statuses, {"truncated"})


if __name__ == "__main__":
    unittest.main()
