"""Fixed-split dataset validation, training-only threshold selection, and metrics."""

from pathlib import Path
import gzip
import hashlib
import json
import numpy as np


def metrics(y, prediction):
    y, prediction = np.asarray(y, dtype=int), np.asarray(prediction, dtype=int)
    if y.shape != prediction.shape or not len(y):
        raise ValueError(
            "Evaluation requires equally sized nonempty labels and predictions"
        )
    tp = int(np.sum((y == 1) & (prediction == 1)))
    fp = int(np.sum((y == 0) & (prediction == 1)))
    fn = int(np.sum((y == 1) & (prediction == 0)))
    tn = int(np.sum((y == 0) & (prediction == 0)))
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return dict(
        tp=tp,
        fp=fp,
        fn=fn,
        tn=tn,
        n=len(y),
        accuracy=round((tp + tn) / len(y), 6),
        precision=round(precision, 6),
        recall=round(recall, 6),
        f1=round(f1, 6),
        fpr=round(fp / (fp + tn), 6) if fp + tn else 0.0,
    )


def choose_threshold(labels, probabilities, fpr_limit=0.37):
    probabilities = np.asarray(probabilities, dtype=float)
    candidates = sorted(set(float(x) for x in probabilities))
    if len(candidates) > 800:
        candidates = [
            float(np.quantile(probabilities, q)) for q in np.linspace(0, 1, 801)
        ]
    candidates.extend((0.0, 0.5, 1.0, 1.000001))
    best = None
    for threshold in sorted(set(candidates)):
        met = metrics(labels, probabilities >= threshold)
        if met["fpr"] > fpr_limit + 1e-12:
            continue
        key = (met["f1"], met["precision"], -met["fpr"], threshold)
        if best is None or key > best[0]:
            best = (key, threshold, met)
    return float(best[1]), best[2]


def load_dataset(manifest_path):
    path = Path(manifest_path)
    manifest = json.loads(path.read_text(encoding="utf-8"))
    rows = manifest["samples"]
    if not rows or len({row["key"] for row in rows}) != len(rows):
        raise ValueError("Dataset sample keys must be nonempty and unique")
    for row in rows:
        if (
            row["label"] not in (0, 1)
            or row["split"] not in ("train", "test")
            or not row.get("family")
        ):
            raise ValueError(
                "Each sample needs a binary label, family, and train/test split"
            )
    families = {
        split: {r["family"] for r in rows if r["split"] == split}
        for split in ("train", "test")
    }
    if not all(families.values()) or families["train"] & families["test"]:
        raise ValueError("Train/test families must be nonempty and disjoint")
    archive = path.parent / manifest["graph_archive"]
    if (
        manifest.get("graph_archive_sha256")
        and hashlib.sha256(archive.read_bytes()).hexdigest()
        != manifest["graph_archive_sha256"]
    ):
        raise ValueError("Graph archive checksum mismatch")
    graphs = {}
    with gzip.open(archive, "rt", encoding="utf-8") as stream:
        for line in stream:
            graph = json.loads(line)
            if graph["skill_name"] in graphs:
                raise ValueError("Duplicate graph key")
            graphs[graph["skill_name"]] = graph
    if set(graphs) != {r["key"] for r in rows}:
        raise ValueError("Graph archive and sample roster must contain identical keys")
    return rows, [graphs[row["key"]] for row in rows], manifest
