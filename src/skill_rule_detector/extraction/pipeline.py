"""Skill directory to a validated graph. Failed or empty extractions raise errors."""

import hashlib
import json
from pathlib import Path
from .loader import load_skill_corpus
from .chunking import _refs_from_corpus, chunk_skill_md, _merge_chunk_results
from .parser import extract_document, save_graph, system_prompt


async def extract_skill(skill_dir, llm_call, output_path, *, model_name="custom"):
    corpus = load_skill_corpus(Path(skill_dir))
    if not corpus.skill_md_text.strip():
        raise ValueError("SKILL.md is empty")
    references = _refs_from_corpus(corpus)
    chunks = chunk_skill_md(corpus.skill_md_text)
    results = []
    for chunk in chunks:
        result = await extract_document(chunk, corpus.skill_name, references, llm_call)
        if result.llm_error:
            raise RuntimeError(f"Graph extraction failed: {result.llm_error}")
        results.append(result)
    merged = _merge_chunk_results(results)
    if not merged.nodes and not merged.edges:
        raise ValueError("LLM returned an empty graph; detection was not attempted")
    output_path = Path(output_path)
    save_graph(merged, output_path)
    graph = json.loads(output_path.read_text(encoding="utf-8"))
    graph["_meta"]["extraction"] = dict(
        model=model_name,
        chunks=len(chunks),
        script_hints=len(references),
        input_sha256=hashlib.sha256(corpus.skill_md_text.encode()).hexdigest(),
        prompt_sha256=hashlib.sha256(system_prompt().encode()).hexdigest(),
        script_paths=corpus.script_paths,
        scope="SKILL.md plus script filename hints",
    )
    output_path.write_text(
        json.dumps(graph, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return graph
