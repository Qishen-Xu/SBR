"""Train the current method on one fixed family-disjoint outer training split."""

from pathlib import Path
import hashlib
import json
import time

import numpy as np
from scipy.sparse import save_npz
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import StratifiedGroupKFold
from threadpoolctl import threadpool_limits

from .evaluation import load_dataset, choose_threshold, metrics
from .text_model import extract_records, fit_local
from .facts import local_facts, augment_graph
from .model import seal_bundle, validate_graph
from .optimization import compress_fitted_rules
from .rules.mining import mine_horn_rules_entity
from .rules.matching import rule_matrix
from .rules.vocabulary import BASE_PREDICATES
from .rules.arity import arguments

LOCAL_SEED = 20260907
SELECTOR_SEED = 20260906


def safe_rule(rule):
    body_variables = {
        arg.split(":")[0] for atom in rule["body"] for arg in arguments(atom)
    }
    return all(
        arg.split(":")[0] in body_variables for arg in arguments(rule['head'])
    )


def fit_selector(matrix, labels, groups):
    if matrix.shape[1] == 0:
        raise ValueError("No rules survived mining; cannot fit a rule detector")
    splits = list(
        StratifiedGroupKFold(3, shuffle=True, random_state=SELECTOR_SEED).split(
            np.zeros(len(labels)), labels, groups
        )
    )
    candidates = []
    for c in (0.01, 0.03, 0.1, 0.3, 1.0):
        oof = np.zeros(len(labels))
        for train, validation in splits:
            estimator = LogisticRegression(
                l1_ratio=1.0,
                solver="liblinear",
                C=c,
                class_weight="balanced",
                max_iter=3000,
                random_state=SELECTOR_SEED,
            )
            estimator.fit(matrix[train], labels[train])
            if estimator.n_iter_.max() >= 3000:
                raise RuntimeError("Rule selector did not converge")
            oof[validation] = estimator.predict_proba(matrix[validation])[:, 1]
        threshold, met = choose_threshold(labels, oof)
        candidates.append(dict(C=c, threshold=threshold, inner_oof=met))
    best = max(
        candidates,
        key=lambda row: (
            row["inner_oof"]["f1"],
            row["inner_oof"]["precision"],
            -row["inner_oof"]["fpr"],
        ),
    )
    estimator = LogisticRegression(
        l1_ratio=1.0,
        solver="liblinear",
        C=best["C"],
        class_weight="balanced",
        max_iter=3000,
        random_state=SELECTOR_SEED,
    )
    estimator.fit(matrix, labels)
    if estimator.n_iter_.max() >= 3000:
        raise RuntimeError("Final rule selector did not converge")
    coefficients = estimator.coef_[0]
    return dict(
        C=best["C"],
        threshold=round(best["threshold"], 8),
        inner_oof=best["inner_oof"],
        nonzero_features=int(np.count_nonzero(coefficients)),
        positive_features=int(np.sum(coefficients > 0)),
        negative_features=int(np.sum(coefficients < 0)),
        coefficients=coefficients.tolist(),
        intercept=float(estimator.intercept_[0]),
        tuning=candidates,
    )


def check_graph_availability(rows, graphs, missing_graphs):
    unavailable = []
    for row, graph in zip(rows, graphs):
        if row.get("graph_status") in ("missing_source_graph", "empty_source_graph"):
            if graph.get("nodes") or graph.get("edges"):
                raise ValueError(
                    "Unavailable graph marker conflicts with nonempty graph"
                )
            unavailable.append(row["key"])
        elif (
            row.get("graph_status") == "partial_source_graph"
            and missing_graphs == "historical-zero-hits"
        ):
            validate_graph(
                {**graph, "_meta": {**graph.get("_meta", {}), "llm_error": None}}
            )
        else:
            validate_graph(graph)
    if unavailable and missing_graphs != "historical-zero-hits":
        raise ValueError(
            f"{len(unavailable)} samples have no graph. To reproduce the historical protocol explicitly pass --missing-graphs historical-zero-hits"
        )
    return unavailable


