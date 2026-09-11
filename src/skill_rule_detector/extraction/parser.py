"""LLM graph extraction, schema validation, and script placeholder resolution."""

from __future__ import annotations
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable, Protocol
from .schemas import (
    NodeBase,
    NodeMeta,
    NodeType,
    SourceLayer,
    FileNode,
    validate_extraction,
)
from .loader import split_skill_md_sections

logging.getLogger(__package__ + ".schemas").setLevel(logging.ERROR)
logger = logging.getLogger(__name__)


@dataclass
class ScriptHint:
    """A referenced script filename and its source text context."""

    script_path: str
    skill_md_section: str = ""
    skill_md_quote: str = ""
    execution_context: str = ""
    decision_source: str = "llm"
    decision_confidence: float = 1.0


_PROMPTS_DIR = Path(__file__).parent / "prompts"


def _load_prompt(filename: str) -> str:
    return (_PROMPTS_DIR / filename).read_text(encoding="utf-8")


def system_prompt() -> str:
    """Compose the five frozen extraction prompt fragments in their original order."""
    return "\n\n---\n\n".join(
        [
            _load_prompt("skill_node_types_guidance.md"),
            _load_prompt("skill_edge_types_guidance.md"),
            _load_prompt("skill_extraction_examples.md"),
            _load_prompt("placeholder_instructions.md"),
            _load_prompt("constraint_extraction_prompt.md"),
        ]
    )


def user_prompt(skill_md_text: str, references: list[ScriptHint]) -> str:
    """Render the user prompt: SKILL.md text + Provided Script Hints block."""
    hints_lines = ["Provided Script Hints (from Step 1 indexing):\n"]
    for i, ref in enumerate(references):
        ctx = f" — {ref.execution_context}" if ref.execution_context else ""
        sect = f" (section: {ref.skill_md_section})" if ref.skill_md_section else ""
        hint_path = _normalize_script_path(ref.script_path)
        hints_lines.append(f"  [{i}] {hint_path}{sect}{ctx}")
    hints_block = (
        "\n".join(hints_lines)
        if references
        else "(no scripts/ references found by Step 1)"
    )
    return f"Extract entities, operations, and constraints from the following SKILL.md. Follow the schema in the system prompt. Pay special attention to the placeholder instructions for any `scripts/...` path you encounter.\n\n=== SKILL.md ===\n{skill_md_text}\n\n=== Provided Script Hints ===\n{hints_block}\n\nEmit your JSON now."


class LLMCall(Protocol):

    async def __call__(self, system_prompt: str, user_prompt: str) -> str: ...


