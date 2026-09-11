# Edge Type Guidance (8 edge types)

This document is injected into the LLM system prompt via the `{edge_types_guidance}`
placeholder. It defines the 8 legal edge types and the `tool_used` enum mapping.

The LLM MUST classify every extracted relationship as exactly one of these 8 types.
If nothing fits, skip the relationship — do NOT invent a new type.

---

## CRITICAL: Edge direction is STRICTLY PRESERVED

Unlike generic knowledge graphs, skill edges ARE DIRECTED. The semantics are:

```
source_entity = ACTOR   (the tool or user that performs the action)
target_entity = OBJECT  (the file, package, host, credential, etc. being acted upon)
```

**DO NOT swap actor and object. DO NOT treat edges as undirected.** Swapping
source/target encodes the wrong causality and corrupts downstream query.

If the input does not specify which tool is responsible, use `"Unknown"` as the
source and add `"Unknown"` to the `keywords` list.

---

## Edge type → tool_used mapping

Every edge row MUST include a `tool_used` field whose value comes from the listed
enum for that edge type. If a real tool name does not appear in the enum, the edge
SHOULD be dropped rather than invented.

### READ — the skill reads / inspects / searches content
| Property    | Allowed `tool_used` values               |
|-------------|------------------------------------------|
| `edge_type` | `READ`                                   |
| `tool_used` | `Read`, `Grep`, `Glob`, `LSP`            |

Typical action: opening a file, searching for a pattern, listing matching files.

### WRITE — the skill modifies content
| Property    | Allowed `tool_used` values               |
|-------------|------------------------------------------|
| `edge_type` | `WRITE`                                  |
| `tool_used` | `Write`, `Edit`, `NotebookEdit`          |

Typical action: creating a file, editing an existing file, modifying a notebook cell.

### EXEC — the skill runs a command or process
| Property    | Allowed `tool_used` values               |
|-------------|------------------------------------------|
| `edge_type` | `EXEC`                                   |
| `tool_used` | `Bash`, `PowerShell`, `Monitor`          |

Typical action: running a shell command, executing a script, monitoring a process.

**`target` selection rule for EXEC (Q14 decision):** the target of an EXEC edge
is NOT a free choice. Use this decision tree:

```
Is the command a script-file invocation (e.g. `python xxx.py`, `bash xxx.sh`,
`node xxx.js`, `sh run.sh`)?
└─ YES → target = FILE(脚本路径, permission="execute")

Is the command a program-binary invocation (e.g. `curl URL`, `wget URL`,
`git clone ...`, `npm install X`, `pip install X`, `brew install X`,
`docker run ...`, `kubectl apply ...`)?
└─ YES → target = PACKAGE(program_name, executable=true, registry=unknown
                          unless the same skill installs it explicitly)

Otherwise (inline commands like `ls -la`, `echo foo`, `mkdir -p`, `cd /tmp`,
`rm xxx`, `cat xxx`, `cp x y`, `mv x y`, env-var reads like `echo $TOKEN`)?
└─ target = synthetic FILE("/bin/bash")  ← SHARED across all skills (B1)
   (do NOT create a new FILE node for /bin/bash — the canonical_id is
    "GLOBAL::FILE::/bin/bash" and M2 dedupes it)
```

**Synthetic global nodes (B1 whitelist):** the following hosts are
**predefined system nodes** — reference them by canonical name, do NOT
extract them as discovered FILE entities, and they will be auto-shared
across all skills:

| Path             | When to use                                          |
|------------------|------------------------------------------------------|
| `/bin/bash`      | Default target for any inline Bash command           |
| `/bin/sh`        | When the script explicitly invokes `/bin/sh`         |
| `/usr/bin/env`   | When a shebang like `#!/usr/bin/env python` is used  |
| `/usr/bin/bash`  | When the script explicitly invokes `/usr/bin/bash`  |

