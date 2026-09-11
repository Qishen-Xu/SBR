"""Read SKILL.md and enumerate referenced script filenames without execution."""

from __future__ import annotations
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class SkillCorpus:
    """Document text, sections, and script filenames."""

    skill_name: str
    skill_dir: Path
    skill_md_text: str
    skill_md_sections: list[tuple[str, str]]
    script_paths: list[str]


def is_skill_dir(path: Path) -> bool:
    """A skill directory must contain SKILL.md at its root."""
    return (path / "SKILL.md").is_file()


SCRIPT_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".py",
        ".sh",
        ".bash",
        ".zsh",
        ".ksh",
        ".js",
        ".ts",
        ".mjs",
        ".cjs",
        ".rb",
        ".pl",
        ".ps1",
        ".bat",
        ".cmd",
    }
)


def list_script_paths(skill_dir: Path) -> list[str]:
    """List runnable script filenames under scripts/ without reading their contents."""
    scripts_root = skill_dir / "scripts"
    if not scripts_root.is_dir():
        return []
    out: list[str] = []
    for p in scripts_root.rglob("*"):
        if not p.is_file():
            continue
        if p.suffix.lower() not in SCRIPT_EXTENSIONS:
            continue
        rel = p.relative_to(skill_dir).as_posix()
        out.append(rel)
    return sorted(out)


_SECTION_RE = re.compile("^(#{2,3})\\s+(.+?)\\s*$", re.MULTILINE)


def split_skill_md_sections(text: str) -> list[tuple[str, str]]:
    """
    Split SKILL.md by `##` / `###` headings. Returns (heading, body) pairs.

    The first chunk (before the first heading) is paired with heading="".
    The body of each section runs until the next heading.
    """
    matches = list(_SECTION_RE.finditer(text))
    if not matches:
        return [("", text.strip())]
    sections: list[tuple[str, str]] = []
    first_start = matches[0].start()
    if first_start > 0 and text[:first_start].strip():
        sections.append(("", text[:first_start].strip()))
    for i, m in enumerate(matches):
        heading = m.group(2).strip()
        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[body_start:body_end].strip()
        sections.append((heading, body))
    return sections


def load_skill_corpus(skill_dir: Path) -> SkillCorpus:
    """
    Load a skill directory into a SkillCorpus.

    Raises FileNotFoundError if SKILL.md is missing.
    """
    if not is_skill_dir(skill_dir):
        raise FileNotFoundError(
            f"{skill_dir} is not a skill directory (missing SKILL.md)"
        )
    skill_md_text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    sections = split_skill_md_sections(skill_md_text)
    scripts = list_script_paths(skill_dir)
    return SkillCorpus(
        skill_name=skill_dir.name,
        skill_dir=skill_dir,
        skill_md_text=skill_md_text,
        skill_md_sections=sections,
        script_paths=scripts,
    )
