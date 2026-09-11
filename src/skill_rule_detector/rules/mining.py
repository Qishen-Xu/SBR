"""Entity-level Horn discovery using typed variable joins and per-skill support."""

from __future__ import annotations
import hashlib
import math
import multiprocessing
import os
from concurrent.futures import ProcessPoolExecutor
from collections import defaultdict
from functools import lru_cache
from typing import Any
from .schemas import HornAtom, HornMiningMeta, HornRule
from .vocabulary import _AGENT_TOOL_NAMES, PREDICATES
from .signature import canonical_rule_signature

_PARALLEL_CONTEXT = None


def _rule_sort_key(rule: dict[str, Any] | HornRule) -> str:
    """Canonical storage order, independent of empirical quality scores."""
    data = rule.model_dump() if isinstance(rule, HornRule) else rule
    return canonical_rule_signature(data)


def _mine_entity_head_worker(task: tuple[tuple[str, str, str], dict]) -> list[dict]:
    """Mine one head using the fork-inherited global corpus context."""
    hsig, params = task
    if _PARALLEL_CONTEXT is None:
        raise RuntimeError("parallel entity-miner context was not initialized")
    result = _mine_horn_rules_entity_serial(
        [],
        _prepared=_PARALLEL_CONTEXT,
        _head_filter={hsig},
        _assign_ids=False,
        **params,
    )
    return result["rules"]


def to_entity_facts(
    graph: dict[str, Any],
) -> tuple[set[tuple[str, str, str]], dict[str, str]]:
    """Convert a skill graph to (facts, entity_type) at the ENTITY level.

    facts: set of (pred, subj_name, obj_name) using CONCRETE entity names.
    entity_type: name -> type label (FILE/NETWORK/.../AGENT). Tool-name edge
        sources/targets that have no node are registered as type AGENT.

    Only edges whose predicate is in PREDICATES and whose endpoints resolve to
    a node or an AGENT tool-name are kept (orphans skipped, same rule as the
    typed miner).
    """
    entity_type: dict[str, str] = {}
    for n in graph.get("nodes", []):
        name = n.get("name", "")
        if name:
            entity_type[name] = n.get("type", "UNKNOWN")
    facts = set()
    for row in graph.get('unary_facts', []):
        pred, entity = row['predicate'], row['entity']
        if not pred.startswith('ML_NODE_LOCAL_GE_'):
            raise ValueError('Only ML_NODE predicates are unary')
        if entity in entity_type:
            facts.add((pred, entity))
    for e in graph.get("edges", []):
        src, tgt, pred = (
            e.get("source", ""),
            e.get("target", ""),
            e.get("edge_type", ""),
        )
        if pred not in PREDICATES:
            continue
        if src not in entity_type and src in _AGENT_TOOL_NAMES:
            entity_type[src] = "AGENT"
        if tgt not in entity_type and tgt in _AGENT_TOOL_NAMES:
            entity_type[tgt] = "AGENT"
        if src not in entity_type or tgt not in entity_type:
            continue
        if pred.startswith('ML_NODE_LOCAL_GE_'):
            if src != tgt:
                raise ValueError('Legacy ML_NODE fact must be a self-loop')
            facts.add((pred, src))
        else:
            facts.add((pred, src, tgt))
    return (facts, entity_type)


PatternAtom = tuple[str, str, str] | tuple[str, str, str, str, str]


def _ground_atom(
    atom: PatternAtom,
    binding: dict[str, str],
    pred_index: dict[str, list[tuple[str, str]]],
    etype: dict[str, str],
) -> list[dict[str, str]]:
    """All extensions of `binding` satisfying `atom` (natural-join step).

    Enforces: predicate match, type constraints, and join with already-bound
    variables. Variables not yet bound get bound here.
    """
    if len(atom) == 3:
        pred, va, ta = atom
        out = []
        for values in pred_index.get(pred, []):
            # Read historical self-loop indexes for compatibility only.
            if len(values) == 2 and values[0] != values[1]:
                raise ValueError('Legacy ML_NODE fact must be a self-loop')
            entity = values[0]
            if etype.get(entity) == ta and (va not in binding or binding[va] == entity):
                out.append({**binding, va: entity})
        return out
    pred, va, ta, vb, tb = atom
    out: list[dict[str, str]] = []
    for subj, obj in pred_index.get(pred, []):
        if etype.get(subj) != ta or etype.get(obj) != tb:
            continue
        if va in binding and binding[va] != subj:
            continue
        if vb in binding and binding[vb] != obj:
            continue
        if va == vb and subj != obj:
            continue
        nb = dict(binding)
        nb[va] = subj
        nb[vb] = obj
        out.append(nb)
    return out


