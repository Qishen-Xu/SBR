"""Lossless compilation after fitting the complete rule classifier."""

import json
from pathlib import Path
import shutil

import numpy as np

from .model import Detector, seal_bundle
from .rules.compression import semantic_groups
from .rules.arity import normalize_rule


def compress_fitted_rules(rules, selector):
    """Merge equivalent match features without fitting or selecting parameters.

    Representatives refer to columns of the original training matrix. Retain
    this mapping so training can independently check its fitted logits.
    """
    original_weights = np.asarray(selector["coefficients"], dtype=float)
    if original_weights.shape != (len(rules),) or not np.isfinite(original_weights).all():
        raise ValueError("Rule and weight alignment is invalid")
    representatives, groups, _ = semantic_groups(rules)
    weights = np.array([original_weights[group].sum() for group in groups])
    if not np.isfinite(weights).all():
        raise ValueError("Nonfinite merged rule weight")
    active = np.flatnonzero(weights)
    compiled = []
    for index in active:
        representative = rules[representatives[index]]
        members = [dict(rule_key=rules[i]["rule_key"], weight=float(original_weights[i]))
                   for i in groups[index]]
        if len(groups[index]) == 1 and "merged_match_features" in representative:
            members = representative["merged_match_features"]
        compiled.append({**normalize_rule(representative), "merged_match_features": members})
    compact_selector = {
        **selector,
        "coefficients": weights[active].tolist(),
        "nonzero_features": len(active),
        "positive_features": int(np.sum(weights > 0)),
        "negative_features": int(np.sum(weights < 0)),
    }
    compression = dict(
        source_rules=len(rules), source_active_rules=int(np.count_nonzero(original_weights)),
        equivalent_feature_groups=len(groups), output_rules=len(compiled),
        representative_indices=[int(representatives[i]) for i in active],
        semantics="equivalent existential body AND head matches on materialized graphs; sum fitted coefficients; no refitting",
    )
    return compiled, compact_selector, compression


def compress_model(output, model_dir):
    """Compile an existing bundle into a new directory."""
    destination = Path(output)
    if destination.exists():
        raise FileExistsError(f"Output directory already exists: {destination}")
    detector = Detector(model_dir)
    rules, selector, compression = compress_fitted_rules(detector.rules, detector.selector)
    destination.mkdir(parents=True, exist_ok=False)
    for filename in ("text_vocabulary.json", "text_parameters.npz"):
        shutil.copy2(detector.directory / filename, destination / filename)
    (destination / "rules.json").write_text(json.dumps(rules, ensure_ascii=False), encoding="utf-8")
    (destination / "selector.json").write_text(json.dumps(selector, indent=2), encoding="utf-8")
    metadata = {**detector.metadata,
                "model_id": detector.metadata["model_id"] + "-compressed",
                "compression": {**compression, "source_model_id": detector.metadata["model_id"]}}
    if "audit" in metadata:
        metadata["source_audit"] = metadata.pop("audit")
    metadata["rules"] = len(rules)
    seal_bundle(destination, metadata)
    return dict(model=str(destination.resolve()), **compression)
