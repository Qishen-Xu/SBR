# Placeholder Node Instructions (Step 2)

This block is appended to the Step 2 system prompt. It teaches the LLM
how to emit **placeholder FILE nodes** for scripts mentioned in SKILL.md
so that Step 3-4 can later replace them with the actual sub-graph.

---

## When you encounter a script mention

If SKILL.md text contains a path like `scripts/.../foo.py` (or
references a script in any other runnable form, e.g. `python scripts/x.py`,
`bash scripts/x.sh`, `node scripts/x.js`), you MUST:

1. **Emit a FILE entity** with:
   - `name`: the **exact script path** as written in the text
     (e.g. `"scripts/office/soffice.py"`, **NOT** `"soffice.py"` and
     **NOT** `"PLACEHOLDER_1"`)
   - `type`: `"FILE"`
   - `properties`:
     - `path`: same as `name` (e.g. `"scripts/office/soffice.py"`)
     - `extension`: extracted from the path (e.g. `"py"`, `"sh"`, `"js"`)
     - `permission`: `"execute"` — placeholder scripts are always invoke-only
   - `_meta.is_placeholder`: `true`
   - `_meta.placeholder_id`: a unique string like `"PLACEHOLDER_1"`
     (increment for each placeholder you emit)
   - `_meta.referenced_script_path`: same as `properties.path`
   - `_meta.script_reference_index`: the 0-based index of this script in
     the **Provided Script Hints** list at the bottom of the user prompt
   - `_meta.placeholder_decision_source`: `"llm"`
2. **Emit edges from the calling tool TO this placeholder**:
   - `source`: `"Bash"` (or `"PowerShell"` if the SKILL.md clearly shows
     PowerShell usage)
   - `target`: the **placeholder_id** you assigned (e.g. `"PLACEHOLDER_1"`)
   - `edge_type`: `"EXEC"`
   - `tool_used`: same as `source`
   - `source_layer`: `"declaration"`
   - `source_section`: the `##` heading under which the mention appears
3. **Optional but encouraged**: if SKILL.md describes what the script does
   in terms of OTHER entities (e.g. "uses pandoc", "calls the API at
   https://..."), emit those entities with edges FROM the placeholder_id
   TO them. These edges will be **preserved** by Step 3-4 stitching.

## Do NOT

- Do not invent scripts that aren't in the text.
- Do not re-emit a placeholder for a path you've already covered.
- Do not set `permission` to `"read"` or `"write"` for a script
  invocation — it's always `"execute"`.
- Do not include the script CONTENTS in any field; this is a
  declaration-layer extraction, not a code analysis. Step 3 reads the code.

## Reference example

**SKILL.md text (truncated)**:
```
## Converting .doc to .docx
Legacy `.doc` files must be converted before editing:
```bash
python scripts/office/soffice.py --headless --convert-to docx document.doc
```
This invokes pandoc under the hood.
```

**Corresponding entity/edge in your output**:
```json
{
  "entities": [
    {
      "name": "scripts/office/soffice.py",
      "type": "FILE",
      "properties": {
        "path": "scripts/office/soffice.py",
        "extension": "py",
        "permission": "execute"
      },
      "_meta": {
        "is_placeholder": true,
        "placeholder_id": "PLACEHOLDER_1",
        "referenced_script_path": "scripts/office/soffice.py",
        "script_reference_index": 0,
        "placeholder_decision_source": "llm"
      }
    },
    {
      "name": "pandoc",
      "type": "PACKAGE",
      "properties": {"name": "pandoc", "registry": "unknown", "executable": true}
    }
  ],
  "relationships": [
    {
      "source": "Bash",
      "target": "PLACEHOLDER_1",
      "edge_type": "EXEC",
      "tool_used": "Bash",
      "description": "convert .doc to .docx via soffice",
      "source_layer": "declaration",
      "source_section": "Converting .doc to .docx"
    },
    {
      "source": "PLACEHOLDER_1",
      "target": "pandoc",
      "edge_type": "EXEC",
      "tool_used": "Bash",
      "description": "soffice internally calls pandoc",
      "source_layer": "declaration",
      "source_section": "Converting .doc to .docx"
    }
  ]
}
```

Note that the `PLACEHOLDER_1` → `pandoc` edge will be retained by Step 3-4
and re-pointed to the soffice sub-graph's entry node.
