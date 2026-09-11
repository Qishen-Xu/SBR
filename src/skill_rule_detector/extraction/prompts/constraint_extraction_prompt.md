# Constraint Extraction Instructions (Step 2)

This block is appended to the Step 2 system prompt. It teaches the LLM to attach
**free-text constraints directly onto nodes** as natural-language descriptions
lifted from the SKILL.md text.

> **2026-08 schema change.** The old design used a top-level `constraints: []`
> array of typed records (`MAX_RETRIES` / `DOMAIN_WHITELIST` / …) that M2 then
> bound to specific node types by rule. That is **gone**. Constraints are now a
> field **on every node**: `constraints: [{ "text": ..., "source_section": ... }]`.
> Any node type (FILE / NETWORK / CREDENTIAL / PACKAGE / USER) may carry zero or
> more constraints. There is no top-level `constraints` key anymore.

---

## What is a constraint?

A "constraint" is any sentence or clause in the SKILL.md that **binds or limits**
how a node is allowed to operate — what it may touch, how many times, under what
condition, what it must never do. Examples of things that ARE constraints:

- "only call `api.openai.com` and `api.anthropic.com`"        → NETWORK node
- "retry up to 3 times with exponential backoff"               → NETWORK / PACKAGE node
- "never `rm -rf /`"                                          → FILE (script) node
- "the API key is read-only; do not use it for writes"        → CREDENTIAL node
- "output filenames must match `^[a-z]+\.docx$`"              → FILE node
- "must run in `--headless` mode to avoid blocking the shell" → FILE (script) node

A constraint is **attached to the node it governs**, in plain text copied or
lightly paraphrased from the SKILL.md. We keep the natural-language phrasing on
purpose — downstream analysis works on the text, not on an enum.

If a node has no governing rule in the text, give it `"constraints": []`.
**Do NOT fabricate constraints.** An empty list is the correct output when the
SKILL.md says nothing binding about that node.

---

## Where to put constraints

Constraints live **inside each entity**, as a `constraints` array:

```json
{
  "entities": [
    {
      "name": "api.openai.com",
      "type": "NETWORK",
      "properties": { "domain": "api.openai.com", "protocol": "https" },
      "constraints": [
        {
          "text": "Only api.openai.com and api.anthropic.com may be called; all other domains are blocked.",
          "source_section": "## Calling the model"
        }
      ]
    },
    {
      "name": "scripts/office/soffice.py",
      "type": "FILE",
      "properties": { "path": "scripts/office/soffice.py", "extension": "py", "permission": "execute" },
      "constraints": [
        {
          "text": "Must run with --headless to avoid blocking the terminal.",
          "source_section": "## Converting .doc to .docx"
        }
      ]
    },
    {
      "name": "scripts/cleanup.sh",
      "type": "FILE",
      "properties": { "path": "scripts/cleanup.sh", "extension": "sh", "permission": "execute" },
      "constraints": [
        {
          "text": "Recursive delete (rm -rf /) is explicitly prohibited.",
          "source_section": "## Cleanup"
        }
      ]
    }
  ],
  "relationships": [...]
}
```

There is **no** top-level `constraints` key. Do not emit one.

---

## Field rules

Each constraint object has exactly two fields:

- **`text`** (required, non-empty): the constraint as natural language. Copy the
  relevant sentence/clause from the SKILL.md, lightly normalized — keep the
  concrete bound (the number, the domain, the flag, the regex) in the text.
  Good: `"retry up to 3 times with backoff"`. Bad: `"has a retry rule"`.
- **`source_section`** (recommended): the `##` section heading of the SKILL.md
  this text came from (e.g. `"## Cleanup"`). This is provenance for downstream
  reports — copy the heading the text actually sits under. If you cannot tell,
  use an empty string `""`.

Every entity must have a `constraints` key (default `[]`). A node with no
binding rule simply has `"constraints": []`.

---

## Extraction heuristics

1. **Constraints come from the SKILL.md text.** Read the prose; when a sentence
   binds or limits a node you have already extracted, attach it to that node.
2. **Attach to the node it governs.** A rule about a network endpoint goes on the
   NETWORK node; a rule about a script flag goes on that FILE node; a rule about
   an API key goes on the CREDENTIAL node. If one rule governs several nodes,
   attach it to each of them.
3. **Keep the concrete value in the text.** The number `3`, the domain
   `api.openai.com`, the flag `--headless`, the regex `^\d{4}$` — keep them
   verbatim in `text`. Downstream reads the text.
4. **One logical rule per constraint object.** "retry 3 times" and "only call
   api.x.com" are two rules → two objects (possibly on the same node).
5. **When unsure, skip.** Fabricated constraints pollute downstream analysis. An
   empty `constraints: []` is always safer than a guessed one.

---

## Do NOT

- Do not emit a top-level `constraints` key — constraints live on entities.
- Do not invent constraints that are not in the SKILL.md text.
- Do not add a constraint for every sentence — only for binding/limiting rules.
- Do not put a `type` field on constraints. There is no enum anymore; `text` is
  the constraint.
- Do not leave the `constraints` key off an entity — always emit it (even if `[]`).
