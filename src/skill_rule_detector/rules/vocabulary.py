BASE_PREDICATES: tuple[str, ...] = (
    "READ",
    "WRITE",
    "EXEC",
    "FETCH",
    "SEND",
    "SEARCH",
    "ASK",
    "SPAWN",
)
_AGENT_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "Read",
        "Write",
        "Edit",
        "NotebookEdit",
        "Grep",
        "Glob",
        "LSP",
        "Bash",
        "PowerShell",
        "Monitor",
        "WebFetch",
        "WebSearch",
        "AskUserQuestion",
        "Agent",
        "Skill",
        "TaskCreate",
        "SendMessage",
        "User",
        "user",
        "agent",
        "System",
        "system",
    }
)
LOCAL_THRESHOLDS = (0.3, 0.5, 0.7, 0.9)
LOCAL_PREDICATES = tuple(
    f"ML_{kind}_LOCAL_GE_{int(t*100)}"
    for kind in ("EDGE", "NODE")
    for t in LOCAL_THRESHOLDS
)
PREDICATES = BASE_PREDICATES + LOCAL_PREDICATES