def _ground(atoms: list[PatternAtom], pred_index, etype) -> list[dict[str, str]]:
    bindings: list[dict[str, str]] = [dict()]
    for atom in atoms:
        nxt: list[dict[str, str]] = []
        for b in bindings:
            nxt.extend(_ground_atom(atom, b, pred_index, etype))
        bindings = nxt
        if not bindings:
            break
    return bindings


def _holds(atoms: list[PatternAtom], pred_index, etype) -> bool:
    """True iff at least one grounding exists (body or body∧head satisfiable)."""
    return bool(_ground(atoms, pred_index, etype))


def _observed_signatures(per_skill) -> list[tuple[str, str, str]]:
    """Distinct (pred, subj_type, obj_type) seen in the corpus.

    This is the SAME candidate universe the typed miner uses. The data alone
    decides which type signatures exist; we add nothing.
    """
    sig: set[tuple[str, str, str]] = set()
    for _name, facts, etype, _idx in per_skill:
        for pred, *values in facts:
            sig.add((pred, *(etype.get(v, 'UNKNOWN') for v in values)))
    return sorted(sig)


def _state_var_types(hpat: PatternAtom, body: list[PatternAtom]) -> dict[str, str]:
    """Variable -> required type for the current rule (head ∪ body)."""
    types: dict[str, str] = {hpat[1]: hpat[2], hpat[3]: hpat[4]}
    for atom in body:
        types[atom[1]] = atom[2]
        if len(atom) == 5:
            types[atom[3]] = atom[4]
    return types


def _enumerate_candidate_atoms(
    hpat: PatternAtom,
    body: list[PatternAtom],
    signatures: list[tuple[str, str, str]],
    *,
    connected_only: bool = True,
) -> list[PatternAtom]:
    """All signature atoms extendable onto the CURRENT rule state (head ∪ body).


    This is the complete AMIE-style refinement operator set over our frozen
    variable-only language (no constant instantiation):

      * closing atom  — both argument positions reuse existing variables
                        (includes same-variable atoms when ta == tb);
      * dangling atom — one position reuses an existing variable, the other
                        introduces a fresh variable (deterministically named
                        from the current state size);
      * when ``connected_only=False`` only: fully independent atoms (both
        positions fresh).

    Tautology filter (variable-aware): a body atom with the head's predicate
    whose SUBJECT variable is the head's subject variable implies the head
    under existential semantics and is skipped. The previous filter (same
    predicate + same type signature) was stricter and also dropped the
    informative reversed atom ``H(?y, ?x)``.
    """
    types = _state_var_types(hpat, body)
    hp, hx, _hta, _hy, _htb = hpat
    nvars = len(types)
    out: list[PatternAtom] = []
    for signature in signatures:
        if len(signature) == 2:
            p, ta = signature
            out.extend((p, v, ta) for v in types if types[v] == ta)
            if not connected_only:
                out.append((p, f'?f{nvars * 2}', ta))
            continue
        p, ta, tb = signature
        subj_cands = [v for v in types if types[v] == ta]
        obj_cands = [v for v in types if types[v] == tb]
        for v1 in subj_cands:
            for v2 in obj_cands:
                if v1 == v2 and ta != tb:
                    continue
                out.append((p, v1, ta, v2, tb))
        fs, fo = (f"?f{nvars * 2}", f"?f{nvars * 2 + 1}")
        for v2 in obj_cands:
            out.append((p, fs, ta, v2, tb))
        for v1 in subj_cands:
            out.append((p, v1, ta, fo, tb))
        if not connected_only:
            out.append((p, fs, ta, fo, tb))
    seen: set[tuple] = set()
    uniq: list[PatternAtom] = []
    hy = hpat[3]
    hx_bound = any(hx in a[1::2] for a in body)
    for a in out:
        if a[0] == hp and a[1] == hx:
            continue
        if len(a) == 5 and a[0] == hp and a[3] == hy and (not hx_bound):
            continue
        if a not in seen:
            seen.add(a)
            uniq.append(a)
    return uniq


