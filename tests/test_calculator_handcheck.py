"""Calculator correctness against values computed BY HAND, not by our code.

Version D cannot show this: the ground-truth ratios were computed with
agent/calculator.py, so a calculator bug would hit both. Sources:
  * docs/ground_truth_verification.md: 12 ratios hand-calculated from the 10-K
    text (Apple FY2025, NVIDIA FY2026, Home Depot FY2024), values in USD millions;
  * experiments/toy_filings/build_toys.py: hand-computed toy answers.
"""

import json
import math
import unittest

from agent.calculator import current_ratio, gross_profit_margin, roa, roe
from agent.normalize import to_usd
from agent.calculator import RATIOS
from tests.helpers import TOY_DIR

HAND = [
    # (label, function, args in USD millions, hand result rounded to 4 decimals)
    ("AAPL FY2025 current ratio", current_ratio, (147957, 165631), 0.8933),
    ("AAPL FY2025 gross margin", gross_profit_margin, (416161, 220960), 0.4691),
    ("AAPL FY2025 ROA", roa, (112010, 364980, 359241), 0.3093),
    ("AAPL FY2025 ROE", roe, (112010, 56950, 73733), 1.7142),
    ("NVDA FY2026 current ratio", current_ratio, (125605, 32163), 3.9053),
    ("NVDA FY2026 gross margin", gross_profit_margin, (215938, 62475), 0.7107),
    ("NVDA FY2026 ROA", roa, (120067, 111601, 206803), 0.7542),
    ("NVDA FY2026 ROE", roe, (120067, 79327, 157293), 1.0149),
    ("HD FY2024 current ratio", current_ratio, (31683, 28661), 1.1054),
    ("HD FY2024 gross margin", gross_profit_margin, (159514, 106206), 0.3342),
    ("HD FY2024 ROA", roa, (14806, 76530, 96119), 0.1715),
    ("HD FY2024 ROE (average equity)", roe, (14806, 1044, 6640), 3.8537),
]


class CalculatorHandCheck(unittest.TestCase):
    def test_real_filings_hand_values(self):
        for label, func, args, expected in HAND:
            with self.subTest(label):
                value, status = func(*[a * 1_000_000 for a in args])
                self.assertEqual(status, "ok")
                self.assertEqual(round(value, 4), expected)

    def test_hd_roe_uses_average_not_ending_equity(self):
        value, _ = roe(14806, 1044, 6640)
        self.assertNotAlmostEqual(value, 14806 / 6640, places=2)  # end-of-year ROE would be ~2.23

    def test_toy_hand_answers(self):
        expected = json.loads((TOY_DIR / "expected.json").read_text(encoding="utf-8"))
        for filing_id, ratios in expected.items():
            for ratio, answer in ratios.items():
                with self.subTest(f"{filing_id} {ratio}"):
                    func, names = RATIOS[ratio]
                    args = [to_usd(answer["items"][n]["value_reported"], answer["items"][n]["scale"]) for n in names]
                    value, status = func(*args)
                    self.assertEqual(status, answer["expected_status"])
                    if answer["expected_ratio"] is None:
                        self.assertIsNone(value)
                    else:
                        self.assertTrue(math.isclose(value, answer["expected_ratio"], rel_tol=1e-12))


if __name__ == "__main__":
    unittest.main()
