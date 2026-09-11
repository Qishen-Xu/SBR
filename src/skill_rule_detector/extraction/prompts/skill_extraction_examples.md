# Skill Extraction Examples (1-shot)

This document is injected into the LLM system prompt via the `{examples}`
placeholder. It provides ONE complete worked example: a natural-language skill
description → the expected JSON output. The model uses this as a template to
calibrate its output format.

The example is wrapped in a JSON code fence because we are in JSON output mode.
The angle-bracket tokens in the example are PLACEHOLDERS, not real values — the
LLM must replace them with values derived from the input text.

This example demonstrates ALL the new features added after the first draft:
- NETWORK.protocol as a free string (Q10)
- PACKAGE.executable flag (B2)
- EXEC to-target decision tree (Q14): script → FILE; program → PACKAGE; inline → /bin/bash
- source / source_section fields (Q12 + Q6)
- USER.user_question field for stable canonical_id

---

## Example input (do NOT include in real prompts; shown here for context only)

> **Skill: deploy-to-staging**
>
> This skill deploys the current branch to the staging environment.
>
> ## Steps
>
> 1. Read `deploy.yaml` from the project root to load the deployment
>    configuration.
> 2. Run `python deploy.py --env=staging` via Bash to deploy.
> 3. The deploy script calls `curl https://api.internal/health` to verify the
>    service is up.
> 4. The deploy script reads `DEPLOY_TOKEN` from the environment (read-write
>    access for deployments).
> 5. The deploy script installs `kubectl` via `brew install kubectl` if missing.
> 6. The deploy script asks the user "Continue with deployment? (y/n)".
> 7. After deployment, the script logs "Deployment complete" to notify the user.
>
> ## Required packages
>
> - `requests` (installed via pip)
> - `pyyaml` (installed via pip)
> - `curl` (assumed to be on PATH)
> - `kubectl` (installed via brew if missing)

---

## Expected JSON output

```json
{
  "entities": [
    {
      "name": "deploy.yaml",
      "type": "FILE",
      "properties": {
        "path": "deploy.yaml",
        "extension": "yaml",
        "permission": "read"
      }
    },
    {
      "name": "deploy.py",
      "type": "FILE",
      "properties": {
        "path": "deploy.py",
        "extension": "py",
        "permission": "execute"
      }
    },
    {
      "name": "api.internal",
      "type": "NETWORK",
      "properties": {
        "domain": "api.internal",
        "protocol": "https"
      },
      "_meta": {
        "inferred_fields": ["protocol"],
        "confidence": 0.85
      }
    },
    {
      "name": "DEPLOY_TOKEN",
      "type": "CREDENTIAL",
      "properties": {
        "name": "DEPLOY_TOKEN",
        "type": "token",
        "scope": "read_write"
      }
    },
    {
      "name": "requests",
      "type": "PACKAGE",
      "properties": {
        "name": "requests",
        "registry": "pip",
        "executable": false
      }
    },
    {
      "name": "pyyaml",
      "type": "PACKAGE",
      "properties": {
        "name": "pyyaml",
        "registry": "pip",
        "executable": false
      }
    },
    {
      "name": "curl",
      "type": "PACKAGE",
      "properties": {
        "name": "curl",
        "registry": "unknown",
        "executable": true
      }
    },
    {
      "name": "kubectl",
      "type": "PACKAGE",
      "properties": {
        "name": "kubectl",
        "registry": "brew",
        "executable": true
      }
    },
    {
      "name": "brew",
      "type": "PACKAGE",
      "properties": {
        "name": "brew",
        "registry": "unknown",
        "executable": true
      }
    },
    {
      "name": "Confirm: continue with deployment?",
      "type": "USER",
      "properties": {
        "interaction_type": "confirm",
        "user_question": "Continue with deployment? (y/n)"
      }
    },
    {
      "name": "Notify: deployment complete",
      "type": "USER",
      "properties": {
        "interaction_type": "notify",
        "user_question": "Deployment complete"
      }
    }
  ],
  "relationships": [
    {
      "source": "Read",
      "target": "deploy.yaml",
      "edge_type": "READ",
      "tool_used": "Read",
      "keywords": "load-config",
      "description": "Reads the deployment configuration file",
      "source_layer": "declaration",
      "source_section": "Step 1"
    },
    {
      "source": "Bash",
      "target": "deploy.py",
      "edge_type": "EXEC",
      "tool_used": "Bash",
      "keywords": "run-script",
      "description": "Runs the deploy script with --env=staging",
      "source_layer": "declaration",
      "source_section": "Step 2"
    },
    {
      "source": "Bash",
      "target": "curl",
      "edge_type": "EXEC",
      "tool_used": "Bash",
      "keywords": "health-check",
      "description": "Invokes curl to fetch the health endpoint",
      "source_layer": "declaration",
      "source_section": "Step 3"
    },
    {
      "source": "Bash",
      "target": "kubectl",
      "edge_type": "EXEC",
      "tool_used": "Bash",
      "keywords": "install",
      "description": "Installs kubectl via brew if missing",
      "source_layer": "declaration",
      "source_section": "Step 5"
    },
    {
      "source": "Bash",
      "target": "brew",
      "edge_type": "EXEC",
      "tool_used": "Bash",
      "keywords": "install",
      "description": "Calls brew to install kubectl",
      "source_layer": "declaration",
      "source_section": "Step 5"
    },
    {
      "source": "AskUserQuestion",
      "target": "Confirm: continue with deployment?",
      "edge_type": "ASK",
      "tool_used": "AskUserQuestion",
      "keywords": "user-confirmation",
      "description": "Asks the user to confirm the deployment",
      "source_layer": "declaration",
      "source_section": "Step 6"
    },
    {
      "source": "deploy.py",
      "target": "DEPLOY_TOKEN",
      "edge_type": "READ",
      "tool_used": "Read",
      "keywords": "auth",
      "description": "Reads the DEPLOY_TOKEN env var for authentication",
      "source_layer": "declaration",
      "source_section": "Step 4"
    },
    {
      "source": "deploy.py",
      "target": "requests",
      "edge_type": "READ",
      "tool_used": "Read",
      "keywords": "import",
      "description": "Imports the requests library (used by the script)",
      "source_layer": "declaration",
      "source_section": "Required packages"
    },
    {
      "source": "deploy.py",
      "target": "pyyaml",
      "edge_type": "READ",
      "tool_used": "Read",
      "keywords": "import",
      "description": "Imports the pyyaml library (used to parse deploy.yaml)",
      "source_layer": "declaration",
      "source_section": "Required packages"
    },
    {
      "source": "/bin/bash",
      "target": "Notify: deployment complete",
      "edge_type": "ASK",
      "tool_used": "AskUserQuestion",
      "keywords": "notify",
      "description": "Displays completion message to the user (inline echo via Bash)",
      "source_layer": "declaration",
      "source_section": "Step 7"
    }
  ],
  "_meta": {
    "overall_confidence": 0.9,
    "inferred_fields": ["api.internal.protocol"],
    "notes": "Protocol for api.internal inferred as https (no scheme shown). Multiple PACKAGE nodes distinguish libraries (executable=false) from invoked programs (executable=true). The /bin/bash source for the notify edge demonstrates the Q14 inline-command handling."
  }
}
```