def train(
    manifest_path, output_dir, workers=1, tune_local=False, missing_graphs="error"
):
    start = time.time()
    rows, graphs, dataset = load_dataset(manifest_path)
    unavailable = check_graph_availability(rows, graphs, missing_graphs)
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=False)
    train_indices = np.array(
        [i for i, row in enumerate(rows) if row["split"] == "train"]
    )
    records = [extract_records(graphs[i]) for i in train_indices]
    labels = np.array([rows[i]["label"] for i in train_indices])
    groups = np.array([rows[i]["family"] for i in train_indices])
    splits = list(
        StratifiedGroupKFold(3, shuffle=True, random_state=LOCAL_SEED).split(
            np.zeros(len(labels)), labels, groups
        )
    )
    tuning, candidates = [], {}
    with threadpool_limits(limits=1):
        for reg in ((0.001, 0.0001, 0.00001) if tune_local else (0.00001,)):
            local_scores = [None] * len(records)
            bag_scores = np.zeros(len(records))
            training_info = []
            for fold, (tr, va) in enumerate(splits):
                if set(groups[tr]) & set(groups[va]):
                    raise ValueError("Local crossfit family overlap")
                model, info = fit_local(
                    [records[i] for i in tr], labels[tr], regularization=reg
                )
                model.save(output / "training" / f"local_{reg}_crossfit_{fold}")
                probabilities, bag = model.predict([records[i] for i in va])
                for index, values in zip(va, probabilities):
                    local_scores[index] = values
                bag_scores[va] = bag
                training_info.append(info)
                print(
                    json.dumps(
                        dict(
                            stage="local_crossfit",
                            regularization=reg,
                            fold=fold,
                            **info,
                        )
                    ),
                    flush=True,
                )
            if any(value is None for value in local_scores):
                raise AssertionError("Incomplete crossfit coverage")
            threshold, met = choose_threshold(labels, bag_scores)
            tuning.append(dict(regularization=reg, threshold=threshold, train_oof=met))
            candidates[reg] = (local_scores, training_info)
        best = max(
            tuning,
            key=lambda row: (
                row["train_oof"]["f1"],
                row["train_oof"]["precision"],
                -row["train_oof"]["fpr"],
            ),
        )
        reg = best["regularization"]
        local_scores, training_info = candidates[reg]
        final_model, info = fit_local(records, labels, regularization=reg)
        training_info.append(info)
        bundle = output / "model"
        final_model.save(bundle)
        print(json.dumps(dict(stage="local_final", **info)), flush=True)

        augmented = [
            augment_graph(graphs[index], local_facts(record, values))
            for index, record, values in zip(train_indices, records, local_scores)
        ]
        # Persist training facts for independently auditing crossfit/mining alignment.
        import gzip

        with gzip.open(
            output / "training" / "local_facts.jsonl.gz", "wt", encoding="utf-8"
        ) as stream:
            for index, record, values in zip(train_indices, records, local_scores):
                stream.write(
                    json.dumps(
                        dict(key=rows[index]["key"], facts=local_facts(record, values)),
                        ensure_ascii=False,
                    )
                    + "\n"
                )
        print(
            json.dumps(dict(stage="mining", train_n=len(augmented), workers=workers)),
            flush=True,
        )
        mined = mine_horn_rules_entity(
            augmented,
            min_support=2,
            min_confidence=0.3,
            min_head_coverage=0.01,
            max_body_len=2,
            connected_only=True,
            workers=workers,
            head_predicates=BASE_PREDICATES,
        )
        rules = [
            rule
            for rule in mined["rules"]
            if rule["head"]["predicate"] in BASE_PREDICATES
            and safe_rule(rule)
            and rule["support"] >= 2
            and rule["head_coverage"] >= 0.01
            and rule["standard_confidence"] >= 0.3
        ]
        (output / "training" / "rules.json").write_text(
            json.dumps(rules, ensure_ascii=False), encoding="utf-8"
        )
        print(json.dumps(dict(stage="rule_matching", rules=len(rules))), flush=True)
        matrix = rule_matrix(augmented, rules, workers)
        save_npz(output / "training" / "rule_matrix.npz", matrix)
        selector = fit_selector(matrix, labels, groups)
        (output / "training" / "selector.json").write_text(
            json.dumps(selector, indent=2), encoding="utf-8"
        )
        # Freeze the fitted decision function, then compile for deployment.
        # No training labels or threshold selection enter this compression.
        source_logits = np.asarray(matrix @ np.asarray(selector["coefficients"])).ravel()
        rules, selector, compression = compress_fitted_rules(rules, selector)
        compact_logits = np.asarray(
            matrix[:, compression["representative_indices"]]
            @ np.asarray(selector["coefficients"])
        ).ravel()
        if not np.allclose(source_logits, compact_logits, atol=1e-11, rtol=0):
            raise RuntimeError("Compression changed fitted training scores")
        compression["max_training_logit_difference"] = float(np.max(np.abs(source_logits - compact_logits)))
        (bundle / "rules.json").write_text(json.dumps(rules, ensure_ascii=False), encoding="utf-8")
        (bundle / "selector.json").write_text(json.dumps(selector, indent=2), encoding="utf-8")
        (output / "training" / "compression.json").write_text(json.dumps(compression, indent=2), encoding="utf-8")
        print(json.dumps(dict(stage="compression", source_rules=compression["source_rules"],
                              source_active_rules=compression["source_active_rules"],
                              rules=len(rules))), flush=True)
        metadata = dict(
            model_id="sbr-canonical-unary",
            release_version="1.3.0",
            candidate_order="canonical_rule_signature",
            pca_used=False,
            node_predicate_arity=1,
            train_n=len(labels),
            dataset_sha256=hashlib.sha256(Path(manifest_path).read_bytes()).hexdigest(),
            local_regularization=reg,
            local_max_iter=500,
            local_crossfit_seed=LOCAL_SEED,
            selector_seed=SELECTOR_SEED,
            missing_graph_policy=missing_graphs,
            local_training=training_info,
            local_tuning=tuning,
            local_selection=(
                "training OOF search" if tune_local else "frozen current regularization"
            ),
            mining=mined["mining"],
            candidate_rules=compression["source_rules"],
            compression=compression,
            rules=len(rules),
            inner_cv_scope="selector only; local model and miner are not refitted inside selector CV",
        )
        seal_bundle(bundle, metadata)
    report = dict(
        **metadata,
        elapsed_s=round(time.time() - start, 3),
        unavailable_training_graphs=[
            rows[i]["key"] for i in train_indices if rows[i]["key"] in unavailable
        ],
        partial_training_graphs=[
            rows[i]["key"]
            for i in train_indices
            if rows[i].get("graph_status") == "partial_source_graph"
        ],
        nonzero_rules=selector["nonzero_features"],
        C=selector["C"],
        threshold=selector["threshold"],
    )
    (output / "training_report.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            dict(
                stage="complete",
                rules=len(rules),
                nonzero_rules=selector["nonzero_features"],
                elapsed_s=report["elapsed_s"],
            )
        ),
        flush=True,
    )
    return report
