# FinVerify

## Experiments (versions A / B / C / C_tool / D)

Four controlled versions that compute the same financial ratios from the same
cleaned 10-K statements and are scored against the same XBRL ground truth.
Design, commands and the predictions format for the analysis:
[docs/experiments.md](docs/experiments.md). Ground truth and cleaning:
[data/README.md](data/README.md).

```bash
pip install -r requirements.txt                    # Python >= 3.10
python -m unittest discover -s tests -t .          # offline, no model needed
python -m experiments.run --run-id d-check --versions D --filings all,toy
```

The LLM versions run on a free local model: install [Ollama](https://ollama.com)
and `ollama pull qwen3:8b` (no API key, no cost). Raw responses are written to
`runs/` (gitignored); compact predictions to `results/`.

## AI tool use

Claude Code (Anthropic) assisted with writing the experiment code in `agent/`
(versions, gate, LLM client, prompts), `experiments/`, `tests/` and
`docs/experiments.md`.
