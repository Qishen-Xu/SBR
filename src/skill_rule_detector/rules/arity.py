"""Predicate arity and lossless migration of historical node self-loop rules."""
import copy
import hashlib

def is_node(predicate):
    return predicate.startswith('ML_NODE_LOCAL_GE_')

def arguments(atom):
    return [atom[k] for k in ('arg1', 'arg2') if atom.get(k) is not None]

def normalize_rule(rule):
    result = copy.deepcopy(rule)
    atoms = result['body'] + [result['head']]
    parent, types = {}, {}
    def root(v):
        parent.setdefault(v, v)
        while parent[v] != v:
            v = parent[v]
        return v
    for atom in atoms:
        for arg in arguments(atom):
            v, t = arg.split(':', 1)
            if v in types and types[v] != t:
                raise ValueError('Conflicting variable types')
            types[v] = t
            root(v)
    for atom in atoms:
        if is_node(atom['predicate']) and atom.get('arg2') is not None:
            x, tx = atom['arg1'].split(':', 1)
            y, ty = atom['arg2'].split(':', 1)
            if tx != ty:
                raise ValueError('Unsatisfiable ML_NODE type constraints')
            left, right = sorted((root(x), root(y)))
            parent[right] = left
    for atom in atoms:
        for k in ('arg1', 'arg2'):
            if atom.get(k) is not None:
                v, t = atom[k].split(':', 1)
                atom[k] = f'{root(v)}:{t}'
        if is_node(atom['predicate']):
            atom.pop('arg2', None)
    return result

def migrate_identity(rule):
    from .signature import canonical_rule_signature
    migrated = normalize_rule(rule)
    key = hashlib.sha256(canonical_rule_signature(migrated).encode()).hexdigest()[:16]
    if rule.get('rule_key') != key:
        migrated['legacy_rule_key'] = rule.get('rule_key')
    migrated['rule_key'] = key
    # Historical text/type renderings can contain variables removed by unification.
    migrated.pop('fol', None)
    migrated.pop('types', None)
    return migrated

def pairs(atom):
    return tuple(zip(atom[1::2], atom[2::2]))
