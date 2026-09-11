# Node Type Guidance (5 node types)

This document is injected into the LLM system prompt via the `{node_types_guidance}`
placeholder. It defines the 5 legal node types and the per-type required properties.

The LLM MUST classify every extracted entity as exactly one of these 5 types.
If nothing fits, skip the entity — do NOT invent a new type.

---

## FILE

Represents any file path that the skill reads, writes, or executes.

**Required properties:**
| Property   | Type   | Allowed values / format                              |
|------------|--------|------------------------------------------------------|
| `path`     | string | Absolute or workspace-relative path, as it appears  |
| `extension`| string | File extension WITHOUT the leading dot, e.g. `py`    |
| `permission` | enum | one of: `read`, `write`, `read_write`, `execute`, `unknown` |

**Permission inference rule:**
- If the skill is described as "reading" / "loading" / "importing" the file → `read`
- If the skill is described as "writing" / "modifying" / "saving" the file → `write`
- If the skill does both (e.g. read-then-edit) → `read_write`
- If the skill "executes" / "runs" the file (e.g. `.sh`, `.py` via Bash) → `execute`
- If permission is genuinely ambiguous → set `permission: "unknown"`. Do NOT
  default to `read`, and do NOT drop the file entity: the fact that the skill
  touches the file is itself the signal we need.

**Examples:**
- `{"path": "src/main.py", "extension": "py", "permission": "read"}`
- `{"path": "/etc/hosts", "extension": "hosts", "permission": "read_write"}`

---

## NETWORK

Represents any external host / endpoint the skill contacts.

**Required properties:**
| Property   | Type   | Allowed values / format                              |
|------------|--------|------------------------------------------------------|
| `domain`   | string | Hostname (`api.example.com`) or `host:port` form    |
| `protocol` | string | **Free-form scheme**, e.g. `http`, `https`, `wss`, `ssh`, `git+ssh`, `ftp`, `tcp`, `udp`, ... |

**Protocol is a free string (Q10 decision, not an enum).** Skills reference
non-HTTP protocols too: `wss://` (WebSocket), `ssh://` (remote shell),
`git+ssh://` (Git over SSH), `smb://`, custom schemes, etc. Hard-coding an
enum would force-drop too many real-world edges.

**Protocol inference rule:**
- `https://...` in the input → `https` (hard evidence, no need to flag)
- `http://...` in the input → `http` (hard evidence)
- `wss://`, `ssh://`, `git+ssh://`, `ftp://`, etc. → use the scheme **literally**
  (hard evidence)
- No scheme shown but "fetch the API" / "calls the endpoint" → `https` is the
  SAFE default. Mark this in `_meta.inferred_fields` so downstream queries know
  it's not hard evidence.
- `tcp` / `udp` only when the skill is explicitly a low-level socket skill

**Examples:**
- `{"domain": "api.github.com", "protocol": "https"}`
- `{"domain": "internal.svc:5432", "protocol": "tcp"}` (low-level, hard evidence)
- `{"domain": "stream.example.com", "protocol": "wss"}` (WebSocket)
- `{"domain": "git.internal", "protocol": "git+ssh"}` (Git-over-SSH)

---

## CREDENTIAL

Represents any secret / token / password / env var the skill depends on.

**Required properties:**
| Property | Type   | Allowed values                                              |
|----------|--------|-------------------------------------------------------------|
| `name`   | string | The stable identifier: for `env_var` the literal env var name (e.g. `OPENAI_API_KEY`); for `api_key`/`token`/`password` an optional human-readable identifier (e.g. `github_token`). **Required for `env_var`; strongly recommended for the others.** |
| `type`   | enum   | one of: `token`, `api_key`, `password`, `env_var`           |
| `scope`  | enum   | one of: `read_only`, `read_write`, `unknown`                 |

**Critical rules (security-sensitive):**
- **ALWAYS extract the CREDENTIAL when the skill depends on a secret**, even if
  the document says nothing about its permission scope. Real skill docs almost
  never state a scope, and dropping those credentials erases the security
  signal entirely.
- **`scope` must NEVER be guessed.** Use `read_only` / `read_write` ONLY when the
  input explicitly states the permission level. Otherwise set
  `scope: "unknown"` — never invent a permission level. Recording "a credential
  exists and we do not know its scope" is correct and useful; silently omitting
  the credential is not.
- **`name` is REQUIRED for `env_var` credentials** — the `name` MUST be the literal
  env var name (e.g. `OPENAI_API_KEY`, `GH_TOKEN`, `BDPAN_BIN`). This is how
  downstream modules distinguish between different env vars in the same skill
  (e.g. `OPENAI_API_KEY` vs `ANTHROPIC_API_KEY` are DIFFERENT credentials even if
  both are `read_only`). If the document references an env var without naming it,
  prefer a non-`env_var` type (e.g. `api_key`) with a descriptive `name` rather
  than dropping the credential.
  For non-env-var types (`api_key`/`token`/`password`), `name` is optional but
  strongly recommended for disambiguation.
