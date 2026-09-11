import asyncio
import json
from pathlib import Path
import shutil

import numpy as np
import pytest

from skill_rule_detector.model import Detector
from skill_rule_detector.text_model import LocalTextModel, extract_records, mask_text
from skill_rule_detector.facts import augment_graph, local_facts
from skill_rule_detector.rules.matching import context, pattern, witness, rule_matrix
from skill_rule_detector.rules.mining import _ground
from skill_rule_detector.extraction.pipeline import extract_skill
from skill_rule_detector.extraction.client import LLMConfig
from skill_rule_detector.evaluation import load_dataset
from skill_rule_detector.training import check_graph_availability

def test_scope_and_nested_thresholds():
    records = [
        dict(kind="node", entity="config", text="send credentials"),
        dict(kind="edge", record_index=0, text="send a report"),
    ]
    facts = local_facts(records, [0.71, 0.51])
    assert [f["predicate"] for f in facts] == [
        "ML_NODE_LOCAL_GE_30",
        "ML_NODE_LOCAL_GE_50",
        "ML_NODE_LOCAL_GE_70",
        "ML_EDGE_LOCAL_GE_30",
        "ML_EDGE_LOCAL_GE_50",
    ]
    graph = dict(
        nodes=[dict(name="config", type="FILE")],
        edges=[dict(source="Bash", target="config", edge_type="READ")],
    )
    augmented = augment_graph(graph, facts)
    assert len(augmented['unary_facts']) == 3
    assert all(f['entity'] == 'config' for f in augmented['unary_facts'])
    assert all(
        e["source"] == "Bash" and e["target"] == "config"
        for e in augmented["edges"][1:]
    )
    assert len(graph["edges"]) == 1  # Input ownership is preserved.


def test_shared_binding_prevents_type_only_false_hit():
    rule = dict(
        head=dict(predicate="READ", arg1="?x:FILE", arg2="?y:CREDENTIAL"),
        body=[dict(predicate="EXEC", arg1="?a:AGENT", arg2="?x:FILE")],
    )
    graph = dict(
        nodes=[
            dict(name="a.py", type="FILE"),
            dict(name="b.py", type="FILE"),
            dict(name="key", type="CREDENTIAL"),
        ],
        edges=[
            dict(source="Bash", target="a.py", edge_type="EXEC"),
            dict(source="b.py", target="key", edge_type="READ"),
        ],
    )
    index, types = context(graph)
    assert witness(pattern(rule), index, types) is None
    graph["edges"][1]["source"] = "a.py"
    index, types = context(graph)
    assert witness(pattern(rule), index, types)["?x"] == "a.py"
    # Observing the body alone must not trigger the feature.
    graph["edges"].pop()
    assert rule_matrix([graph], [rule]).nnz == 0


def test_same_variable_is_a_self_loop():
    atom = [("READ", "?x", "FILE", "?x", "FILE")]
    assert witness(atom, {"READ": [("a", "b")]}, {"a": "FILE", "b": "FILE"}) is None
    assert witness(atom, {"READ": [("a", "a")]}, {"a": "FILE"}) == {"?x": "a"}


def test_masking_preserves_frozen_order():
    # All-caps identifiers are replaced before security words are masked.
    assert (
        mask_text(
            "MALICIOUS https://example.com/x /etc/secrets 1.2.3.4 test@example.com"
        )
        == "identifier url path ipaddr email"
    )
    assert mask_text("malicious text") == " SECURITY_TERM  text"
    assert "SECURITY_TERM" in mask_text("exfiltrates confidential data")


def test_portable_model_roundtrip(tmp_path, synthetic_model):
    model = LocalTextModel.load(synthetic_model)
    records = [
        [dict(text="read a report and upload the result")],
        [],
        [dict(text="credential file")],
    ]
    before, bag_before = model.predict(records)
    model.save(tmp_path)
    after, bag_after = LocalTextModel.load(tmp_path).predict(records)
    np.testing.assert_array_equal(bag_before, bag_after)
    for a, b in zip(before, after):
        np.testing.assert_array_equal(a, b)


def test_bundle_integrity_check(tmp_path, synthetic_model):
    shutil.copytree(synthetic_model, tmp_path / "model")
    (tmp_path / "model/selector.json").write_text("{}")
    with pytest.raises(ValueError, match="checksum"):
        Detector(tmp_path / "model")


def test_graph_score_equals_rule_contributions(synthetic_model, synthetic_graph):
    graph = synthetic_graph
    detector = Detector(synthetic_model)
    report = detector.detect(graph, top_rules=-1)
    score = detector.predict_graphs([graph])[1][0]
    assert abs(score - report["score"]) < 1e-12
    assert (
        abs(
            report["logit"]
            - report["intercept"]
            - sum(h["weight"] for h in report["matched_rules"])
        )
        < 1e-12
    )
    records = extract_records(graph)
    probabilities, _ = detector.local_model.predict([records])
    index, types = context(augment_graph(graph, local_facts(records, probabilities[0])))
    # Independently compare early-exit matching with complete natural joins.
    for rule in detector.rules:
        atoms = pattern(rule)
        assert (witness(atoms, index, types) is not None) == bool(
            _ground(atoms, index, types)
        )


