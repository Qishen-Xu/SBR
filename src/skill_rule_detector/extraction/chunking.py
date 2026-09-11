"""Preserve frontmatter and merge validated document chunks."""

from .loader import SkillCorpus
from .hints import path_match_fallback
from .parser import ScriptHint, ExtractionResult

CHUNK_THRESHOLD = 16000
TARGET_CHUNK_SIZE = 12000
MIN_CHUNK_SIZE = 1500


def _refs_from_corpus(corpus: SkillCorpus) -> list[ScriptHint]:
    """Match script filenames in SKILL.md and construct hints for graph extraction."""
    refs = path_match_fallback(corpus)
    return [
        ScriptHint(
            script_path=r.script_path,
            skill_md_section=r.skill_md_section,
            skill_md_quote=r.skill_md_quote,
            execution_context=r.execution_context,
            decision_source=r.decision_source,
            decision_confidence=r.decision_confidence,
        )
        for r in refs
    ]


def _split_frontmatter(text: str) -> tuple[str, str]:
    """Return (frontmatter_block_with_delimiters, body). Frontmatter is the
    leading ```yaml-style --- ... --- block; body is the rest."""
    if not text.startswith("---"):
        return ("", text)
    end = text.find("\n---", 3)
    if end == -1:
        return ("", text)
    fm = text[: end + 4]
    body = text[end + 4 :].lstrip("\n")
    return (fm, body)


def chunk_skill_md(text: str) -> list[str]:
    """Split a SKILL.md into chunks, each prefixed with the frontmatter.

    - Docs ≤ CHUNK_THRESHOLD: returned as one chunk (whole doc).
    - Longer docs: split by markdown headers, balanced to ~TARGET_CHUNK_SIZE,
      with the frontmatter prepended to every chunk so the LLM always sees
      name/description/allowed-tools metadata.
    """
    if len(text) <= CHUNK_THRESHOLD:
        return [text]
    fm, body = _split_frontmatter(text)
    import re

    parts = re.split("(?=^#{1,4}\\s)", body, flags=re.MULTILINE)
    parts = [p for p in parts if p.strip()]
    chunks: list[str] = []
    cur = ""
    for p in parts:
        if len(cur) + len(p) <= TARGET_CHUNK_SIZE or len(cur) < MIN_CHUNK_SIZE:
            cur += p
        else:
            chunks.append(cur)
            cur = p
    if cur.strip():
        chunks.append(cur)
    if fm:
        chunks = [f"{fm}\n\n{c}" for c in chunks]
    return chunks


def _merge_chunk_results(results: list[ExtractionResult]) -> ExtractionResult:
    """Merge per-chunk Step2Results into one.

    Nodes merged by canonical_id (first wins; constraints unioned). Edges
    appended and deduped by (source, target, edge_type, source_section).
    Dropped logs concatenated. The first result's skill_name is kept.
    """
    if len(results) == 1:
        return results[0]
    base = results[0]
    merged_nodes_by_cid: dict[str, dict] = {}
    node_order: list[str] = []
    seen_edges: set[tuple] = set()
    merged_edges: list[dict] = []
    dropped_entities: list[dict] = []
    dropped_edges: list[dict] = []
    placeholder_audits = list(base.placeholder_audits)
    llm_error = base.llm_error
    for r in results:
        if r.llm_error:
            llm_error = r.llm_error
        for n in r.nodes:
            cid = getattr(n, "canonical_id", "") or str(id(n))
            if cid not in merged_nodes_by_cid:
                merged_nodes_by_cid[cid] = n
                node_order.append(cid)
            else:
                existing = merged_nodes_by_cid[cid]
                if hasattr(existing, "constraints") and hasattr(n, "constraints"):
                    seen = {(c.text, c.source_section) for c in existing.constraints}
                    for c in n.constraints:
                        if (c.text, c.source_section) not in seen:
                            existing.constraints.append(c)
                            seen.add((c.text, c.source_section))
        for e in r.edges:
            key = (
                e.get("source"),
                e.get("target"),
                e.get("edge_type"),
                e.get("source_section"),
            )
            if key not in seen_edges:
                seen_edges.add(key)
                merged_edges.append(e)
        dropped_entities.extend(r.dropped_entities)
        dropped_edges.extend(r.dropped_edges)
    return ExtractionResult(
        skill_name=base.skill_name,
        nodes=[merged_nodes_by_cid[c] for c in node_order],
        edges=merged_edges,
        dropped_entities=dropped_entities,
        dropped_edges=dropped_edges,
        placeholder_audits=placeholder_audits,
        llm_error=llm_error,
    )