def _prepare_entity_context(
    graphs: list[dict[str, Any]],
) -> tuple[list[tuple[str, set, dict, dict]], list[tuple[str, str, str]]]:
    """Build the complete read-only corpus context once.

    The context is the semantic unit shared by all head workers. In particular,
    it must not be replaced by shard-local support counts: support and head coverage
    are defined over the whole skill population.
    """
    per_skill: list[tuple[str, set, dict, dict]] = []
    name_counts: dict[str, int] = {}
    for graph in graphs:
        name = graph.get("skill_name", "unknown")
        seen = name_counts.get(name, 0)
        name_counts[name] = seen + 1
        if seen:
            name = f"{name}#dup{seen}"
        facts, etype = to_entity_facts(graph)
        if not facts:
            continue
        index: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for pred, *values in sorted(facts):
            index[pred].append(tuple(values))
        per_skill.append((name, facts, etype, index))
    per_skill.sort(key=lambda row: row[0])
    return (per_skill, _observed_signatures(per_skill))


def _mine_horn_rules_entity_serial(
    graphs: list[dict[str, Any]],
    *,
    min_support: int = 5,
    min_confidence: float = 0.3,
    max_body_len: int = 2,
    connected_only: bool = True,
    min_head_coverage: float = 0.0,
    min_head_support: int | None = None,
    _prepared: (
        tuple[list[tuple[str, set, dict, dict]], list[tuple[str, str, str]]] | None
    ) = None,
    _head_filter: set[tuple[str, str, str]] | None = None,
    _assign_ids: bool = True,
) -> dict[str, Any]:
    """Mine ENTITY-level Horn rules across all skill graphs.

    Mirrors mine_horn_rules() structure (level-wise, anti-monotone) but with
    entity-level grounding. Candidate atoms come ONLY from observed type
    signatures; nothing is hand-crafted.
    """
    per_skill, signatures = (
        _prepare_entity_context(graphs) if _prepared is None else _prepared
    )
    total_skills = len(per_skill)
    predicates_seen = sorted({s[0] for s in signatures})
    if total_skills == 0 or not signatures:
        return _empty_result(
            0,
            predicates_seen,
            min_support,
            min_confidence,
            max_body_len,
            connected_only,
        )

    def make_head(sig) -> PatternAtom:
        p, ta, tb = sig
        return (p, "?x", ta, "?y", tb)

    head_support_cache: dict[tuple, int] = {}

    def head_support(hpat: PatternAtom) -> int:
        if hpat not in head_support_cache:
            head_support_cache[hpat] = sum(
                (1 for _n, _f, et, fi in per_skill if _holds([hpat], fi, et))
            )
        return head_support_cache[hpat]

    rules: list[HornRule] = []
    rule_seen: set[tuple] = set()

    @lru_cache(maxsize=32768)
    def matching_skills(atoms):
        if len(atoms) == 1:
            candidates = range(len(per_skill))
        else:
            candidates = matching_skills(atoms[:-1]) & matching_skills(atoms[-1:])
        return frozenset(
            (
                i
                for i in candidates
                if _holds(list(atoms), per_skill[i][3], per_skill[i][2])
            )
        )

    def support_of(body: list[PatternAtom], hpat: PatternAtom):
        body_ids = matching_skills(tuple(sorted(body)))
        joint_ids = matching_skills(tuple(sorted(body + [hpat])))
        return ({per_skill[i][0] for i in joint_ids}, len(body_ids))

    def emit_supported(hpat, body, supp, body_cnt, hs):
        context = [per_skill[i] for i in sorted(matching_skills(tuple(sorted(body))))]
        _emit(
            rules,
            rule_seen,
            hpat,
            body,
            supp,
            body_cnt,
            hs,
            context,
            min_confidence,
            min_head_coverage,
        )

    for hsig in signatures:
        if len(hsig) != 3:
            continue  # Heads remain binary behavior relations.
        if _head_filter is not None and hsig not in _head_filter:
            continue
        hpat = make_head(hsig)
        hs = head_support(hpat)
        if hs < (min_support if min_head_support is None else min_head_support):
            continue
        support_bound = max(
            min_support,
            math.ceil(min_head_coverage * hs) if min_head_coverage > 0 else 0,
        )
        level = 1
        frontier: list[list[PatternAtom]] = []
        for b1 in _enumerate_candidate_atoms(
            hpat, [], signatures, connected_only=connected_only
        ):
            body = [b1]
            supp, body_cnt = support_of(body, hpat)
            if len(supp) >= support_bound:
                frontier.append(body)
                emit_supported(hpat, body, supp, body_cnt, hs)
        while level < max_body_len:
            next_frontier: list[list[PatternAtom]] = []
            level_seen = set()
            for body in frontier:
                for candidate in _enumerate_candidate_atoms(
                    hpat, body, signatures, connected_only=connected_only
                ):
                    if candidate in body:
                        continue
                    new_body = body + [candidate]
                    body_key = tuple(sorted(new_body))
                    if body_key in level_seen:
                        continue
                    level_seen.add(body_key)
                    supp, body_cnt = support_of(new_body, hpat)
                    if len(supp) >= support_bound:
                        next_frontier.append(new_body)
                        emit_supported(hpat, new_body, supp, body_cnt, hs)
            if not next_frontier:
                break
            frontier = next_frontier
            level += 1
    if _assign_ids:
        rules.sort(key=_rule_sort_key)
        for i, r in enumerate(rules):
            r.rule_id = f"RE{i + 1:03d}"
    meta = HornMiningMeta(
        skills_total=total_skills,
        triples_total=sum((len(f) for _n, f, _e, _i in per_skill)),
        predicates=predicates_seen,
        rules_found=len(rules),
        min_support=min_support,
        min_confidence=min_confidence,
        min_head_coverage=min_head_coverage,
        min_head_support=min_support if min_head_support is None else min_head_support,
        max_body_len=max_body_len,
        connected_only=connected_only,
        note="ENTITY-level Horn mining: real variable binding / join over concrete entities; candidate atoms from observed signatures only (no hand-crafted type/variable bias); per-skill dedup.",
    )
    return {
        "statistics": {
            "total_skills": total_skills,
            "triples_total": sum((len(f) for _n, f, _e, _i in per_skill)),
            "predicates": predicates_seen,
        },
        "rules": [r.model_dump(exclude_none=True) for r in rules],
        "mining": meta.model_dump(),
    }


