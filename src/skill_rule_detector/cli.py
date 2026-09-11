"""Command-line entry points for extraction, detection, training, and evaluation."""

import argparse
import asyncio
from collections import Counter
import json
from pathlib import Path
import sys
import time

import numpy as np
from scipy.special import expit
from .model import Detector
from .evaluation import load_dataset, metrics
from .training import check_graph_availability, train
from .extraction.client import LLMConfig, LLMClient
from .extraction.pipeline import extract_skill


def write_json(path, result):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


def evaluate(args):
    start = time.time()
    rows, graphs, _ = load_dataset(args.manifest)
    selected = [i for i, row in enumerate(rows) if row["split"] == args.split]
    rows, graphs = [rows[i] for i in selected], [graphs[i] for i in selected]
    unavailable = check_graph_availability(rows, graphs, args.missing_graphs)
    detector = Detector(args.model)
    available = [i for i, row in enumerate(rows) if row["key"] not in unavailable]
    # This explicit legacy convention is exclusive to dataset replay.
    scores = np.full(len(rows), expit(detector.selector["intercept"]))
    replay_graphs = [
        (
            {**graphs[i], "_meta": {**graphs[i].get("_meta", {}), "llm_error": None}}
            if rows[i].get("graph_status") == "partial_source_graph"
            else graphs[i]
        )
        for i in available
    ]
    _, values = detector.predict_graphs(replay_graphs, args.workers)
    scores[available] = values
    predictions = (scores >= detector.selector["threshold"]).astype(int)
    labels = np.array([row["label"] for row in rows])
    result = dict(
        model_id=detector.metadata["model_id"],
        split=args.split,
        missing_graph_policy=args.missing_graphs,
        metrics=metrics(labels, predictions),
        available_graph_metrics=metrics(labels[available], predictions[available]),
        graph_status_counts=dict(
            Counter(row.get("graph_status", "available") for row in rows)
        ),
        unavailable_graph_keys=unavailable,
        predictions=[
            dict(
                key=row["key"],
                label=row["label"],
                prediction=int(prediction),
                score=float(score),
                graph_status=row.get("graph_status", "available"),
            )
            for row, prediction, score in zip(rows, predictions, scores)
        ],
    )
    if args.reference:
        reference = json.loads(Path(args.reference).read_text(encoding="utf-8"))
        by_key = {row["key"]: row for row in reference}
        if len(by_key) != len(reference) or set(by_key) != {row["key"] for row in rows}:
            raise ValueError("Reference and evaluated sample keys must match exactly")
        if any(by_key[row["key"]]["label"] != row["label"] for row in rows):
            raise ValueError("Reference label mismatch")
        mismatches = [
            row["key"]
            for row, prediction in zip(rows, predictions)
            if by_key[row["key"]]["prediction"] != int(prediction)
        ]
        delta = max(
            abs(by_key[row["key"]]["score"] - score) for row, score in zip(rows, scores)
        )
        result["reference_comparison"] = dict(
            prediction_mismatches=mismatches, max_score_absolute_difference=float(delta)
        )
        result["reference_match"] = bool(not mismatches and delta < 1e-7)
    result["elapsed_s"] = round(time.time() - start, 3)
    write_json(args.output, result)
    print(
        json.dumps(
            {
                key: value
                for key, value in result.items()
                if key not in ("predictions", "unavailable_graph_keys")
            },
            indent=2,
        )
    )
    return 0 if result.get("reference_match", True) else 1


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Skill detection with local ML predicates and mined Horn rules"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("extract", "detect"):
        command = commands.add_parser(name)
        command.add_argument("skill_dir", type=Path)
        command.add_argument("--output", type=Path, required=True)
        command.add_argument("--base-url")
        command.add_argument("--llm-model")
        command.add_argument(
            "--no-think",
            action="store_true",
            help="Append /no_think to the user prompt for compatible local models",
        )
        command.add_argument(
            "--ollama-cpu",
            action="store_true",
            help="Use native Ollama /api/chat with num_gpu=0",
        )
        if name == "detect":
            command.add_argument("--model", type=Path, required=True)
            command.add_argument("--top-rules", type=int, default=10)
    command = commands.add_parser("detect-graph")
    command.add_argument("graph", type=Path)
    command.add_argument("--model", type=Path, required=True)
    command.add_argument("--output", type=Path, required=True)
    command.add_argument("--top-rules", type=int, default=10)
    command = commands.add_parser("compress-model", help="Merge equivalent fitted rule matches without refitting")
    command.add_argument("--model", type=Path, required=True)
    command.add_argument("--output", type=Path, required=True)
    for name in ("train", "evaluate"):
        command = commands.add_parser(name)
        command.add_argument("--manifest", type=Path, required=True)
        command.add_argument("--output", type=Path, required=True)
        command.add_argument("--workers", type=int, default=1)
        command.add_argument(
            "--missing-graphs",
            choices=("error", "historical-zero-hits"),
            default="error",
        )
        if name == "train":
            command.add_argument(
                "--tune-local",
                action="store_true",
                help="Search local L2 over 1e-3, 1e-4, 1e-5 using training OOF scores",
            )
        else:
            command.add_argument("--model", type=Path, required=True)
            command.add_argument("--split", choices=("train", "test"), default="test")
            command.add_argument("--reference", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "compress-model":
            from .optimization import compress_model
            summary = compress_model(args.output, args.model)
            summary.pop("representative_indices", None)
            print(json.dumps(summary, indent=2))
            return 0
        if args.command == "train":
            train(
                args.manifest,
                args.output,
                args.workers,
                args.tune_local,
                args.missing_graphs,
            )
            return 0
        if args.command == "evaluate":
            return evaluate(args)
        if args.command in ("detect", "extract"):
            config = LLMConfig.from_env(
                args.base_url, args.llm_model, args.no_think, args.ollama_cpu
            )
            client = LLMClient(config)
            graph_path = (
                args.output
                if args.command == "extract"
                else args.output.with_suffix(".graph.json")
            )
            graph = asyncio.run(
                extract_skill(
                    args.skill_dir, client, graph_path, model_name=config.model
                )
            )
            if args.command == "extract":
                print(
                    json.dumps(
                        dict(
                            graph=str(graph_path),
                            nodes=len(graph["nodes"]),
                            edges=len(graph["edges"]),
                            llm_calls=client.calls,
                        )
                    )
                )
                return 0
        else:
            graph = json.loads(args.graph.read_text(encoding="utf-8"))
        result = Detector(args.model).detect(graph, args.top_rules)
        if args.command == "detect":
            result["extraction"] = graph["_meta"]["extraction"]
            result["llm_calls"] = client.calls
            result["graph_path"] = str(graph_path)
        write_json(args.output, result)
        print(
            json.dumps(
                {
                    key: result[key]
                    for key in (
                        "skill",
                        "verdict",
                        "score",
                        "threshold",
                        "active_rule_hits",
                    )
                },
                indent=2,
            )
        )
        return 0
    except (ValueError, RuntimeError, OSError, KeyError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
