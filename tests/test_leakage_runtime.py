"""Leakage checks.

1. Runtime: with every file read that touches ``ground_truth`` turned into an
   error, versions A / B / C / C_tool must still run (and import) fine.
2. Prompt text: the leak patterns catch XBRL traces, do not fire on normal
   words, and find nothing in the real and toy prompts.
"""

import builtins
import importlib
import io
import pathlib
import tempfile
import unittest
from pathlib import Path

from agent import prompt_loader, version_a, version_b, version_c, version_c_tool
from agent.leak_guard import find_leaks
from agent.schemas import ratio_inputs
from data.clean_filing import to_text
from tests.helpers import CLEANED_DIR, ROOT, TOY_DIR, fake_llm, load_json, not_found_responder

RATIOS = ["current_ratio", "gross_profit_margin", "roa", "roe"]


class GroundTruthTouched(AssertionError):
    pass


class BlockGroundTruth:
    """Context manager: any open/read of a path containing 'ground_truth' raises."""

    def __enter__(self):
        self.saved = (builtins.open, io.open, pathlib.Path.open, pathlib.Path.read_text, pathlib.Path.read_bytes)
        real_open = builtins.open

        def guard(path):
            if "ground_truth" in str(path):
                raise GroundTruthTouched(f"ground truth accessed: {path}")

        def open_(file, *a, **k):
            guard(file)
            return real_open(file, *a, **k)

        real_path_open, real_read_text, real_read_bytes = self.saved[2], self.saved[3], self.saved[4]

        def path_open(self_, *a, **k):
            guard(self_)
            return real_path_open(self_, *a, **k)

        def read_text(self_, *a, **k):
            guard(self_)
            return real_read_text(self_, *a, **k)

        def read_bytes(self_, *a, **k):
            guard(self_)
            return real_read_bytes(self_, *a, **k)

        builtins.open = open_
        io.open = open_
        pathlib.Path.open = path_open
        pathlib.Path.read_text = read_text
        pathlib.Path.read_bytes = read_bytes
        return self

    def __exit__(self, *exc):
        builtins.open, io.open, pathlib.Path.open, pathlib.Path.read_text, pathlib.Path.read_bytes = self.saved
        return False


class RuntimeLeakageTests(unittest.TestCase):
    def test_guard_itself_works(self):
        with BlockGroundTruth(), self.assertRaises(GroundTruthTouched):
            (ROOT / "data" / "ground_truth.json").read_text(encoding="utf-8")

    def test_abc_never_read_ground_truth(self):
        docs = [load_json(CLEANED_DIR / "AAPL_2025-09-27_10K.json"),
                load_json(TOY_DIR / "TOY-SIMPLE_2025-12-31_10K.json")]
        with tempfile.TemporaryDirectory() as tmp, BlockGroundTruth():
            for module in (version_a, version_b, version_c, version_c_tool):
                importlib.reload(module)  # import-time reads count too
            llm = fake_llm(Path(tmp) / "cache", not_found_responder)
            for doc in docs:
                for ratio in RATIOS:
                    version_a.run(doc, ratio, llm, 1)
                    ext = version_b.extract(doc, ratio, llm, 1)
                    version_b.calc(ext, doc, ratio, llm, 1)
                    version_c.run(ext, doc, ratio)
                    version_c_tool.run(ext, doc, ratio, llm, 1)

    def test_abc_sources_do_not_mention_ground_truth(self):
        for name in ("version_a", "version_b", "version_c", "version_c_tool", "llm_client", "prompt_loader"):
            source = (ROOT / "agent" / f"{name}.py").read_text(encoding="utf-8")
            self.assertNotIn("ground_truth", source, name)


class PromptPatternTests(unittest.TestCase):
    def test_detects_xbrl_traces(self):
        for text in ['<ix:nonFraction name="x">5</ix:nonFraction>', "</ix:header>", "ix:nonfraction",
                     "id=us-gaap_Revenues", "us-gaap:Assets", 'contextRef="c1"', 'unitRef="usd"',
                     "xmlns:xbrli", "dei:DocumentType", "AssetsCurrent", "NetIncomeLoss",
                     "StockholdersEquity", "CostOfGoodsAndServicesSold"]:
            self.assertTrue(find_leaks(text), text)

    def test_no_false_positives_on_normal_text(self):
        for text in ["Appendix: see note 4", "Matrix: 3 by 3", "Six: items", "index: 4",
                     "Total current assets", "Net Operating Revenues", "Total assets",
                     "Cost of revenue", "Total stockholders' equity", "Net income attributable to"]:
            self.assertEqual(find_leaks(text), [], text)

    def test_real_and_toy_prompts_are_clean(self):
        docs = [load_json(p) for p in sorted(CLEANED_DIR.glob("*.json"))]
        docs += [load_json(p) for p in sorted(TOY_DIR.glob("TOY-*.json"))]
        self.assertEqual(len(docs), 14 + 6)
        for doc in docs:
            for ratio in RATIOS:
                for template in ("a_direct", "extract"):
                    system, user = prompt_loader.render(
                        template, ratio=ratio, target_period_end=doc["period_end"],
                        inputs_block=prompt_loader.inputs_block(ratio), statements=to_text(doc))
                    self.assertEqual(find_leaks(system + "\n" + user), [], f"{doc['filing_id']} {template}")

    def test_prompts_contain_no_sample_company_names_or_numbers(self):
        names = ["Apple", "Microsoft", "NVIDIA", "Coca-Cola", "Home Depot", "Tesla", "Nike",
                 "AAPL", "MSFT", "NVDA", "KO", "HD", "TSLA", "NKE", "Shareowners"]
        for template in prompt_loader.PROMPT_FILES:
            text = prompt_loader.load(template)
            for name in names:
                self.assertNotRegex(text, rf"\b{name}\b", f"{template} mentions {name}")
        # inputs are listed exactly as the calculator defines them
        self.assertIn("equity_begin", prompt_loader.inputs_block("roe"))
        self.assertEqual(len(prompt_loader.inputs_block("roa").splitlines()), len(ratio_inputs("roa")))


if __name__ == "__main__":
    unittest.main()