def _strip_markdown_fences(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        first_nl = cleaned.find("\n")
        if first_nl > 0:
            cleaned = cleaned[first_nl + 1 :]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    return cleaned.strip()


@dataclass
class PlaceholderAudit:
    """Per-placeholder audit record. Returned to the caller for logging."""

    script_path: str
    placeholder_id: str
    decision_source: str
    script_reference_index: int


def _normalize_script_path(path: str) -> str:
    """Normalize to a canonical scripts/-bearing POSIX path.

    Accepts paths like:
      - "scripts/office/soffice.py"                       → unchanged
      - "office/soffice.py"                               → "scripts/office/soffice.py"
      - "./scripts/office/x.py"                           → "scripts/office/x.py"
      - "skills/self-improvement/scripts/activator.sh"    → unchanged (has scripts/ segment)
      - "./skills/self-improvement/scripts/activator.sh"  → unchanged

    The path already contains a ``scripts/`` segment anywhere (not just as a
    prefix) is left as-is — prepending another ``scripts/`` would create a
    bogus ``scripts/scripts/...`` or ``scripts/skills/.../scripts/...`` name.
    """
    p = path.strip().lstrip("./")
    if p == "scripts/" or p.startswith("scripts/"):
        return p
    if "/scripts/" in p:
        return p
    return "scripts/" + p


def _collect_placeholder_paths(nodes: list[NodeBase]) -> dict[str, NodeBase]:
    """Map placeholder script_path -> the node that already represents it.

    Handles 3 cases the LLM might emit:
      - node.properties.path is the path (most common)
      - node.name is the path and properties.path is missing
      - node has is_placeholder=True in _meta (definite placeholder)
    """
    out: dict[str, NodeBase] = {}
    for n in nodes:
        path = None
        if isinstance(n, FileNode):
            path = n.properties.get("path")
        if not path:
            path = n.name
        path = _normalize_script_path(path)
        if not path.startswith("scripts/"):
            continue
        if path in out:
            if getattr(out[path].meta, "is_placeholder", False):
                continue
            if getattr(n.meta, "is_placeholder", False):
                out[path] = n
        else:
            out[path] = n
    return out


def _build_placeholder_node(
    path: str, *, placeholder_id: str, script_reference_index: int, decision_source: str
) -> FileNode:
    """Build a placeholder FILE node for `path`."""
    extension = Path(path).suffix.lstrip(".") or "unknown"
    return FileNode.model_validate(
        {
            "name": path,
            "type": "FILE",
            "properties": {
                "path": path,
                "extension": extension,
                "permission": "execute",
            },
            "_meta": {
                "is_placeholder": True,
                "placeholder_id": placeholder_id,
                "referenced_script_path": path,
                "script_reference_index": script_reference_index,
                "placeholder_decision_source": decision_source,
            },
        }
    )


def _build_placeholder_exec_edge(placeholder_id: str, source_section: str) -> dict:
    """Build the `Bash → placeholder_id` EXEC edge dict (LLM shape)."""
    return {
        "source": "Bash",
        "target": placeholder_id,
        "edge_type": "EXEC",
        "tool_used": "Bash",
        "description": f"execute script (placeholder for {placeholder_id})",
        "source_layer": "declaration",
        "source_section": source_section or None,
    }


def inject_placeholders(
    nodes: list[NodeBase],
    raw_edges: list[dict],
    references: list[ScriptHint],
    skill_name: str,
) -> tuple[list[NodeBase], list[dict], list[PlaceholderAudit]]:
    """Ensure each referenced script has a FILE node and normalize placeholder edge endpoints."""
    existing = _collect_placeholder_paths(nodes)
    seen_paths: set[str] = set()
    unique_refs: list[ScriptHint] = []
    for ref in references:
        p = _normalize_script_path(ref.script_path)
        if p in seen_paths:
            continue
        seen_paths.add(p)
        unique_refs.append(ref)
    audits: list[PlaceholderAudit] = []
    used_ids: set[str] = set()
    old_id_to_new: dict[str, str] = {}
    pid_to_name: dict[str, str] = {}
    llm_placeholders: list[tuple[FileNode, str, int | None]] = []
    for ref_idx, ref in enumerate(unique_refs):
        p = _normalize_script_path(ref.script_path)
        node = existing.get(p)
        if node is None:
            continue
        if not getattr(node.meta, "is_placeholder", False):
            node.meta.is_placeholder = True
        pid = f"PLACEHOLDER_{len(used_ids) + 1}"
        used_ids.add(pid)
        old_id = node.meta.placeholder_id
        if old_id and old_id != pid:
            old_id_to_new[old_id] = pid
        node.meta.placeholder_id = pid
        pid_to_name[pid] = node.name
        node.meta.referenced_script_path = p
        node.meta.script_reference_index = ref_idx
        node.meta.placeholder_decision_source = "llm"
        if node.properties.get("permission") != "execute":
            node.properties["permission"] = "execute"
        llm_placeholders.append((node, pid, ref_idx))
        audits.append(
            PlaceholderAudit(
                script_path=p,
                placeholder_id=pid,
                decision_source="llm",
                script_reference_index=ref_idx,
            )
        )
    referenced_paths = {_normalize_script_path(r.script_path) for r in unique_refs}
    for node in nodes:
        if not getattr(node.meta, "is_placeholder", False):
            continue
        if node.meta.referenced_script_path in referenced_paths:
            continue
        p = node.name
        pid = f"PLACEHOLDER_{len(used_ids) + 1}"
        used_ids.add(pid)
        old_id = node.meta.placeholder_id
        if old_id and old_id != pid:
            old_id_to_new[old_id] = pid
        node.meta.placeholder_id = pid
        pid_to_name[pid] = node.name
        node.meta.referenced_script_path = p
        node.meta.script_reference_index = None
        node.meta.placeholder_decision_source = "llm"
        if node.properties.get("permission") != "execute":
            node.properties["permission"] = "execute"
        llm_placeholders.append((node, pid, None))
        audits.append(
            PlaceholderAudit(
                script_path=p,
                placeholder_id=pid,
                decision_source="llm",
                script_reference_index=-1,
            )
        )
    new_nodes = list(nodes)
    new_edges: list[dict] = []
    for ref_idx, ref in enumerate(unique_refs):
        p = _normalize_script_path(ref.script_path)
        if p in existing:
            continue
        pid = f"PLACEHOLDER_{len(used_ids) + 1}"
        used_ids.add(pid)
        ph_node = _build_placeholder_node(
            p,
            placeholder_id=pid,
            script_reference_index=ref_idx,
            decision_source="auto",
        )
        new_nodes.append(ph_node)
        pid_to_name[pid] = ph_node.name
        new_edges.append(
            _build_placeholder_exec_edge(ph_node.name, ref.skill_md_section)
        )
        audits.append(
            PlaceholderAudit(
                script_path=p,
                placeholder_id=pid,
                decision_source="auto",
                script_reference_index=ref_idx,
            )
        )
    for e in raw_edges:
        src = e.get("source", "")
        tgt = e.get("target", "")
        src = old_id_to_new.get(src, src)
        tgt = old_id_to_new.get(tgt, tgt)
        src = pid_to_name.get(src, src)
        tgt = pid_to_name.get(tgt, tgt)
        e["source"] = src
        e["target"] = tgt
        new_edges.append(e)
    existing_edge_targets = {
        (e.get("source", ""), e.get("target", "")) for e in new_edges
    }
    for node, pid, ref_idx in llm_placeholders:
        if ref_idx is None:
            continue
        ref = unique_refs[ref_idx]
        if ("Bash", node.name) not in existing_edge_targets:
            new_edges.append(
                _build_placeholder_exec_edge(node.name, ref.skill_md_section)
            )
    return (new_nodes, new_edges, audits)


@dataclass
class ExtractionResult:
    """Validated graph elements with extraction and placeholder audit metadata."""

    skill_name: str
    nodes: list[NodeBase]
    edges: list[dict]
    dropped_entities: list[dict] = field(default_factory=list)
    dropped_edges: list[dict] = field(default_factory=list)
    placeholder_audits: list[PlaceholderAudit] = field(default_factory=list)
    llm_error: str | None = None


def build_section_index(skill_md_text: str) -> dict[str, int]:
    """Map markdown headings to section indices for declaration edge ordering."""
    sections = split_skill_md_sections(skill_md_text)
    index: dict[str, int] = {}
    for i, (heading, _body) in enumerate(sections):
        h = heading.strip().lstrip("#").strip()
        if h and h not in index:
            index[h] = i
    return index


def _resolve_section_index(
    source_section: str | None, section_index: dict[str, int]
) -> int:
    """Return the section index for an edge's source_section, or -1 if unknown."""
    if not source_section:
        return -1
    raw = source_section.strip().lstrip("#").strip()
    if not raw:
        return -1
    if raw in section_index:
        return section_index[raw]
    head = raw.split(">", 1)[0].strip()
    if head and head in section_index:
        return section_index[head]
    for heading, idx in section_index.items():
        if heading and (heading in raw or raw in heading):
            return idx
    return -1


async def extract_document(
    skill_md_text: str, skill_name: str, references: list[ScriptHint], llm_call: LLMCall
) -> ExtractionResult:
    """Extract one document chunk, inject placeholders, and validate graph endpoints."""
    system = system_prompt()
    user = user_prompt(skill_md_text, references)
    try:
        raw_text = await llm_call(system, user)
    except Exception as e:
        logger.error("Graph LLM call failed: %r", e)
        return ExtractionResult(
            skill_name=skill_name, nodes=[], edges=[], llm_error=repr(e)
        )
    try:
        cleaned = _strip_markdown_fences(raw_text)
        raw_json = json.loads(cleaned)
    except json.JSONDecodeError as e:
        logger.error("Graph LLM returned invalid JSON: %s", e)
        return ExtractionResult(
            skill_name=skill_name, nodes=[], edges=[], llm_error=f"invalid JSON: {e}"
        )
    if not isinstance(raw_json, dict) or not all(
        isinstance(raw_json.get(key), list) for key in ("entities", "relationships")
    ):
        return ExtractionResult(
            skill_name=skill_name,
            nodes=[],
            edges=[],
            llm_error="response requires entities and relationships lists",
        )
    try:
        validated = validate_extraction(raw_json, skill_name=skill_name)
    except Exception as e:
        logger.error("Graph validate_extraction failed: %r", e)
        return ExtractionResult(
            skill_name=skill_name,
            nodes=[],
            edges=[],
            llm_error=f"validate_extraction failed: {e}",
        )
    raw_llm_edges = raw_json.get("relationships", [])
    new_nodes, new_edges, audits = inject_placeholders(
        validated.nodes, raw_llm_edges, references, skill_name
    )
    section_index = build_section_index(skill_md_text)
    path_to_section_idx: dict[str, int] = {}
    for ref in references:
        p = _normalize_script_path(ref.script_path)
        if p in path_to_section_idx:
            continue
        idx = _resolve_section_index(ref.skill_md_section, section_index)
        if idx >= 0:
            path_to_section_idx[p] = idx
    for n in new_nodes:
        if not getattr(n.meta, "is_placeholder", False):
            continue
        rp = getattr(n.meta, "referenced_script_path", None) or n.name
        rp = _normalize_script_path(rp)
        n.meta.section_index = path_to_section_idx.get(rp)
    from .schemas import _TOOL_SOURCE_NAMES, GLOBAL_FILE_PATHS, EdgeRow, EdgeType

    valid_node_names = {n.name for n in new_nodes}
    valid_endpoints = valid_node_names | _TOOL_SOURCE_NAMES | set(GLOBAL_FILE_PATHS)
    final_edges: list[dict] = []
    final_dropped_edges: list[dict] = []
    for e in new_edges:
        try:
            edge = EdgeRow.model_validate(e)
            if edge.source not in valid_endpoints:
                raise ValueError(f"edge source {edge.source!r} not in nodes/tools")
            if edge.target not in valid_endpoints:
                raise ValueError(f"edge target {edge.target!r} not in nodes/tools")
            if edge.source_layer == SourceLayer.DECLARATION and edge.order is None:
                idx = _resolve_section_index(edge.source_section, section_index)
                if idx >= 0:
                    edge.order = idx * 1000
            final_edges.append(edge.model_dump())
        except Exception as ex:
            final_dropped_edges.append(
                {
                    "source": str(e.get("source", "")),
                    "target": str(e.get("target", "")),
                    "reason": str(ex),
                }
            )
    return ExtractionResult(
        skill_name=skill_name,
        nodes=new_nodes,
        edges=final_edges,
        dropped_entities=validated.dropped_entities,
        dropped_edges=final_dropped_edges,
        placeholder_audits=audits,
    )


def save_graph(result: ExtractionResult, output_path: Path) -> None:
    """Serialize the validated graph and its audit metadata."""
    from .schemas import compute_canonical_id, NodeType

    for n in result.nodes:
        if not n.canonical_id:
            n.canonical_id = compute_canonical_id(
                result.skill_name, n.type, n.properties
            )
    kg = {
        "skill_name": result.skill_name,
        "nodes": [
            {
                "name": n.name,
                "type": n.type.value,
                "properties": n.properties,
                "constraints": [c.model_dump() for c in n.constraints],
                "canonical_id": n.canonical_id,
                "meta": n.meta.model_dump(by_alias=True, exclude_none=True),
            }
            for n in result.nodes
        ],
        "edges": result.edges,
        "_meta": {
            "placeholder_count": sum(
                (1 for n in result.nodes if getattr(n.meta, "is_placeholder", False))
            ),
            "llm_error": result.llm_error,
            "placeholder_audits": [
                {
                    "script_path": a.script_path,
                    "placeholder_id": a.placeholder_id,
                    "decision_source": a.decision_source,
                    "script_reference_index": a.script_reference_index,
                }
                for a in result.placeholder_audits
            ],
        },
        "dropped_entities": result.dropped_entities,
        "dropped_edges": result.dropped_edges,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(kg, ensure_ascii=False, indent=2), encoding="utf-8"
    )
