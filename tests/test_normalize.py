import unittest

from agent.normalize import enrich_items, parse_number, to_usd


class NormalizeTests(unittest.TestCase):
    def test_parse_number(self):
        self.assertEqual(parse_number(147957), 147957.0)
        self.assertEqual(parse_number("$147,957"), 147957.0)
        self.assertEqual(parse_number("(14,264)"), -14264.0)
        self.assertEqual(parse_number("-5.5"), -5.5)
        self.assertIsNone(parse_number(None))
        self.assertIsNone(parse_number("—"))
        with self.assertRaises(ValueError):
            parse_number("abc")
        with self.assertRaises(ValueError):
            parse_number(True)

    def test_to_usd(self):
        self.assertEqual(to_usd(147957, "millions"), 147_957_000_000)
        self.assertEqual(to_usd("4,500", "thousands"), 4_500_000)
        self.assertEqual(to_usd(12, "units"), 12)
        self.assertIsNone(to_usd(None, "millions"))
        self.assertIsNone(to_usd(5, None))
        with self.assertRaises(ValueError):
            to_usd(5, "percent")

    def test_enrich_adds_concept_and_usd(self):
        out = enrich_items({"equity_begin": {"value_reported": 1044, "scale": "millions"}})
        self.assertEqual(out["equity_begin"]["concept"], "equity")
        self.assertEqual(out["equity_begin"]["value_usd"], 1_044_000_000)

    def test_does_not_fix_percentages(self):
        # A ratio written as a percentage is not silently rescaled anywhere.
        self.assertEqual(parse_number("46.91"), 46.91)


if __name__ == "__main__":
    unittest.main()
