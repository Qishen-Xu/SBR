"""Local TF-IDF scores learned from skill labels with log-mean-exp pooling."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy.optimize import minimize
from scipy.special import expit
from sklearn.feature_extraction.text import TfidfVectorizer

URL_RE = re.compile(r"(?:https?|wss?|ftp|ssh)://\S+", re.I)
EMAIL_RE = re.compile(r"\b[^\s@]+@[^\s@]+\.[^\s@]+\b")
IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?\b")
PATH_RE = re.compile(r"(?<!\w)(?:\.?\.?/|~/|/)[\w.@{}+~/-]+")
LONG_ID_RE = re.compile(r"\b(?:[A-Fa-f0-9]{20,}|[A-Z][A-Z0-9_]{8,})\b")
SECURITY_CUE_RE = re.compile(
    r"\b(?:malware|malicious|attacker|attack|exfiltrat\w*|steal\w*|theft|"
    r"ransomware|backdoors?|phishing|cryptomin\w*|miners?|unauthori[sz]\w*|"
    r"suspicious|payloads?|command[ -]and[ -]control|c2|compromis\w*)\b",
    re.I,
)


def mask_text(value: str) -> str:
    """Preserve the frozen model's exact normalization and cue masking order."""
    for pattern, token in (
        (URL_RE, " URL "),
        (EMAIL_RE, " EMAIL "),
        (IP_RE, " IPADDR "),
        (PATH_RE, " PATH "),
        (LONG_ID_RE, " IDENTIFIER "),
    ):
        value = pattern.sub(token, value or "")
    value = re.sub(r"\s+", " ", value).strip().lower()
    return SECURITY_CUE_RE.sub(" SECURITY_TERM ", value)


def extract_records(graph: dict) -> list[dict]:
    records = []
    for node in graph.get("nodes") or []:
        for constraint in node.get("constraints") or []:
            text = mask_text(str(constraint.get("text", "")))
            if text:
                records.append(dict(kind="node", entity=node.get("name"), text=text))
    for index, edge in enumerate(graph.get("edges") or []):
        text = mask_text(str(edge.get("description", "")))
        if text:
            records.append(dict(kind="edge", record_index=index, text=text))
    return records


def flatten(records: list[list[dict]]) -> tuple[list[str], np.ndarray]:
    docs, owners = [], []
    for i, rows in enumerate(records):
        docs.extend([r["text"] for r in rows] if rows else [""])
        owners.extend([i] * max(1, len(rows)))
    return docs, np.asarray(owners, dtype=int)


def pool(logits: np.ndarray, owners: np.ndarray, n: int):
    maximum = np.full(n, -np.inf)
    np.maximum.at(maximum, owners, logits)
    exponents = np.exp(logits - maximum[owners])
    denominator = np.bincount(owners, weights=exponents, minlength=n)
    counts = np.bincount(owners, minlength=n)
    return maximum + np.log(denominator / counts), exponents / denominator[owners]


def new_vectorizer(**kwargs):
    return TfidfVectorizer(
        ngram_range=(1, 2),
        min_df=4,
        max_features=30000,
        sublinear_tf=True,
        strip_accents="unicode",
        **kwargs,
    )


@dataclass
class LocalTextModel:
    vectorizer: TfidfVectorizer
    weights: np.ndarray

    def predict(self, records: list[list[dict]]):
        if not records:
            return [], np.empty(0)
        docs, owners = flatten(records)
        logits = self.vectorizer.transform(docs) @ self.weights[:-1] + self.weights[-1]
        bags, _ = pool(logits, owners, len(records))
        probabilities = expit(logits)
        offsets = np.r_[0, np.cumsum([max(1, len(r)) for r in records])]
        local = [
            probabilities[offsets[i] : offsets[i] + len(r)]
            for i, r in enumerate(records)
        ]
        return local, expit(bags)

    def save(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "text_vocabulary.json").write_text(
            json.dumps(
                {k: int(v) for k, v in self.vectorizer.vocabulary_.items()},
                ensure_ascii=False,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        np.savez_compressed(
            directory / "text_parameters.npz",
            idf=self.vectorizer.idf_,
            weights=self.weights,
        )

    @classmethod
    def load(cls, directory: Path):
        vocabulary = json.loads(
            (directory / "text_vocabulary.json").read_text(encoding="utf-8")
        )
        arrays = np.load(directory / "text_parameters.npz", allow_pickle=False)
        vectorizer = new_vectorizer(vocabulary=vocabulary)
        vectorizer.idf_ = arrays["idf"]
        weights = arrays["weights"]
        if weights.shape != (len(vocabulary) + 1,) or not np.isfinite(weights).all():
            raise ValueError("Invalid local model dimensions or parameters")
        return cls(vectorizer, weights)


def fit_local(records: list[list[dict]], labels, regularization=1e-5, max_iter=500):
    labels = np.asarray(labels, dtype=int)
    if set(labels) != {0, 1}:
        raise ValueError("Local model training requires both classes")
    docs, owners = flatten(records)
    vectorizer = new_vectorizer()
    matrix = vectorizer.fit_transform(docs)
    n = len(labels)
    balance = np.where(
        labels == 1, n / (2 * labels.sum()), n / (2 * (n - labels.sum()))
    )

    def objective(weights):
        logits = matrix @ weights[:-1] + weights[-1]
        bags, attention = pool(logits, owners, n)
        loss = np.mean(balance * (np.logaddexp(0, bags) - labels * bags))
        loss += 0.5 * regularization * np.dot(weights[:-1], weights[:-1])
        derivative = balance * (expit(bags) - labels) / n
        local = derivative[owners] * attention
        gradient = np.r_[
            np.asarray(matrix.T @ local).ravel() + regularization * weights[:-1],
            local.sum(),
        ]
        return loss, gradient

    result = minimize(
        objective,
        np.zeros(matrix.shape[1] + 1),
        jac=True,
        method="L-BFGS-B",
        options={"maxiter": max_iter, "ftol": 1e-9},
    )
    info = dict(
        loss=float(result.fun),
        iterations=int(result.nit),
        success=bool(result.success),
        message=str(result.message),
        train_n=n,
        records=len(docs),
        features=matrix.shape[1],
    )
    if not result.success:
        raise RuntimeError(f"Local model did not converge: {info}")
    return LocalTextModel(vectorizer, result.x), info
