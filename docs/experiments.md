# A/B/C/D experiments: design, how to run, output contract

This document covers the agent side of FinVerify: the four experiment versions,
how to run them, and the exact format of the predictions handed to the
analysis (Verifier, metrics, error taxonomy). Ground truth and cleaning are
documented in `data/README.md`.

## Versions

| Version | Pipeline | LLM calls per (filing x ratio) |
|---|---|---|
| **A** Direct LLM | statements -> one call returns items used, formula, steps, result | 1 |
| **B** Extraction + LLM math | shared extraction -> input gate -> LLM computes from values as reported + scale | 2 (1 if gated) |
| **C** Extraction + Python | same extraction record as B -> same gate -> normalise to USD -> `agent/calculator.py` | 1 (shared with B) |
| **C_tool** (P1) | same extraction -> same gate -> LLM calls the calculator through a strict tool | 2 (1 if gated) |
| **D** Pipeline check | ground-truth inputs in the extraction schema -> exactly C's downstream path | 0 |

* **B vs C is the clean comparison**: same extraction record (`extraction_id`), same
  gate, same denominator; the only difference is who does the arithmetic.
  C_tool has the same number of LLM calls as B.
* **A can abstain**: it uses the same definitions ("if a line item is missing, use null").
  `result = null` is recorded as `missing_input`. A has no gate, so it may abstain less
  often than B/C; see the denominator rule below.