**`source_layer` field for EXEC edges:** when an EXEC edge is extracted from a
script in `scripts/*.sh` (not from SKILL.md's natural-language description),
set `source_layer: "implementation"`. Otherwise default to `"declaration"`.

### FETCH — the skill retrieves a remote resource
| Property    | Allowed `tool_used` values               |
|-------------|------------------------------------------|
| `edge_type` | `FETCH`                                  |
| `tool_used` | `WebFetch`                               |

Typical action: downloading a URL, fetching an API response, retrieving a webpage.

**FETCH is strictly INBOUND**: the remote resource flows INTO the skill
(`requests.get`, `curl URL`, `wget`, reading a response body). If the point of
the call is pushing local data OUT, use `SEND` instead.

### SEND — the skill sends / uploads data to a NETWORK target
| Property    | Allowed `tool_used` values               |
|-------------|------------------------------------------|
| `edge_type` | `SEND`                                   |
| `tool_used` | `WebFetch`, `Bash`, `PowerShell`         |

Typical action: HTTP `POST`/`PUT`/`PATCH` with a request body, file upload,
webhook delivery, SMTP email send, `socket.send`/`sendall`/`sendto`.

**SEND is strictly OUTBOUND**: `source` = the actor performing the send,
`target` = the NETWORK entity receiving the data. Decision rules:

- `requests.post/put/patch(...)`, `httpx.post(...)`, `fetch(url, {method:
  "POST"})`, `urllib.request.Request(url, data=...)`, `axios.post(...)`
  → `SEND` (`tool_used: "WebFetch"`).
- `smtplib.SMTP(...).sendmail(...)` / `send_message(...)`,
  `socket.send(...)` / `sendall(...)` / `sendto(...)`,
  `upload_file(...)` / `put_object(...)` → `SEND` (`tool_used: "WebFetch"`).
- Shell-invoked sends (`curl -d ...`, `curl -X POST`, `curl --upload-file`,
  `git push`, `scp`, `rsync ... remote:`, `docker push`)
  → `SEND` (`tool_used: "Bash"` / `"PowerShell"`).
- `requests.get(...)`, downloads, and response-driven reads stay `FETCH`.
  An explicit outbound primitive is `SEND` even when a response is also read.
- Do NOT invent new `tool_used` values for SEND: library egress maps to
  `WebFetch`, shell egress to `Bash`/`PowerShell`; record the real primitive
  (e.g. "smtplib.sendmail", "socket.sendall") in `description`.

### SEARCH — the skill queries a search engine
| Property    | Allowed `tool_used` values               |
|-------------|------------------------------------------|
| `edge_type` | `SEARCH`                                 |
| `tool_used` | `WebSearch`                              |

Typical action: searching the web, looking up documentation, finding references.

### ASK — the skill prompts the human for a decision
| Property    | Allowed `tool_used` values               |
|-------------|------------------------------------------|
| `edge_type` | `ASK`                                    |
| `tool_used` | `AskUserQuestion`                        |

Typical action: asking the user to confirm, select, or provide input.

**ASK edges connect `source = "AskUserQuestion"` (or `"User"`) to `target =`
the specific USER entity that represents the question being asked.**

### SPAWN — the skill launches a sub-task or sub-agent
| Property    | Allowed `tool_used` values               |
|-------------|------------------------------------------|
| `edge_type` | `SPAWN`                                  |
| `tool_used` | `Agent`, `Skill`, `TaskCreate`, `SendMessage` |

Typical action: delegating work to a sub-agent, invoking another skill, creating
a task, sending a message to another agent.

---

## Edge row schema (JSON mode)

```json
{
  "source": "<actor entity name>",
  "target": "<object entity name>",
  "edge_type": "<one of: READ, WRITE, EXEC, FETCH, SEND, SEARCH, ASK, SPAWN>",
  "tool_used": "<one of the enum values listed for that edge_type>",
  "keywords": "<comma-separated high-level tags, optional>",
  "description": "<one sentence explaining the action>",
  "source_layer": "<declaration | implementation>",
  "source_section": "<path within source file, e.g. 'Step 3' or 'scripts/backup.sh:line 42'>"
}
```

**`source_layer` field (Q12):** which layer did the edge come from?
- `"declaration"` — the SKILL.md natural-language description (default)
- `"implementation"` — a `scripts/*.sh` or `scripts/*.py` file

**`source_section` field (Q6):** fine-grained location within the source.
- For declaration: e.g. `"Steps > Step 3"`, `"Required packages"`, `"## Auth"`
- For implementation: e.g. `"scripts/backup.sh:line 42"`, `"scripts/upload.py:check_token"`

**Hard validation rules (any violation → drop the edge):**
1. `edge_type` MUST be one of the 8 listed values
2. `tool_used` MUST be in the allowed enum for the declared `edge_type`
3. `source != target` (no self-loops unless explicitly modeling recursion)
4. Both `source` and `target` MUST correspond to entities already extracted
   in the same response (or be one of the fixed `tool` names like `Read`,
   `Bash`, etc., which are valid sources without needing a separate entity)
5. `source_layer` MUST be either `"declaration"` or `"implementation"` (default
   to `"declaration"` if not stated)

**Soft rules (warning, keep the edge):**
- `keywords` empty → fine for very obvious actions
- `description` empty → drop (LightRAG-style hard rejection — see
  `operate.py:639-643` in the source project)

---

## `tool_used` is a TYPED ENUM, not a free-form string

The current project's edge prompt uses `relationship_keywords` as a free-form
comma-separated string. We are DELIBERATELY NOT doing that here because tool
names need to be consistent for downstream graph queries (e.g. "show me all
edges where `tool_used = 'Bash'`").

If the input uses a synonym that is NOT in the enum (e.g. "shell" instead of
"Bash"), do one of:
- Map it: `"shell" → "Bash"` (mention the mapping in the description)
- Drop the edge: if the mapping is genuinely uncertain
- DO NOT introduce a new tool_used value

---

## `tool` source entity naming convention

When the source of an edge is a tool (not a user/agent), the source name MUST
match the tool's canonical name from the enum. Examples:
- `source: "Read"` (not `"read_file"`, `"read()"`, or `"the Read tool"`)
- `source: "Bash"` (not `"bash"`, `"shell"`, or `"the terminal"`)
- `source: "Agent"` (not `"subagent"`, `"sub-agent"`, or `"the spawned agent"`)

This is critical for edge merging across multiple extraction passes.
