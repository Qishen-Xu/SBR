"""Create tiny artificial test inputs at runtime; no learned artifacts are bundled."""
import json

import numpy as np
import pytest

from skill_rule_detector.model import seal_bundle
from skill_rule_detector.text_model import LocalTextModel, new_vectorizer


@pytest.fixture
def synthetic_graph():
    return {
        "skill_name": "synthetic-notes",
        "nodes": [{"name": "notes.txt", "type": "FILE", "constraints": [{"text": "read local notes"}]}],
        "edges": [{"source": "Read", "target": "notes.txt", "edge_type": "READ", "description": "read local notes"}],
    }


@pytest.fixture
def synthetic_model(tmp_path):
    directory = tmp_path / "synthetic-model"
    vectorizer = new_vectorizer()
    vectorizer.fit(["read local notes", "send a report"] * 4)
    weights = np.zeros(len(vectorizer.vocabulary_) + 1)
    weights[-1] = 2.0
    LocalTextModel(vectorizer, weights).save(directory)
    head = dict(predicate="READ", arg1="?a:AGENT", arg2="?f:FILE")
    high = dict(predicate="ML_NODE_LOCAL_GE_70", arg1="?f:FILE")
    low = dict(predicate="ML_NODE_LOCAL_GE_30", arg1="?f:FILE")
    rules = [
        dict(rule_key="synthetic-a", head=head, body=[high]),
        dict(rule_key="synthetic-b", head=head, body=[low, high]),
        dict(rule_key="synthetic-c", head=head, body=[dict(predicate="SEND", arg1="?a:AGENT", arg2="?f:FILE")]),
    ]
    selector = dict(coefficients=[1.0, -0.25, -0.5], intercept=-0.3, threshold=0.5,
                    C=0.1, nonzero_features=3, positive_features=1, negative_features=2)
    (directory / "rules.json").write_text(json.dumps(rules), encoding="utf-8")
    (directory / "selector.json").write_text(json.dumps(selector), encoding="utf-8")
    seal_bundle(directory, dict(model_id="synthetic-test-only", rules=3))
    return directory
