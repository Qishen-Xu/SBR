"""Boolean joint body-and-head matching, with shared typed variable bindings."""

from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from multiprocessing import get_context
import os

import numpy as np
from scipy.sparse import csr_matrix
from .mining import to_entity_facts, _ground_atom
from .arity import normalize_rule, arguments, is_node


def pattern(rule):
    rule = normalize_rule(rule)
    result = []
    for atom in rule["body"] + [rule["head"]]:
        left, left_type = atom["arg1"].split(":", 1)
        if is_node(atom['predicate']):
            result.append((atom['predicate'], left, left_type))
        else:
            right, right_type = atom["arg2"].split(":", 1)
            result.append((atom["predicate"], left, left_type, right, right_type))
    return result


def context(graph):
    facts, types = to_entity_facts(graph)
    index = defaultdict(list)
    for predicate, *values in sorted(facts):
        index[predicate].append(tuple(values))
    return index, types


def witness(atoms, index, types):
    """Return the first valid grounding; stop once existence is established."""
    if any(atom[0] not in index for atom in atoms):
        return None

    def visit(position, binding):
        if position == len(atoms):
            return binding
        for extension in _ground_atom(atoms[position], binding, index, types):
            answer = visit(position + 1, extension)
            if answer is not None:
                return answer
        return None

    return visit(0, {})


_PATTERNS = None


def _initialize(patterns):
    global _PATTERNS
    _PATTERNS = patterns


def _match_graph(graph):
    index, types = context(graph)
    return [
        i
        for i, atoms in enumerate(_PATTERNS)
        if witness(atoms, index, types) is not None
    ]


def rule_matrix(graphs, rules, workers=1):
    if workers < 1:
        raise ValueError("workers must be positive")
    patterns = [pattern(rule) for rule in rules]
    rows, columns = [], []

    def collect(matches):
        for i, hits in enumerate(matches):
            rows.extend([i] * len(hits))
            columns.extend(hits)

    if workers == 1:
        # Keep single-process matching independent of process-global state.
        def match(graph):
            index, types = context(graph)
            return [
                i
                for i, atoms in enumerate(patterns)
                if witness(atoms, index, types) is not None
            ]

        collect(map(match, graphs))
    else:
        method = "fork" if os.name == "posix" else "spawn"
        with ProcessPoolExecutor(
            workers,
            mp_context=get_context(method),
            initializer=_initialize,
            initargs=(patterns,),
        ) as executor:
            collect(executor.map(_match_graph, graphs, chunksize=8))
    return csr_matrix(
        (np.ones(len(rows), dtype=np.float32), (rows, columns)),
        shape=(len(graphs), len(rules)),
        dtype=np.float32,
    )
