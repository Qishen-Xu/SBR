import numpy as np
import pytest

from skill_rule_detector.rules.compression import (
    implies, semantic_core, semantic_groups,
)
from skill_rule_detector.rules.matching import pattern, witness


def atom(predicate, left="x", right="y", left_type="FILE", right_type="FILE"):
    return dict(predicate=predicate, arg1=f"?{left}:{left_type}", arg2=f"?{right}:{right_type}")


def rule(key, body, head=None):
    return dict(rule_key=key, body=body, head=head or atom("READ"))


def test_threshold_entailment_and_repeated_variables():
    simple = rule("simple", [atom("ML_EDGE_LOCAL_GE_70")])
    redundant = rule("redundant", [atom("ML_EDGE_LOCAL_GE_30"), atom("ML_EDGE_LOCAL_GE_70")])
    duplicate = rule("duplicate", [atom("ML_EDGE_LOCAL_GE_70"), atom("READ", "z", "y")])
    assert semantic_core(simple) == semantic_core(redundant) == semantic_core(duplicate)
    # Distinct variable names can bind the same entity; a shared variable
    # cannot be replaced by independent edges or by an edge of another type.
    unrelated = rule("unrelated", [atom("ML_EDGE_LOCAL_GE_70", "x", "z")])
    reversed_edge = rule("reversed", [atom("ML_EDGE_LOCAL_GE_70", "y", "x")])
    assert semantic_core(simple) != semantic_core(unrelated)
    assert semantic_core(simple) != semantic_core(reversed_edge)
    high, low = semantic_core(simple), semantic_core(rule("low", [atom("ML_EDGE_LOCAL_GE_30")]))
    assert implies(high, low) and not implies(low, high)


def test_node_predicates_require_self_loop_bindings():
    implicit = rule("implicit", [atom("ML_NODE_LOCAL_GE_50")])
    explicit = rule("explicit", [atom("ML_NODE_LOCAL_GE_50", "x", "x")], atom("READ", "x", "x"))
    assert semantic_core(implicit) == semantic_core(explicit)
    with pytest.raises(ValueError, match="ML_NODE type"):
        semantic_core(rule("bad", [atom("ML_NODE_LOCAL_GE_50", right_type="NETWORK")],
                           atom("READ", right_type="NETWORK")))


def test_feature_equivalence_is_not_horn_head_equivalence():
    left = rule("a", [atom("SEND")], atom("READ"))
    right = rule("b", [atom("READ")], atom("SEND"))
    assert semantic_core(left) == semantic_core(right)


def test_semantic_core_matches_original_on_random_materialized_graphs():
    rng = np.random.default_rng(9217)
    variants = [
        rule("a", [atom("ML_EDGE_LOCAL_GE_70")]),
        rule("b", [atom("ML_EDGE_LOCAL_GE_30"), atom("ML_EDGE_LOCAL_GE_70")]),
        rule("c", [atom("ML_EDGE_LOCAL_GE_70"), atom("READ", "z", "y")]),
        rule("d", [atom("ML_EDGE_LOCAL_GE_70", "x", "z"), atom("SEND", "z", "y")]),
        rule("e", [atom("ML_NODE_LOCAL_GE_50")]),
        rule("f", [atom("ML_NODE_LOCAL_GE_50", "x", "x"), atom("SEND", "x", "y")]),
        rule("g", [atom("ML_EDGE_LOCAL_GE_30", "x", "z"), atom("ML_EDGE_LOCAL_GE_90", "x", "y")]),
    ]
    cores = [semantic_core(r) for r in variants]
    types = {"a": "FILE", "b": "FILE", "c": "NETWORK"}
    for _ in range(350):
        index = {p: [] for p in ("READ", "SEND")}
        for left in types:
            for right in types:
                for p in ("READ", "SEND"):
                    if rng.random() < .4:
                        index[p].append((left, right))
                score = rng.random()
                for threshold in (30, 50, 70, 90):
                    if score >= threshold / 100:
                        index.setdefault(f"ML_EDGE_LOCAL_GE_{threshold}", []).append((left, right))
            score = rng.random()
            for threshold in (30, 50, 70, 90):
                if score >= threshold / 100:
                    index.setdefault(f"ML_NODE_LOCAL_GE_{threshold}", []).append((left, left))
        for original, core in zip(variants, cores):
            assert (witness(pattern(original), index, types) is not None) == (witness(core, index, types) is not None)




def test_merged_coefficients_preserve_score_with_negative_weights():
    rules = [rule("a", [atom("ML_EDGE_LOCAL_GE_70")]),
             rule("b", [atom("ML_EDGE_LOCAL_GE_30"), atom("ML_EDGE_LOCAL_GE_70")]),
             rule("c", [atom("SEND")])]
    representatives, groups, _ = semantic_groups(rules)
    x = np.array([[1, 1, 0], [0, 0, 1], [1, 1, 1]])
    weights = np.array([2., -1., -.5])
    merged = np.array([weights[g].sum() for g in groups])
    np.testing.assert_allclose(x @ weights, x[:, representatives] @ merged, atol=1e-14)


def test_fitted_compilation_preserves_parameters_and_cancels_zero_groups():
    import copy
    from skill_rule_detector.optimization import compress_fitted_rules
    rules = [rule("a", [atom("ML_EDGE_LOCAL_GE_70")]),
             rule("b", [atom("ML_EDGE_LOCAL_GE_30"), atom("ML_EDGE_LOCAL_GE_70")]),
             rule("c", [atom("SEND")])]
    selector = dict(coefficients=[1., -1., .5], intercept=-.8, threshold=.61,
                    C=.3, inner_oof={"f1": .8}, tuning=[{"C": .3}])
    before = copy.deepcopy((rules, selector))
    compact, selected, summary = compress_fitted_rules(rules, selector)
    assert [r["rule_key"] for r in compact] == ["c"]
    assert selected["coefficients"] == [.5]
    assert summary["representative_indices"] == [2]
    for key in ("C", "intercept", "threshold", "inner_oof", "tuning"):
        assert selected[key] == selector[key]
    assert (rules, selector) == before
    again, repeated, _ = compress_fitted_rules(compact, selected)
    assert again == compact and repeated == selected
    empty, constant, info = compress_fitted_rules(rules, {**selector, "coefficients": [0., 0., 0.]})
    assert empty == [] and constant["coefficients"] == [] and info["output_rules"] == 0
    assert constant["intercept"] == selector["intercept"]


def test_compressed_bundle_roundtrip_preserves_predictions(tmp_path, synthetic_model, synthetic_graph):
    import json
    from pathlib import Path
    from skill_rule_detector.model import Detector
    from skill_rule_detector.optimization import compress_model
    graphs = [synthetic_graph, {**synthetic_graph, "edges": []}]
    original = Detector(synthetic_model)
    summary = compress_model(tmp_path / "compiled", synthetic_model)
    compressed = Detector(tmp_path / "compiled")
    assert summary["output_rules"] <= summary["source_active_rules"]
    before, old_scores = original.predict_graphs(graphs)
    after, new_scores = compressed.predict_graphs(graphs)
    np.testing.assert_array_equal(before, after)
    np.testing.assert_allclose(old_scores, new_scores, atol=1e-12, rtol=0)
    assert compressed.selector["threshold"] == original.selector["threshold"]
    with pytest.raises(FileExistsError):
        compress_model(tmp_path / "compiled", synthetic_model)