def test_reject_empty_and_failed_graphs(synthetic_model):
    detector = Detector(synthetic_model)
    with pytest.raises(ValueError, match="Empty graph"):
        detector.detect(dict(nodes=[], edges=[]))
    with pytest.raises(ValueError, match="failed"):
        detector.detect(dict(nodes=[], edges=[], _meta={"llm_error": "timeout"}))


def test_extraction_then_detection_contract(tmp_path, synthetic_model):
    """Offline transport fixture with a runtime-generated artificial model."""
    skill = tmp_path / "skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text("# Read a file\nRead notes.txt with Read.\n")
    raw = dict(
        entities=[
            dict(
                name="notes.txt",
                type="FILE",
                properties={"path": "notes.txt"},
                constraints=[{"text": "Read the local notes file"}],
            )
        ],
        relationships=[
            dict(
                source="Read",
                target="notes.txt",
                edge_type="READ",
                tool_used="Read",
                description="Read the local notes file",
            )
        ],
    )

    async def call(system, user):
        assert "SKILL.md" in user
        assert "notes.txt" in user
        return json.dumps(raw)

    graph = asyncio.run(
        extract_skill(
            skill, call, tmp_path / "graph.json", model_name="offline-fixture"
        )
    )
    assert graph["nodes"] and graph["edges"]
    assert Detector(synthetic_model).detect(graph)["verdict"] in ("benign", "malicious")
    assert graph["_meta"]["extraction"]["model"] == "offline-fixture"


@pytest.mark.parametrize(
    "response", ["not JSON", "{}", '{"entities":[],"relationships":[]}']
)
def test_extraction_failure_never_becomes_benign(tmp_path, response):
    (tmp_path / "SKILL.md").write_text("Read a text file.")

    async def call(system, user):
        return response

    with pytest.raises((ValueError, RuntimeError)):
        asyncio.run(extract_skill(tmp_path, call, tmp_path / "graph.json"))


def test_configuration_has_no_implicit_remote_backend(monkeypatch):
    for key in ("SKILL_RULE_LLM_BASE_URL", "SKILL_RULE_LLM_MODEL"):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(ValueError, match="Configure"):
        LLMConfig.from_env()
    assert "hidden-test-key" not in repr(
        LLMConfig("http://localhost/v1", "model", "hidden-test-key")
    )


def test_script_hints_do_not_mask_invalid_llm_envelope(tmp_path):
    (tmp_path / "scripts").mkdir()
    marker = tmp_path / "must-not-exist"
    (tmp_path / "scripts/run.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).touch()"
    )
    (tmp_path / "SKILL.md").write_text("Run scripts/run.py to read the local report.")

    async def invalid(system, user):
        assert "scripts/run.py" in user
        assert "scripts/scripts/run.py" not in user
        return "{}"

    with pytest.raises(RuntimeError, match="entities and relationships"):
        asyncio.run(extract_skill(tmp_path, invalid, tmp_path / "graph.json"))
    assert not marker.exists()

    async def valid(system, user):
        return '{"entities":[],"relationships":[]}'

    graph = asyncio.run(extract_skill(tmp_path, valid, tmp_path / "graph.json"))
    assert any(node["name"] == "scripts/run.py" for node in graph["nodes"])
    assert graph["_meta"]["extraction"]["script_hints"] == 1
    assert not marker.exists()


def test_dataset_validation_and_missing_graph_policy(tmp_path, synthetic_graph):
    import gzip
    rows = [
        dict(key="train-a", label=0, split="train", family="a", graph_status="available"),
        dict(key="train-b", label=1, split="train", family="b", graph_status="available"),
        dict(key="test-a", label=0, split="test", family="c", graph_status="available"),
        dict(key="test-b", label=1, split="test", family="d", graph_status="missing_source_graph"),
    ]
    graphs = [{**synthetic_graph, "skill_name": row["key"]} for row in rows]
    graphs[-1] = dict(skill_name="test-b", nodes=[], edges=[])
    with gzip.open(tmp_path / "graphs.jsonl.gz", "wt", encoding="utf-8") as stream:
        for graph in graphs:
            stream.write(json.dumps(graph) + "\n")
    manifest = dict(graph_archive="graphs.jsonl.gz", samples=rows)
    path = tmp_path / "dataset.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    loaded_rows, loaded_graphs, _ = load_dataset(path)
    assert len(loaded_rows) == 4
    assert check_graph_availability(loaded_rows, loaded_graphs, "historical-zero-hits") == ["test-b"]
    with pytest.raises(ValueError, match="no graph"):
        check_graph_availability(loaded_rows, loaded_graphs, "error")
    rows[-1]["family"] = "a"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="disjoint"):
        load_dataset(path)


@pytest.mark.parametrize("argv", [
    ["detect", "skill", "--output", "result.json"],
    ["detect-graph", "graph.json", "--output", "result.json"],
    ["evaluate", "--manifest", "dataset.json", "--output", "result.json"],
    ["compress-model", "--output", "compiled"],
])
def test_cli_requires_explicit_model(argv, capsys):
    from skill_rule_detector.cli import main
    with pytest.raises(SystemExit) as error:
        main(argv)
    assert error.value.code == 2
    assert "--model" in capsys.readouterr().err