* **Model**: the reported runs use a free local model, `qwen3:8b` served by
  [Ollama](https://ollama.com), with thinking disabled for every version. So A has
  no hidden reasoning: its only reasoning is the visible `calculation_steps` field it
  writes before `result`. (The optional Anthropic backend cannot disable thinking;
  if it is ever used, A becomes "a single call with built-in hidden reasoning".)
* **D cannot show the calculator is correct.** The ground-truth ratios were
  themselves computed with `agent/calculator.py`, so D scores 100% by construction.
  D checks the schema -> gate -> normalise -> calculator -> output path. Calculator
  correctness is tested against hand-computed values in
  `tests/test_calculator_handcheck.py` (the 12 hand-checked ratios from
  `docs/ground_truth_verification.md`, including HD's average-equity ROE, plus the toy filings).

## Input gate (B, C, C_tool share one implementation: `agent/gate.py`)

Structured outputs guarantee that every required input appears exactly once
(`items` is an object keyed by input name, all keys required). The gate then
normalises each item conservatively and flags `inconsistent: true`:

| Item as returned | Treated as |
|---|---|
| `extracted` but `value_reported` is null | `not_found` |
| `extracted` but `scale` is null | `unit_unclear` |
| `not_found` but a number was given | `not_found` (number ignored) |

Then: any `not_found` -> `missing_input` (also when `unit_unclear` is present too);
otherwise any `unit_unclear` -> `unit_mismatch`; otherwise the gate passes. When it
blocks, the B / C_tool calculation call is not made, so all three versions record
the same status.

## Prompts

`agent/prompts/*.v1.txt`. Every template embeds the same `definitions.v1.txt`
(GT conventions only: formulas, average balances for ROA/ROE, equity and net
income attributable to the parent excluding noncontrolling interests, decimals
not percentages, null instead of guessing). The prompts contain no company names,
no row labels copied from the sample filings and no sample numbers. The
definitions were written after seeing how the sample filings differ (for example
noncontrolling interests); disclose this in the report.

`prompt_version` = `<file stem>@<sha256 of template + definitions, 8 hex>`.
Prompts are developed only on the toy filings and the dev filing
(`AAPL_2025-09-27_10K`), then frozen with `python -m experiments.freeze_prompts`.
The runner refuses to run LLM versions on test filings unless the frozen versions
in `manifests/experiment_config.json` equal the current prompt files.

## Running

```bash
python -m venv .venv && .venv/Scripts/activate      # Windows; source .venv/bin/activate elsewhere
pip install -r requirements.txt                      # Python >= 3.10
# install Ollama from https://ollama.com (Windows: winget install Ollama.Ollama), then:
ollama pull qwen3:8b                                 # ~5.2 GB, runs on an 8 GB GPU; no API key, no cost
export PYTHONUTF8=1                                  # Windows: read the cleaned filings as UTF-8
# optional paid backend: --provider anthropic with ANTHROPIC_API_KEY in the environment or the gitignored .env

python -m unittest discover -s tests -t .            # offline tests, no API key needed
python -m experiments.run --run-id d-check --versions D --filings all,toy   # no API key needed

python -m experiments.run --run-id dev-01 --filings dev,toy                 # prompt development
python -m experiments.sanity_summary --run-id dev-01                        # toy + dev results only
python -m experiments.freeze_prompts                                        # then commit the config

python -m experiments.run --run-id main --filings all,toy --repeats 1      # formal run, r1 = primary
python -m experiments.run --run-id main --filings all,toy --repeats 2,3 --resume
python -m experiments.sanity_summary --run-id main
```

* `--filings`: `all` (14 real), `test` (real minus dev), `dev`, `toy`, or filing ids.
* `--versions`: any subset of `A,B,C,C_tool,D` (default: all).
* The cache lives in `runs/<run_id>/cache/`. A new run_id starts empty; an existing
  run_id is only reopened with `--resume`, which replays every cached response
  (including failed attempts) exactly. A retry is always a new cache key.
* `sanity_summary` deliberately prints no accuracy for the test split.

## Reproducibility

Fixed in `manifests/experiment_config.json` (provider `ollama`): model `qwen3:8b`,
`temperature` 0 (greedy decoding), `seed` 42, `num_ctx` 8192, `max_tokens` 4096
(Ollama `num_predict`, identical for every step of every version), thinking off,
prompt versions, primary repeat (r1), dev filing. Each run records the Ollama
server version and the exact model digest in `run_meta.json`. Greedy decoding with
a fixed seed is close to deterministic on the same machine, but not guaranteed
across hardware or drivers; repeats r2/r3 measure how much it varies in practice.
Every raw response is kept in `runs/<run_id>/cache/` and referenced from each
prediction (`llm_calls[].raw_path`). The data never leaves the machine and there is
no API cost or rate limit.

Per LLM step the decision order is fixed: API error (no response, not cached) ->
`stop_reason` (`max_tokens` -> `truncated`, `refusal` -> `refusal`; never retried) ->
JSON parse + schema check (one retry with "return only JSON", same for every
version; second failure -> `parse_failure`).

## Leakage controls

* `tests/test_leakage_runtime.py` blocks every file read whose path contains
  `ground_truth`, reloads and runs A / B / C / C_tool offline, and expects no error.
* `agent/leak_guard.py` checks the exact prompt before sending: short prefixes with
  boundaries (`<ix:`, `ix:X`, `dei:X`), substrings that never occur in English
  (`us-gaap`, `xbrli`, `contextref`, `unitref`, `xmlns`, case-insensitive, which also
  catches `us-gaap_Revenues`), and the camel-case XBRL concept names the ground truth
  uses (`AssetsCurrent`, `NetIncomeLoss`, ...; one-word tags such as `Revenues` are
  excluded because they occur in visible text). A hit records `leak_check_failed`
  for that filing and the run continues. The 14 real and 6 toy prompts have no hits.
* Prompts contain only the rendered statements and the target fiscal year end, never
  the URL, accession number or CIK. No web tools are given to the model.

## Output contract (`results/<run_id>/predictions.jsonl`)

One JSON object per line, one line per (version x filing x ratio x repeat):

```json
{
  "version": "C", "filing_id": "AAPL_2026-09-26_10K", "company": "AAPL", "fiscal_year": 2026,
  "period_end": "2026-09-26", "ratio": "roe", "status": "ok", "predicted_ratio": 1.4226,
  "formula_used": "net_income / ((equity_begin + equity_end) / 2)", "calculation_steps": null,
  "extraction_id": "ext_AAPL_2026-09-26_10K_roe_r1",
  "items": {
    "net_income": {"value_reported": 132175, "scale": "millions", "period_end": "2026-09-26",
                   "source_statement": "income_statement", "source_label": "Net income",
                   "status": "extracted", "inconsistent": false, "concept": "net_income",
                   "value_usd": 132175000000.0}
  },
  "llm_calls": [{"step": "extract", "attempt": 1, "prompt_version": "extract.v1@...",
                 "provider": "ollama", "model": "qwen3:8b", "temperature": 0, "seed": 42, "num_ctx": 8192,
                 "max_tokens": 4096, "think": false, "cache_key": "...", "cached": false,
                 "message_id": null, "stop_reason": "end_turn", "input_tokens": 0, "output_tokens": 0,
                 "outcome": "ok", "raw_path": "runs/<run_id>/cache/<key>.json"}],
  "run_id": "main", "repeat_idx": 1, "is_primary": true, "split": "test"
}
```

(`items` shows one input here; every row holds all required inputs of its ratio.)

| Field | Values |
|---|---|
| `version` | `A`, `B`, `C`, `C_tool`, `D` |
| `split` | `dev`, `test`, `toy` |
| item `status` | `extracted`, `not_found`, `unit_unclear` (plus `inconsistent`, and `status_reported` when the gate changed it) |
| prediction `status` | `ok`, `missing_input`, `unit_mismatch`, `undefined_division`, `low_confidence_negative_equity`, `parse_failure`, `truncated`, `refusal`, `api_error`, `no_tool_call`, `leak_check_failed` |
| `llm_calls[].outcome` | `ok`, `parse_error`, `truncated`, `refusal`, `api_error`, `tool_use` (intermediate C_tool round) |

Notes for the analysis:

* `items[*].value_usd` is comparable with `data/ground_truth.json` `inputs[*].value`
  (raw USD). `period_end`, `source_statement`, `source_label` are the evidence trail.
* A / B carry `formula_used` and `calculation_steps` as written by the model; C / C_tool / D
  carry the fixed formula.
* C_tool rows also carry `tool_runs`, `tool_args_match_extraction`, `final_text`, `loop_outcome`.
* B, C and C_tool rows of one (filing, ratio, repeat) repeat the shared extraction call
  in `llm_calls`; deduplicate by `cache_key` when counting tokens.
* Toy filings are synthetic (`experiments/toy_filings/`, answers in `expected.json`),
  for known-answer checks only.

## Proposed metric rules (to agree before the formal run)

* Same denominator for every version: all ground-truth-available (filing x ratio)
  records of the split.
* Correct = a value within the tolerance (proposed: relative error <= 1%).
  Every other status counts as incorrect.
* Report separately: abstention rate (`missing_input`, `unit_mismatch`, including
  A's `result = null`), failure rate (`parse_failure`, `truncated`, `refusal`,
  `api_error`, `no_tool_call`), preprocessing failures (`leak_check_failed`).
* Headline: the 13 non-dev filings (52 records). Robustness: also without any AAPL
  filing (AAPL_2026 shares the dev filing's company and format).
