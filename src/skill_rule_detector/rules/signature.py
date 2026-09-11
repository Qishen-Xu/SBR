"""Canonical identities for Horn rules, invariant to variable renaming."""

from __future__ import annotations
import itertools
import re
from typing import Any
from .arity import is_node, normalize_rule, arguments

_ATOM_RE = re.compile("^([A-Z][A-Z0-9_]*)\\(([^()]*)\\)$")


def _split_typed_arg(value: Any) -> tuple[str, str]:
    text = str(value)
    if ":" not in text:
        return (text, "")
    return tuple(text.split(":", 1))


def typed_rule_from_fol(fol: str) -> dict[str, Any]:
    """Parse the classifier's FOL rendering into typed head/body atoms."""
    sides = fol.split("⇒")
    if len(sides) != 2:
        raise ValueError("FOL rule must contain one implication")
    parsed_sides: list[list[tuple[str, list[str]]]] = []
    for side in sides:
        atoms: list[tuple[str, list[str]]] = []
        for text in (part.strip() for part in side.split("∧")):
            if not text:
                continue
            match = _ATOM_RE.match(text)
            if not match:
                raise ValueError(f"invalid FOL atom: {text!r}")
            args = [arg.strip() for arg in match.group(2).split(",")]
            if len(args) not in (1, 2) or any((not arg for arg in args)):
                raise ValueError(f"unsupported FOL atom arity: {text!r}")
            atoms.append((match.group(1), args))
        parsed_sides.append(atoms)
    type_of: dict[str, str] = {}
    for atoms in parsed_sides:
        for predicate, args in atoms:
            if len(args) != 1 or is_node(predicate):
                continue
            previous = type_of.get(args[0])
            if previous is not None and previous != predicate:
                raise ValueError(f"variable {args[0]} has conflicting types")
            type_of[args[0]] = predicate

    def typed_atom(predicate: str, args: list[str]) -> dict[str, str]:
        missing = [arg for arg in args if arg not in type_of]
        if missing:
            raise ValueError(f"untyped FOL variables: {missing}")
        result = {
            "predicate": predicate,
            "arg1": f"{args[0]}:{type_of[args[0]]}",
        }
        if len(args) == 2:
            result['arg2'] = f'{args[1]}:{type_of[args[1]]}'
        return result

    body = [
        typed_atom(predicate, args)
        for predicate, args in parsed_sides[0]
        if len(args) == 2 or is_node(predicate)
    ]
    head_relations = [
        typed_atom(predicate, args)
        for predicate, args in parsed_sides[1]
        if len(args) == 2
    ]
    if len(head_relations) != 1:
        raise ValueError("FOL rule head must contain one binary atom")
    types = [
        {"predicate": predicate, "arg1": args[0]}
        for atoms in parsed_sides
        for predicate, args in atoms
        if len(args) == 1 and not is_node(predicate)
    ]
    return {"body": body, "head": head_relations[0], "types": types}


def canonical_rule_signature(rule: dict[str, Any]) -> str:
    """Return an ID-independent, alpha-equivalent typed rule signature.

    The head remains distinct, body atoms are sorted, argument types are kept,
    and variable names are normalized. Rules that differ only by ``rule_id``,
    variable spelling, or body ordering therefore receive the same signature.

    Unary type atoms are DERIVED from the typed binary atoms
    (``?x:PACKAGE`` implies ``PACKAGE(?x)``) and unioned with any explicit
    ``types`` list. Dict-form rules (types embedded in args) and FOL-form
    rules (unary atoms spelled out) now produce the SAME signature, so
    cross-artifact label matching cannot silently fall back to display IDs.
    """
    if not isinstance(rule.get("head"), dict):
        fol = rule.get("fol")
        if not isinstance(fol, str) or not fol.strip():
            raise ValueError("rule must contain typed head/body atoms or FOL")
        rule = typed_rule_from_fol(fol)
    rule = normalize_rule(rule)
    atoms = list(rule.get("body", [])) + [rule["head"]]
    derived_types: set[tuple[str, str]] = set()
    for atom in atoms:
        for key in ("arg1", "arg2"):
            variable, arg_type = _split_typed_arg(atom.get(key) or "")
            if variable.startswith("?") and arg_type:
                derived_types.add((arg_type, variable))
    for type_atom in rule.get("types", []):
        derived_types.add((type_atom.get("predicate", ""), type_atom.get("arg1", "")))
    type_atoms = [{"predicate": p, "arg1": v} for p, v in sorted(derived_types)]
    variables = sorted(
        {
            variable
            for atom in atoms + type_atoms
            for key in ("arg1", "arg2")
            for variable, _type in (_split_typed_arg(atom.get(key, "")),)
            if variable.startswith("?")
        }
    )
    canonical_names = tuple((f"?v{i}" for i in range(len(variables))))

    def render_atom(atom: dict[str, Any], mapping: dict[str, str]) -> str:
        rendered_args = []
        for key in ("arg1", "arg2"):
            if atom.get(key) is None:
                continue
            variable, arg_type = _split_typed_arg(atom.get(key, ""))
            rendered_args.append(f"{mapping.get(variable, variable)}:{arg_type}")
        return f"{atom.get('predicate', '')}({','.join(rendered_args)})"

    signatures = []
    for renamed in itertools.permutations(canonical_names):
        mapping = dict(zip(variables, renamed))
        head = render_atom(rule["head"], mapping)
        body = sorted((render_atom(atom, mapping) for atom in rule.get("body", [])))
        types = sorted(
            (
                f"{atom.get('predicate', '')}({mapping.get(atom.get('arg1', ''), atom.get('arg1', ''))})"
                for atom in type_atoms
            )
        )
        signatures.append(
            f"head:{head};body:[{';'.join(body)}];types:[{';'.join(types)}]"
        )
    return min(signatures)
