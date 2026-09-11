"""Compare two explicitly supplied model bundles."""

import argparse
import json
from pathlib import Path

import numpy as np
from skill_rule_detector.model import Detector


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    reference, candidate = Detector(args.reference), Detector(args.candidate)
    checks = dict(
        vocabulary=reference.local_model.vectorizer.vocabulary_
        == candidate.local_model.vectorizer.vocabulary_,
        idf=bool(
            np.array_equal(
                reference.local_model.vectorizer.idf_,
                candidate.local_model.vectorizer.idf_,
            )
        ),
        local_weights=bool(
            np.array_equal(reference.local_model.weights, candidate.local_model.weights)
        ),
        rules=reference.rules == candidate.rules,
        rule_weights=bool(np.array_equal(reference.weights, candidate.weights)),
        intercept=reference.selector["intercept"] == candidate.selector["intercept"],
        threshold=reference.selector["threshold"] == candidate.selector["threshold"],
        C=reference.selector["C"] == candidate.selector["C"],
    )
    result = dict(exact_match=all(checks.values()), checks=checks)
    text = json.dumps(result, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text)
    return 0 if result["exact_match"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
