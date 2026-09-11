"""Deterministic matching of existing script filenames in SKILL.md."""

from dataclasses import dataclass
from .loader import SkillCorpus


@dataclass
class ScriptReference:
    script_path: str
    skill_md_section: str
    skill_md_quote: str
    mentioned_tools: list[str]
    execution_context: str
    decision_source: str = "llm"
    decision_confidence: float = 1.0


def path_match_fallback(corpus: SkillCorpus) -> list[ScriptReference]:
    """
    Deterministic fallback when the LLM call fails. Grep SKILL.md for any
    `scripts/...` path that exists in the corpus's script_paths.
    """
    valid_paths = set(corpus.script_paths)
    section_index = {heading: body for heading, body in corpus.skill_md_sections}
    refs: list[ScriptReference] = []
    for path in corpus.script_paths:
        if path not in corpus.skill_md_text:
            continue
        section_name = ""
        for heading, body in section_index.items():
            if path in body:
                section_name = heading
                break
        idx = corpus.skill_md_text.find(path)
        start = max(0, idx - 60)
        end = min(len(corpus.skill_md_text), idx + len(path) + 60)
        quote = corpus.skill_md_text[start:end].strip()
        if len(quote) > 200:
            quote = quote[:197] + "..."
        refs.append(
            ScriptReference(
                script_path=path,
                skill_md_section=section_name,
                skill_md_quote=quote,
                mentioned_tools=[],
                execution_context="(extracted by path_match fallback; LLM unavailable)",
                decision_source="path_match",
                decision_confidence=0.6,
            )
        )
    return refs
