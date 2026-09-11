"""Portable inference bundle: local model, mined rules, and sparse rule weights."""

from pathlib import Path
import hashlib
import json

import numpy as np
from scipy.special import expit

from .text_model import LocalTextModel, extract_records
from .facts import local_facts, augment_graph
from .rules.matching import rule_matrix, context, pattern, witness
from .rules.vocabulary import BASE_PREDICATES
from .rules.arity import migrate_identity

def validate_graph(graph):
    if (
        not isinstance(graph, dict)
        or not isinstance(graph.get("nodes"), list)
        or not isinstance(graph.get("edges"), list)
    ):
        raise ValueError("Graph requires nodes and edges lists")
    if (graph.get("_meta") or {}).get("llm_error"):
        raise ValueError("Graph extraction failed; a failed graph cannot be classified")
    if not graph["nodes"] and not graph["edges"]:
        raise ValueError("Empty graph: insufficient extracted evidence for detection")
    for node in graph["nodes"]:
        if not isinstance(node, dict) or not node.get("name") or not node.get("type"):
            raise ValueError("Every graph node needs a name and type")
    for edge in graph["edges"]:
        if not isinstance(edge, dict) or not all(
            edge.get(k) for k in ("source", "target", "edge_type")
        ):
            raise ValueError("Every edge needs source, target, and edge_type")
        if edge["edge_type"] not in BASE_PREDICATES:
            raise ValueError(
                "Input must be a base behavior graph, before ML augmentation"
            )


class Detector:
    def __init__(self, model_dir):
        self.directory = Path(model_dir)
        manifest = json.loads(
            (self.directory / "bundle.json").read_text(encoding="utf-8")
        )
        for filename, digest in manifest["sha256"].items():
            if Path(filename).name != filename:
                raise ValueError("Invalid bundle filename")
            if (
                hashlib.sha256((self.directory / filename).read_bytes()).hexdigest()
                != digest
            ):
                raise ValueError(f"Model bundle checksum mismatch: {filename}")
        self.metadata = manifest
        self.local_model = LocalTextModel.load(self.directory)
        self.rules = json.loads(
            (self.directory / "rules.json").read_text(encoding="utf-8")
        )
        self.rules = [migrate_identity(rule) for rule in self.rules]
        self.selector = json.loads(
            (self.directory / "selector.json").read_text(encoding="utf-8")
        )
        self.weights = np.asarray(self.selector["coefficients"], dtype=float)
        if len(self.rules) != len(self.weights) or not np.isfinite(self.weights).all():
            raise ValueError("Rule and weight alignment is invalid")
        self.active = np.flatnonzero(self.weights)

    def predict_graphs(self, graphs, workers=1):
        for graph in graphs:
            validate_graph(graph)
        records = [extract_records(graph) for graph in graphs]
        probabilities, _ = self.local_model.predict(records)
        augmented = [
            augment_graph(graph, local_facts(rows, values))
            for graph, rows, values in zip(graphs, records, probabilities)
        ]
        matrix = rule_matrix(augmented, [self.rules[i] for i in self.active], workers)
        logits = (
            np.asarray(matrix @ self.weights[self.active]).ravel()
            + self.selector["intercept"]
        )
        scores = expit(logits)
        return (scores >= self.selector["threshold"]).astype(int), scores

    def detect(self, graph, top_rules=10):
        validate_graph(graph)
        records = extract_records(graph)
        probabilities, _ = self.local_model.predict([records])
        facts = local_facts(records, probabilities[0])
        augmented = augment_graph(graph, facts)
        index, types = context(augmented)
        hits = []
        for i in self.active:
            binding = witness(pattern(self.rules[i]), index, types)
            if binding is not None:
                hits.append(
                    dict(
                        rule_index=int(i),
                        rule_key=self.rules[i]["rule_key"],
                        weight=float(self.weights[i]),
                        binding=binding,
                        head=self.rules[i]["head"],
                        body=self.rules[i]["body"],
                    )
                )
        logit = float(self.selector["intercept"] + sum(hit["weight"] for hit in hits))
        score = float(expit(logit))
        prediction = int(score >= self.selector["threshold"])
        hits.sort(key=lambda hit: (-abs(hit["weight"]), hit["rule_index"]))
        return dict(
            skill=graph.get("skill_name", "input"),
            prediction=prediction,
            verdict="malicious" if prediction else "benign",
            score=score,
            threshold=self.selector["threshold"],
            logit=logit,
            intercept=self.selector["intercept"],
            nodes=len(graph["nodes"]),
            edges=len(graph["edges"]),
            local_records=len(records),
            local_facts=facts,
            active_rule_hits=len(hits),
            matched_rules=hits[:top_rules] if top_rules >= 0 else hits,
            model_id=self.metadata["model_id"],
        )


def seal_bundle(directory, metadata):
    names = (
        "text_vocabulary.json",
        "text_parameters.npz",
        "rules.json",
        "selector.json",
    )
    metadata = {
        **metadata,
        "format_version": 1,
        "sha256": {
            name: hashlib.sha256((directory / name).read_bytes()).hexdigest()
            for name in names
        },
    }
    (directory / "bundle.json").write_text(
        json.dumps(metadata, indent=2), encoding="utf-8"
    )
