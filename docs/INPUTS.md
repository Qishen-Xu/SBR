# User-supplied input formats

No data or fitted model is included in this repository. The structures below describe the interface, not an evaluation dataset.

## Skill directory and base graph

A skill directory has a root `SKILL.md`; optional `scripts/` filenames provide extraction hints. `skill-rule extract` creates the base graph. A base graph contains `skill_name`, `nodes`, and `edges`. Nodes require `name` and `type`, with optional `constraints` containing text. Edges require `source`, `target`, and `edge_type`, with optional `description`.

Behavior relations are `READ`, `WRITE`, `EXEC`, `FETCH`, `SEND`, `SEARCH`, `ASK`, and `SPAWN`. Entity types and extraction validation are implemented in `extraction/schemas.py`. Supply base graphs before ML predicate augmentation.

## Training/evaluation manifest

The manifest is a JSON object with a relative `graph_archive` path and a `samples` list. Each sample has:

- `key`: unique string matching one graph's `skill_name`.
- `label`: 0 (benign) or 1 (malicious).
- `family`: group identifier; train and test families must be disjoint.
- `split`: `train` or `test`.
- `graph_status`: normally `available`.

The graph archive is gzip-compressed JSONL with one base graph per line. Archive keys and manifest keys must match exactly. `graph_archive_sha256` optionally verifies the archive bytes. Training requires both classes and enough families for the three-fold grouped procedures; choose sufficient class/group coverage for each fold.

Missing graphs should normally be fixed before fitting. The explicit historical compatibility policy supports `missing_source_graph` and `empty_source_graph` with empty graph objects; it maps them to zero rule-hit vectors. `partial_source_graph` preserves recorded partial input under that same policy. These options do not relax failure handling for a newly extracted skill.

## Model directory

`skill-rule train` creates `model/` containing `bundle.json`, `rules.json`, `selector.json`, `text_vocabulary.json`, and `text_parameters.npz`. The bundle records checksums; the NumPy arrays are loaded with `allow_pickle=False`. Pass this directory with `--model` for detection or evaluation. Model weights and data should remain outside the repository.

## Optional prediction comparison

For `evaluate --reference`, supply a JSON list containing the same sample keys and labels as the selected split, with `key`, `label`, `prediction`, and `score`. These records are used only for comparison, not training. A comparison can fail when the trained model, graph extraction, split, or numerical environment differs.
