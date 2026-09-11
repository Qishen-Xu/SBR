"""Equivalent existential match queries with native unary/binary atoms."""
from collections import defaultdict
from functools import lru_cache
from itertools import combinations, permutations
from .matching import pattern
from .vocabulary import LOCAL_PREDICATES
from .arity import pairs

def _predicate(value):
    if value in LOCAL_PREDICATES:
        family, threshold = value.rsplit('_', 1)
        return family, int(threshold)
    return value, None

def _predicate_implies(stronger, weaker):
    left, a = _predicate(stronger)
    right, b = _predicate(weaker)
    return left == right and (a is None and b is None or a is not None and b is not None and a >= b)

@lru_cache(maxsize=131072)
def implies(stronger, weaker):
    candidates = []
    for atom in weaker:
        args = pairs(atom)
        matches = [pairs(s) for s in stronger
                   if len(s) == len(atom) and _predicate_implies(s[0], atom[0])
                   and s[2::2] == atom[2::2]]
        if not matches:
            return False
        candidates.append((args, matches))
    candidates.sort(key=lambda row: len(row[1]))
    def visit(k, mapping):
        if k == len(candidates):
            return True
        args, matches = candidates[k]
        for match in matches:
            extension = dict(mapping)
            for (source, _), (target, _) in zip(args, match):
                if source in extension and extension[source] != target:
                    break
                extension[source] = target
            else:
                if visit(k + 1, extension):
                    return True
        return False
    return visit(0, {})

def _canonical(atoms):
    variables = sorted({v for atom in atoms for v, _ in pairs(atom)})
    signatures = []
    for order in permutations([f'?v{i}' for i in range(len(variables))]):
        mapping = dict(zip(variables, order))
        signatures.append(tuple(sorted((a[0], *(item for v,t in pairs(a) for item in (mapping[v],t))) for a in atoms)))
    return min(signatures)

def semantic_core(rule):
    atoms = tuple(sorted(set(pattern(rule))))
    if not atoms or len(atoms) > 3:
        raise ValueError('Compression requires one to three joint atoms')
    for size in range(1, len(atoms) + 1):
        equivalent = [_canonical(subset) for subset in combinations(atoms, size)
                      if implies(tuple(subset), atoms)]
        if equivalent:
            return min(equivalent)
    raise AssertionError('Original query must imply itself')

def rule_rank(rule):
    atoms = pattern(rule)
    return (len(atoms), len({v for a in atoms for v, _ in pairs(a)}),
            sum(_predicate(a[0])[1] or 0 for a in atoms), rule['rule_key'])

def semantic_groups(rules):
    groups = defaultdict(list)
    cores = [semantic_core(rule) for rule in rules]
    for i, core in enumerate(cores):
        groups[core].append(i)
    members = list(groups.values())
    representatives = [min(group, key=lambda i: rule_rank(rules[i])) for group in members]
    return representatives, members, [cores[i] for i in representatives]