---

## Walk-through of the design decisions in this example

### 1. Why `deploy.py` has `permission: "execute"` not `"read"`
The script's primary role is being RUN via `python deploy.py`. Choosing
`execute` encodes this. If we had chosen `read`, downstream queries like
"show me all files this skill modifies" would incorrectly include `deploy.py`.

### 2. Why `api.internal` is the only NETWORK node (not the full URL)
The `domain` field is the host, not the full URL. The path component
(`/health`) is captured in the edge's `description` / `keywords` instead, so
the graph stays at the host level (a single NETWORK node can have many
incoming FETCH / EXEC edges).

### 3. Why `api.internal.protocol` is in `_meta.inferred_fields`
The input never said "https" — the skill just "fetches the health endpoint".
A reasonable default, but a security audit needs to know this is not hard
evidence. The `_meta.inferred_fields` array is the carrier for this signal.

### 4. Why there are two PACKAGE entries for `curl` and `kubectl` (executable=true)
vs `requests` and `pyyaml` (executable=false)
The skill INSTALLS `requests` and `pyyaml` (libraries) but INVOKES `curl` and
`kubectl` (binaries). The `executable` flag (B2) lets M5 filter for
"executable program with network access" — a critical security signal that
is fundamentally different from "library with import statement".

### 5. Why the `Bash → curl` edge is separate from `curl → api.internal`
The Q14 EXEC target decision tree says: when a Bash command is a program call
(`curl URL`), the EXEC target is the PROGRAM (`PACKAGE(curl)`), not the URL's
host. The network access is captured implicitly via `curl`'s identity as a
network-capable executable. If we wanted a separate FETCH edge for the URL
itself, that would be a different convention (not currently enforced).

### 6. Why the `Bash → brew` edge is its own row, separate from `Bash → kubectl`
`brew install kubectl` involves TWO program calls: `brew` (the package manager)
and `kubectl` (the package being installed). Both are real PACKAGE nodes.
This makes the "package manager → package" relationship visible in the graph.

### 7. Why `Confirm: continue with deployment?` is a USER entity, not a string
By making the question text the USER entity's name, the ASK edge can
connect the tool (`AskUserQuestion`) to the human-decision point as a
first-class node. This lets downstream queries do things like "show me all
USER entities that require user confirmation across all skills" — a
graph query that would be impossible if the question text were buried in the
edge description.

### 8. Why the notify edge has `source: "/bin/bash"` and `target: Notify: deployment complete`
The skill's "log a message" step is an inline `echo` (or similar) via Bash
— not a real `AskUserQuestion` call. Per Q14 inline-command handling, the
EXEC source becomes the synthetic `/bin/bash` node (B1, shared across all
skills). The `tool_used` is still `AskUserQuestion` because the edge type is
ASK, but the ACTOR is `/bin/bash` running the echo. This is the cleanest
encoding of "user gets a notification via inline shell command".

(In a more refined schema, we could split `ASK` into `ASK` (real interaction)
and `NOTIFY` (display-only), but for now the edge type captures the intent
and the source captures the execution mechanism.)

### 9. Why the `deploy.py → DEPLOY_TOKEN` edge uses `tool_used: "Read"`
This is a soft mapping — the script "reads" the env var. Other reasonable
choices are `EXEC` (the script runs and the read happens as a side effect)
or a hypothetical `ENV` edge type we don't have. We pick `Read` because the
enumeration is conservative and the actual action is "read the value".
If you find the model waffles on this kind of edge, add a clearer rule to
`edge_types_guidance.md`.

### 10. Why `deploy.py → requests` and `deploy.py → pyyaml` are READ edges
Importing a library is functionally "reading" the installed package
metadata. The actor is the script (which does the import), the object is
the library package. Encoding it as READ makes the dependency graph
queryable ("which packages does this skill depend on?") without inventing
a new edge type.

### 11. Why every edge has `source: "declaration"` and `source_section: "..."`
The Q12 / Q6 fields preserve provenance. Downstream M4 (pattern mining) and
M5 (anomaly detection) can filter by source — e.g. "show me all EXEC edges
in `scripts/*.sh` (implementation) that touch NETWORK nodes" surfaces a
different signal than declaration-level EXEC edges.