- `api_key` vs `token`: if the input distinguishes them (e.g. "API key" /
  "OAuth token" / "bearer token"), use that distinction. If ambiguous, prefer
  `token` (broader category).

**Examples:**
- `{"name": "github_token", "type": "api_key", "scope": "read_write"}` — "uses GitHub API key with repo access"
- `{"name": "OPENAI_API_KEY", "type": "env_var", "scope": "read_only"}` — "reads `OPENAI_API_KEY` from env"
- `{"name": "READ_ONLY_TOKEN", "type": "env_var", "scope": "read_only"}` — "reads `READ_ONLY_TOKEN` from env"
- `{"name": "AGENTMAIL_API_KEY", "type": "env_var", "scope": "unknown"}` — "Set environment variable: `AGENTMAIL_API_KEY=your_key_here`" (the doc names the
  env var but never states its permissions → extract it with `scope: unknown`;
  do NOT drop it)

---

## PACKAGE

Represents any external software package / library the skill installs or imports,
OR any **executable program** invoked via Bash/PowerShell.

**Required properties:**
| Property      | Type | Allowed values / format                              |
|---------------|------|------------------------------------------------------|
| `name`        | string | Package/program name as it appears (install name or binary name) |
| `registry`    | enum   | one of: `npm`, `pip`, `brew`, `apt`, `gem`, `unknown`|
| `executable`  | bool   | **B2: `true` if invoked via Bash/PowerShell (`curl`, `git`, `npm`, `pip`, `kubectl`, `docker`, ...). `false` if only imported/required (Python lib, JS module).** Default `false`. |

**Why `executable` matters:** Downstream M5 (anomaly detection) treats
"executable program with network access" as a critical security signal —
e.g. `curl` invoking an attacker-controlled URL is fundamentally different
from `requests.get()` in a library. The flag lets M5 filter by `executable=true`
when computing network-bound program counts.

**Disambiguation rules:**
- `name` should be the **install name or binary name** as it appears:
  - `pip install pillow` → `{"name": "pillow", "registry": "pip", "executable": false}`
    (PIL is the import name, not the install name)
  - `npm install @types/node` → `{"name": "@types/node", "registry": "npm", "executable": false}`
  - `brew install postgresql@15` → `{"name": "postgresql@15", "registry": "brew", "executable": false}`
  - `curl https://...` (no install) → `{"name": "curl", "registry": "unknown", "executable": true}`
  - `kubectl apply -f x.yaml` (no install) → `{"name": "kubectl", "registry": "unknown", "executable": true}`
- If the skill mentions `import` / `require` / `use` WITHOUT an install line
  AND WITHOUT a Bash invocation, the registry MUST be `unknown` and
  `executable=false` (we cannot infer it).
- Do NOT extract standard-library modules (`os`, `sys`, `pathlib`, `fs`) —
  they are not "packages" in this sense.

**Examples:**
- `{"name": "requests", "registry": "pip", "executable": false}` — Python library
- `{"name": "lodash", "registry": "npm", "executable": false}` — JS library
- `{"name": "ffmpeg", "registry": "apt", "executable": true}` — installed AND used as a binary
- `{"name": "curl", "registry": "unknown", "executable": true}` — invoked via Bash, not installed by this skill
- `{"name": "kubectl", "registry": "brew", "executable": true}` — installed via brew AND used as a binary

---

## USER

Represents a point where the skill pauses to interact with the human.

**Required properties:**
| Property            | Type | Allowed values                                  |
|---------------------|------|-------------------------------------------------|
| `interaction_type`  | enum | one of: `confirm`, `select`, `input`, `notify`  |
| `user_question`     | string | The literal question/prompt text (≤200 chars). Used as canonical_id seed. |

**When to extract:**
- `confirm` — the skill asks the user a yes/no or proceed/cancel question
- `select` — the skill presents options and asks the user to pick one
- `input` — the skill asks the user to type a value (text, password, etc.)
- `notify` — the skill displays a message to the user (no decision required)

**Naming convention for the entity name** (the `name` field):
Use a short label prefix that signals the interaction type + the question/prompt
text, ≤80 chars total, e.g.:
- `"Confirm: continue with deployment?"`
- `"Select: which environment to deploy to?"`
- `"Input: database password"`
- `"Notify: deployment complete"`

**`user_question` vs `name`:**
- `name` — short label for LLM readability and edge endpoints
- `user_question` — the raw question text, used as a stable seed for the
  canonical_id hash. If two USER nodes across skills have the same
  `user_question` text, M2 may merge them (decided downstream).

**Do NOT extract USER entities for purely internal prompts** (e.g. tool progress
messages that don't actually require a human decision) — those are `notify` ONLY
if the skill explicitly surfaces them to the user.

---

## Skipping rules (apply to ALL types)

Skip the entity entirely if:
1. Any required property is missing AND cannot be inferred with ≥medium confidence
2. The property value would be a guess with no supporting evidence in the input
3. The entity is not central to what the skill DOES (decorative mentions don't count)

When in doubt, omit. The cost of a missing entity is one missed query hit;
the cost of a wrong entity is a polluted KG.
