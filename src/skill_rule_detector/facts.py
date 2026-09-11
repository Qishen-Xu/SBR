"""Bind local threshold predicates to the entities described by their text."""

import copy
from .rules.vocabulary import LOCAL_THRESHOLDS


def local_facts(records, probabilities):
    if len(records) != len(probabilities):
        raise ValueError("Local records and probabilities must be aligned")
    facts = []
    for record, probability in zip(records, probabilities):
        for threshold in LOCAL_THRESHOLDS:
            if probability >= threshold:
                facts.append(
                    {
                        **record,
                        "predicate": f'ML_{record["kind"].upper()}_LOCAL_GE_{int(threshold * 100)}',
                        "score": float(probability),
                    }
                )
    return facts


def augment_graph(graph, facts):
    result = copy.deepcopy(graph)
    result.setdefault("nodes", [])
    result.setdefault("edges", [])
    result.setdefault("unary_facts", [])
    nodes = {node.get("name") for node in result["nodes"] if node.get("name")}
    pairs = [(edge.get("source"), edge.get("target")) for edge in result["edges"]]
    for fact in facts:
        if fact["kind"] == "node":
            source = target = fact.get("entity")
            if not source:
                continue
            if source not in nodes:
                result["nodes"].append(
                    dict(name=source, type="SEMANTIC", properties={}, constraints=[])
                )
                nodes.add(source)
            result['unary_facts'].append(dict(predicate=fact['predicate'], entity=source))
            continue
        else:
            index = int(fact.get("record_index", -1))
            if not 0 <= index < len(pairs):
                continue
            source, target = pairs[index]
            if source is None or target is None:
                continue
        result["edges"].append(
            dict(
                source=source,
                target=target,
                edge_type=fact["predicate"],
                description="",
                _ml_local_predicate=fact["predicate"],
            )
        )
    return result
