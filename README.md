# SBR: Skill Behavioral Rules

Source code for malicious agent skill detection:

**SKILL.md → behavior graph → learned local predicates → joint rule matches → sparse weighted decision.**

This source-only repository contains the implementation, extraction prompts, tests, and documentation. It contains no pretrained models, datasets, benchmark documents, reference predictions, or experiment outputs. Provide your own input data and train a model before detection.

The method uses native unary node predicates and canonical structural candidate order. PCA (partial completeness assumption confidence) is not computed or used for filtering or ordering. Training fits the complete candidate matrix before lossless joint-match compilation.

[中文说明](README_zh.md) · [Method](docs/METHOD.md) · [Input formats](docs/INPUTS.md)

## Install and test

Use Python 3.12:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install '.[test]'
python -m pytest -q
python scripts/verify_release.py
```

On Windows, activate with `.venv\Scripts\Activate.ps1`. Tests create small artificial inputs and model parameters in temporary directories; they do not download or require a pretrained model or dataset. Numerical dependencies are pinned in `pyproject.toml`. Exact fitted parameters can differ between operating systems and numerical-library builds.

## Train with your own data

Prepare a graph archive and a family-disjoint training/test manifest using [the input format](docs/INPUTS.md), then run:

```bash
skill-rule train --manifest /path/to/dataset.json --output runs/trained --workers 4
```

The output directory must not already exist. Training exports the deployment model to `runs/trained/model` and saves training diagnostics alongside it. No existing model or test reference scores are loaded during fitting. Missing graphs cause an error by default. The explicit `--missing-graphs historical-zero-hits` option is only for reproducing a dataset that already used that convention.

## Detect a skill

Configure an OpenAI-compatible service for graph extraction and explicitly supply your trained model:

```bash
export SKILL_RULE_LLM_BASE_URL=https://your-service.example/v1
export SKILL_RULE_LLM_MODEL=your-model-name
# Set SKILL_RULE_LLM_API_KEY privately if your service requires authentication.
skill-rule detect /path/to/skill --model runs/trained/model --output runs/detection.json
```

The extractor reads `SKILL.md` and explicitly referenced executable filenames under `scripts/`. It does not read script bodies, execute skill commands, or fetch linked content. An invalid or empty extraction fails without returning a benign verdict. The output includes score, threshold, local facts, matched rules and weights, variable bindings, and a sibling graph file with extraction metadata. `--top-rules -1` returns every matched rule.

A configured service is required for new-document extraction. Cached-graph detection and training need no LLM or GPU:

```bash
skill-rule extract /path/to/skill --output runs/skill.graph.json
skill-rule detect-graph runs/skill.graph.json --model runs/trained/model --output runs/result.json
skill-rule evaluate --manifest /path/to/dataset.json --model runs/trained/model --split test --output runs/test.json
skill-rule compress-model --model runs/trained/model --output runs/compiled
```

Detection, evaluation, and compilation require `--model`; no model is bundled or downloaded automatically. The Python interfaces also require an explicit directory: `Detector(model_dir)` and `compress_model(output, model_dir)`.

To compare two locally available fitted bundles:

```bash
python scripts/compare_bundles.py /path/to/candidate --reference /path/to/reference
```

## Method and scope

Local TF-IDF MIL scores produce unary node and binary edge predicates at thresholds 0.3, 0.5, 0.7, and 0.9. Connected rules have at most two body atoms and base-behavior heads. Mining uses support, standard confidence, and head coverage, then orders candidates by canonical structural signature. The L1 selector is fit before logically equivalent joint-match features are compiled.

A feature requires one consistent typed variable binding satisfying both the rule body and head. The final score is the sigmoid of the intercept plus the weighted binary matches. There is no forward-chaining stage or separate raw-text fallback detector. See [METHOD.md](docs/METHOD.md) for the fitting and validation boundaries.

Keep local datasets, models and generated outputs outside version control. Common artifact directories and binary model formats are ignored by this repository.