def mine_horn_rules_entity(
    graphs: list[dict[str, Any]],
    *,
    min_support: int = 5,
    min_confidence: float = 0.3,
    max_body_len: int = 2,
    connected_only: bool = True,
    min_head_coverage: float = 0.0,
    min_head_support: int | None = None,
    workers: int = 1,
    head_predicates: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Mine entity-level rules, optionally sharding independent heads.

    Head shards all use the same complete corpus context. This is deliberately
    not a skill-shard reduction: support, support and head coverage remain global.
    ``head_predicates`` restricts heads only; all observed body signatures
    remain available. ``None`` preserves the unrestricted head language.
    ``workers=1`` is the reference serial path; Linux ``fork`` is used for
    CPU-only workers so the read-mostly context is inherited without pickling
    it once per task.
    """
    if workers < 1:
        raise ValueError("workers must be >= 1")
    if max_body_len < 1:
        raise ValueError("max_body_len must be >= 1")
    selected_prepared = (
        _prepare_entity_context(graphs) if head_predicates is not None else None
    )
    selected_heads = (
        {sig for sig in selected_prepared[1] if sig[0] in head_predicates}
        if selected_prepared is not None
        else None
    )
    if workers == 1:
        result = _mine_horn_rules_entity_serial(
            graphs,
            min_support=min_support,
            min_confidence=min_confidence,
            max_body_len=max_body_len,
            connected_only=connected_only,
            min_head_coverage=min_head_coverage,
            min_head_support=min_head_support,
            _prepared=selected_prepared,
            _head_filter=selected_heads,
        )
        result.setdefault("mining", {})["workers"] = 1
        result.setdefault("mining", {})["start_method"] = "serial"
        return result
    if os.name != "posix":
        result = _mine_horn_rules_entity_serial(
            graphs,
            min_support=min_support,
            min_confidence=min_confidence,
            max_body_len=max_body_len,
            connected_only=connected_only,
            min_head_coverage=min_head_coverage,
            min_head_support=min_head_support,
            _prepared=selected_prepared,
            _head_filter=selected_heads,
        )
        result.setdefault("mining", {})["workers"] = 1
        result.setdefault("mining", {})["start_method"] = "serial-fallback"
        return result
    prepared = (
        selected_prepared
        if selected_prepared is not None
        else _prepare_entity_context(graphs)
    )
    per_skill, signatures = prepared
    if not per_skill or not signatures:
        result = _empty_result(
            0, [], min_support, min_confidence, max_body_len, connected_only
        )
        result.setdefault("mining", {})["workers"] = workers
        result.setdefault("mining", {})["start_method"] = "fork"
        return result
    global _PARALLEL_CONTEXT
    _PARALLEL_CONTEXT = prepared
    params = {
        "min_support": min_support,
        "min_confidence": min_confidence,
        "max_body_len": max_body_len,
        "connected_only": connected_only,
        "min_head_coverage": min_head_coverage,
        "min_head_support": min_head_support,
    }
    tasks = [
        (hsig, params)
        for hsig in signatures
        if len(hsig) == 3 and (selected_heads is None or hsig in selected_heads)
    ]
    ctx = multiprocessing.get_context("fork")
    rule_dicts: list[dict] = []
    with ProcessPoolExecutor(max_workers=workers, mp_context=ctx) as pool:
        for part in pool.map(_mine_entity_head_worker, tasks, chunksize=1):
            rule_dicts.extend(part)
    unique: dict[str, dict] = {}
    for row in rule_dicts:
        unique.setdefault(canonical_rule_signature(row), row)
    rules = list(unique.values())
    rules.sort(key=_rule_sort_key)
    for i, row in enumerate(rules, 1):
        row["rule_id"] = f"RE{i:03d}"
        if not row.get("rule_key"):
            row["rule_key"] = hashlib.sha256(
                canonical_rule_signature(row).encode("utf-8")
            ).hexdigest()[:16]
    triples_total = sum((len(facts) for _name, facts, _etype, _idx in per_skill))
    meta = HornMiningMeta(
        skills_total=len(per_skill),
        triples_total=triples_total,
        predicates=sorted({sig[0] for sig in signatures}),
        rules_found=len(rules),
        min_support=min_support,
        min_confidence=min_confidence,
        min_head_coverage=min_head_coverage,
        min_head_support=min_support if min_head_support is None else min_head_support,
        max_body_len=max_body_len,
        connected_only=connected_only,
        note="ENTITY-level Horn mining: head-sharded multiprocessing; complete corpus context per worker; global per-skill dedup; deterministic canonical order.",
    )
    return {
        "statistics": {
            "total_skills": len(per_skill),
            "triples_total": triples_total,
            "predicates": sorted({sig[0] for sig in signatures}),
        },
        "rules": rules,
        "mining": {**meta.model_dump(), "workers": workers, "start_method": "fork"},
    }


def _emit(
    rules,
    rule_seen,
    hpat,
    body,
    supp_set,
    body_cnt,
    head_supp,
    per_skill,
    min_confidence,
    min_head_coverage=0.0,
) -> None:

    def to_atom(a: PatternAtom) -> HornAtom:
        if len(a) == 3:
            p, va, ta = a
            return HornAtom(predicate=p, arg1=f'{va}:{ta}')
        p, va, ta, vb, tb = a
        return HornAtom(predicate=p, arg1=f"{va}:{ta}", arg2=f"{vb}:{tb}")

    draft = {
        "head": to_atom(hpat).model_dump(exclude_none=True),
        "body": [to_atom(a).model_dump(exclude_none=True) for a in sorted(body)],
    }
    key = canonical_rule_signature(draft)
    if key in rule_seen:
        return
    rule_seen.add(key)
    if body_cnt == 0:
        return
    std = len(supp_set) / body_cnt
    if std < min_confidence:
        return
    head_cov = len(supp_set) / head_supp if head_supp > 0 else 0.0
    if head_cov < min_head_coverage:
        return
    rule_key = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
    rules.append(
        HornRule(
            rule_id="TMP",
            rule_key=rule_key,
            head=to_atom(hpat),
            body=[to_atom(a) for a in sorted(body)],
            support=len(supp_set),
            body_support=body_cnt,
            head_support=head_supp,
            standard_confidence=round(std, 4),
            head_coverage=round(head_cov, 4),
            example_skills=sorted(supp_set)[:5],
        )
    )


def _empty_result(
    total, predicates, min_support, min_confidence, max_body_len, connected_only
):
    meta = HornMiningMeta(
        skills_total=total,
        triples_total=0,
        predicates=predicates,
        rules_found=0,
        min_support=min_support,
        min_confidence=min_confidence,
        max_body_len=max_body_len,
        connected_only=connected_only,
        note="ENTITY-level Horn mining (no data).",
    )
    return {
        "statistics": {
            "total_skills": total,
            "triples_total": 0,
            "predicates": predicates,
        },
        "rules": [],
        "mining": meta.model_dump(),
    }
