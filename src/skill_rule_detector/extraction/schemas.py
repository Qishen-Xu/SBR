"""Entity and behavior graph schema and extraction validators."""

from __future__ import annotations
import logging
from enum import Enum
from typing import Any, Literal
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

logger = logging.getLogger(__name__)


class FilePermission(str, Enum):
    READ = "read"
    WRITE = "write"
    READ_WRITE = "read_write"
    EXECUTE = "execute"
    UNKNOWN = "unknown"


class CredentialType(str, Enum):
    TOKEN = "token"
    API_KEY = "api_key"
    PASSWORD = "password"
    ENV_VAR = "env_var"


class CredentialScope(str, Enum):
    READ_ONLY = "read_only"
    READ_WRITE = "read_write"
    UNKNOWN = "unknown"


class PackageRegistry(str, Enum):
    NPM = "npm"
    PIP = "pip"
    BREW = "brew"
    APT = "apt"
    GEM = "gem"
    UNKNOWN = "unknown"


class UserInteractionType(str, Enum):
    CONFIRM = "confirm"
    SELECT = "select"
    INPUT = "input"
    NOTIFY = "notify"


class NodeType(str, Enum):
    FILE = "FILE"
    NETWORK = "NETWORK"
    CREDENTIAL = "CREDENTIAL"
    PACKAGE = "PACKAGE"
    USER = "USER"


class SourceLayer(str, Enum):
    """Source provenance of a graph edge."""

    DECLARATION = "declaration"
    IMPLEMENTATION = "implementation"


class EdgeType(str, Enum):
    READ = "READ"
    WRITE = "WRITE"
    EXEC = "EXEC"
    FETCH = "FETCH"
    SEND = "SEND"
    SEARCH = "SEARCH"
    ASK = "ASK"
    SPAWN = "SPAWN"


EDGE_TOOL_USED: dict[EdgeType, frozenset[str]] = {
    EdgeType.READ: frozenset({"Read", "Grep", "Glob", "LSP"}),
    EdgeType.WRITE: frozenset({"Write", "Edit", "NotebookEdit"}),
    EdgeType.EXEC: frozenset({"Bash", "PowerShell", "Monitor"}),
    EdgeType.FETCH: frozenset({"WebFetch"}),
    EdgeType.SEND: frozenset({"WebFetch", "Bash", "PowerShell"}),
    EdgeType.SEARCH: frozenset({"WebSearch"}),
    EdgeType.ASK: frozenset({"AskUserQuestion"}),
    EdgeType.SPAWN: frozenset({"Agent", "Skill", "TaskCreate", "SendMessage"}),
}
PERMISSION_ALIASES: dict[str, str] = {
    "r": "read",
    "R": "read",
    "READ": "read",
    "Read": "read",
    "w": "write",
    "W": "write",
    "WRITE": "write",
    "Write": "write",
    "rw": "read_write",
    "RW": "read_write",
    "r/w": "read_write",
    "r+w": "read_write",
    "x": "execute",
    "exec": "execute",
    "EXEC": "execute",
    "run": "execute",
}
PROTOCOL_ALIASES: dict[str, str] = {
    "HTTP": "http",
    "http://": "http",
    "HTTPS": "https",
    "https://": "https",
    "ssl": "https",
    "tls": "https",
    "FTP": "ftp",
    "ftp://": "ftp",
    "TCP": "tcp",
    "UDP": "udp",
}
CREDENTIAL_TYPE_ALIASES: dict[str, str] = {
    "bearer": "token",
    "oauth": "token",
    "OAuth": "token",
    "API_KEY": "api_key",
    "ApiKey": "api_key",
    "apikey": "api_key",
    "TOKEN": "token",
    "Token": "token",
    "PASSWORD": "password",
    "Password": "password",
    "passwd": "password",
    "ENV": "env_var",
    "ENV_VAR": "env_var",
    "envvar": "env_var",
    "environment_variable": "env_var",
}
CREDENTIAL_SCOPE_ALIASES: dict[str, str] = {
    "ro": "read_only",
    "RO": "read_only",
    "READ_ONLY": "read_only",
    "ReadOnly": "read_only",
    "read-only": "read_only",
    "R/O": "read_only",
    "r/o": "read_only",
    "rw": "read_write",
    "RW": "read_write",
    "READ_WRITE": "read_write",
    "ReadWrite": "read_write",
    "read-write": "read_write",
    "r/w": "read_write",
    "R/W": "read_write",
}
REGISTRY_ALIASES: dict[str, str] = {
    "NPM": "npm",
    "yarn": "npm",
    "pnpm": "npm",
    "PIP": "pip",
    "pip3": "pip",
    "PyPI": "pip",
    "pypi": "pip",
    "BREW": "brew",
    "homebrew": "brew",
    "macports": "brew",
    "APT": "apt",
    "apt-get": "apt",
    "deb": "apt",
    "GEM": "gem",
    "rubygems": "gem",
    "bundler": "gem",
}
INTERACTION_ALIASES: dict[str, str] = {
    "y/n": "confirm",
    "yes_no": "confirm",
    "yes-no": "confirm",
    "CONFIRM": "confirm",
    "ack": "confirm",
    "acknowledge": "confirm",
    "SELECT": "select",
    "choose": "select",
    "pick": "select",
    "option": "select",
    "INPUT": "input",
    "type": "input",
    "enter": "input",
    "prompt": "input",
    "NOTIFY": "notify",
    "inform": "notify",
    "alert": "notify",
    "log": "notify",
}
EDGE_TYPE_ALIASES: dict[str, str] = {
    "read": "READ",
    "Read": "READ",
    "READ": "READ",
    "open": "READ",
    "load": "READ",
    "write": "WRITE",
    "Write": "WRITE",
    "WRITE": "WRITE",
    "save": "WRITE",
    "edit": "WRITE",
    "exec": "EXEC",
    "Exec": "EXEC",
    "EXEC": "EXEC",
    "run": "EXEC",
    "execute": "EXEC",
    "fetch": "FETCH",
    "Fetch": "FETCH",
    "FETCH": "FETCH",
    "http_get": "FETCH",
    "download": "FETCH",
    "send": "SEND",
    "Send": "SEND",
    "SEND": "SEND",
    "upload": "SEND",
    "Upload": "SEND",
    "post": "SEND",
    "POST": "SEND",
    "http_post": "SEND",
    "webhook": "SEND",
    "search": "SEARCH",
    "Search": "SEARCH",
    "SEARCH": "SEARCH",
    "web_search": "SEARCH",
    "ask": "ASK",
    "Ask": "ASK",
    "ASK": "ASK",
    "question": "ASK",
    "prompt_user": "ASK",
    "spawn": "SPAWN",
    "Spawn": "SPAWN",
    "SPAWN": "SPAWN",
    "delegate": "SPAWN",
    "invoke": "SPAWN",
    "subagent": "SPAWN",
}


class NodeMeta(BaseModel):
    """Entity provenance and optional script placeholder metadata."""

    inferred_fields: list[str] = Field(default_factory=list)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    source_chunk_id: str | None = None
    raw_output: dict[str, Any] | None = None
    is_placeholder: bool = False
    placeholder_id: str | None = None
    referenced_script_path: str | None = None
    script_reference_index: int | None = None
    placeholder_decision_source: Literal["llm", "auto"] | None = None
    entry_decision_source: Literal["llm", "rule:__main__", "rule:no_entry"] | None = (
        None
    )
    entry_decision_confidence: float | None = None
    no_entry_detected: bool | None = None
    fallback_reason: str | None = None
    section_index: int | None = None


class Constraint(BaseModel):
    """A free-text constraint attached to a graph node."""

    text: str = Field(min_length=1)
    source_section: str = ""


class NodeBase(BaseModel):
    """Common shape for all 5 node types."""

    name: str = Field(min_length=1, max_length=512)
    type: NodeType
    properties: dict[str, Any]
    canonical_id: str = ""
    constraints: list[Constraint] = Field(default_factory=list)
    meta: NodeMeta = Field(default_factory=NodeMeta, alias="_meta")
    model_config = {"populate_by_name": True}

    @field_validator("name")
    @classmethod
    def _strip_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("empty node name after strip")
        return v


class FileNode(NodeBase):
    type: Literal[NodeType.FILE] = NodeType.FILE

    @field_validator("properties")
    @classmethod
    def _check_file_props(cls, v: dict[str, Any]) -> dict[str, Any]:
        if "path" not in v:
            raise ValueError("FILE missing required properties: ['path']")
        if "extension" not in v or v.get("extension") is None:
            tail = str(v["path"]).rsplit("/", 1)[-1]
            v["extension"] = tail.rsplit(".", 1)[-1] if "." in tail else ""
        perm_raw = str(v.get("permission") or "unknown").strip() or "unknown"
        v["permission"] = PERMISSION_ALIASES.get(perm_raw, perm_raw)
        if v["permission"] not in {p.value for p in FilePermission}:
            raise ValueError(f"FILE.permission {v['permission']!r} not in enum")
        return v


class NetworkNode(NodeBase):
    type: Literal[NodeType.NETWORK] = NodeType.NETWORK

    @field_validator("properties")
    @classmethod
    def _check_net_props(cls, v: dict[str, Any]) -> dict[str, Any]:
        required = {"domain", "protocol"}
        missing = required - v.keys()
        if missing:
            raise ValueError(f"NETWORK missing required properties: {sorted(missing)}")
        proto_raw = str(v["protocol"])
        v["protocol"] = PROTOCOL_ALIASES.get(proto_raw, proto_raw)
        if not v["protocol"].strip():
            raise ValueError("NETWORK.protocol is empty after normalization")
        return v


class CredentialNode(NodeBase):
    type: Literal[NodeType.CREDENTIAL] = NodeType.CREDENTIAL

    @field_validator("properties")
    @classmethod
    def _check_cred_props(cls, v: dict[str, Any]) -> dict[str, Any]:
        if "type" not in v:
            raise ValueError("CREDENTIAL missing required properties: ['type']")
        type_raw = str(v["type"])
        v["type"] = CREDENTIAL_TYPE_ALIASES.get(type_raw, type_raw)
        if v["type"] not in {t.value for t in CredentialType}:
            raise ValueError(f"CREDENTIAL.type {v['type']!r} not in enum")
        scope_raw = str(v.get("scope") or "unknown").strip() or "unknown"
        v["scope"] = CREDENTIAL_SCOPE_ALIASES.get(scope_raw, scope_raw)
        if v["scope"] not in {s.value for s in CredentialScope}:
            raise ValueError(f"CREDENTIAL.scope {v['scope']!r} not in enum")
        return v


class PackageNode(NodeBase):
    type: Literal[NodeType.PACKAGE] = NodeType.PACKAGE

    @field_validator("properties")
    @classmethod
    def _check_pkg_props(cls, v: dict[str, Any]) -> dict[str, Any]:
        required = {"name", "registry"}
        missing = required - v.keys()
        if missing:
            raise ValueError(f"PACKAGE missing required properties: {sorted(missing)}")
        reg_raw = str(v["registry"])
        v["registry"] = REGISTRY_ALIASES.get(reg_raw, reg_raw)
        if v["registry"] not in {r.value for r in PackageRegistry}:
            raise ValueError(f"PACKAGE.registry {v['registry']!r} not in enum")
        if "executable" not in v:
            v["executable"] = False
        else:
            v["executable"] = bool(v["executable"])
        return v


class UserNode(NodeBase):
    type: Literal[NodeType.USER] = NodeType.USER

    @field_validator("properties")
    @classmethod
    def _check_user_props(cls, v: dict[str, Any]) -> dict[str, Any]:
        required = {"interaction_type"}
        missing = required - v.keys()
        if missing:
            raise ValueError(f"USER missing required properties: {sorted(missing)}")
        it_raw = str(v["interaction_type"])
        v["interaction_type"] = INTERACTION_ALIASES.get(it_raw, it_raw)
        if v["interaction_type"] not in {i.value for i in UserInteractionType}:
            raise ValueError(
                f"USER.interaction_type {v['interaction_type']!r} not in enum"
            )
        return v


NODE_MODELS: dict[NodeType, type[NodeBase]] = {
    NodeType.FILE: FileNode,
    NodeType.NETWORK: NetworkNode,
    NodeType.CREDENTIAL: CredentialNode,
    NodeType.PACKAGE: PackageNode,
    NodeType.USER: UserNode,
}


class EdgeRow(BaseModel):
    """One relationship row from the LLM output."""

    source: str = Field(min_length=1)
    target: str = Field(min_length=1)
    edge_type: str
    tool_used: str
    keywords: str = ""
    description: str = ""
    source_layer: SourceLayer = SourceLayer.DECLARATION
    source_section: str | None = None
    order: int | None = None

    @field_validator("source", "target")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("empty source/target after strip")
        return v

    @field_validator("edge_type")
    @classmethod
    def _normalize_edge_type(cls, v: str) -> str:
        v_norm = EDGE_TYPE_ALIASES.get(v, v)
        if v_norm not in {e.value for e in EdgeType}:
            raise ValueError(f"edge_type {v!r} (normalized: {v_norm!r}) not in enum")
        return v_norm

    @field_validator("description")
    @classmethod
    def _require_description(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("empty edge description (hard reject per project style)")
        return v.strip()

    @model_validator(mode="after")
    def _check_tool_used_matches_edge_type(self) -> "EdgeRow":
        try:
            et = EdgeType(self.edge_type)
        except ValueError as e:
            raise ValueError(f"invalid edge_type: {e}") from e
        allowed = EDGE_TOOL_USED[et]
        if self.tool_used not in allowed:
            raise ValueError(
                f"tool_used {self.tool_used!r} not in allowed set for edge_type {self.edge_type!r}: {sorted(allowed)}"
            )
        if self.source == self.target:
            raise ValueError(f"self-loop edge: {self.source!r} -> {self.target!r}")
        return self


class ExtractionMeta(BaseModel):
    """Top-level meta emitted by the LLM (optional but recommended)."""

    overall_confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    inferred_fields: list[str] = Field(default_factory=list)
    notes: str = ""


class RawExtraction(BaseModel):
    """The shape the LLM is asked to emit."""

    entities: list[dict[str, Any]] = Field(default_factory=list)
    relationships: list[dict[str, Any]] = Field(default_factory=list)
    meta: ExtractionMeta | None = Field(default=None, alias="_meta")
    model_config = {"populate_by_name": True}


class ValidatedExtraction(BaseModel):
    """The cleaned, validated output you can write into your KG."""

    nodes: list[NodeBase]
    edges: list[EdgeRow]
    dropped_entities: list[dict[str, str]] = Field(default_factory=list)
    dropped_edges: list[dict[str, str]] = Field(default_factory=list)
    meta: ExtractionMeta | None = None
    skill_name: str = ""


GLOBAL_FILE_PATHS: frozenset[str] = frozenset(
    {"/bin/bash", "/bin/sh", "/usr/bin/env", "/usr/bin/bash"}
)


def compute_canonical_id(
    skill_name: str, ntype: NodeType, properties: dict[str, Any]
) -> str:
    """Layer 4 (ID_NORMALIZE): build the canonical, machine-stable node ID.

    The LLM extracts `properties.path` / `properties.domain` / `properties.name`
    as raw values; this function normalizes them into a deterministic ID suitable
    for M2 cross-skill dedup.

    Format:  "{skill_name}::{NodeType}::{key}"
    Special: "{GLOBAL}::FILE::/bin/bash"  for synthetic shell hosts (B1)
    """
    if ntype == NodeType.FILE:
        path = str(properties.get("path", "")).strip()
        if path in GLOBAL_FILE_PATHS:
            return f"GLOBAL::FILE::{path}"
        return f"{skill_name}::FILE::{path}"
    if ntype == NodeType.NETWORK:
        return f"{skill_name}::NETWORK::{properties.get('domain', '').strip()}"
    if ntype == NodeType.CREDENTIAL:
        name = (
            properties.get("name")
            or (
                properties.get("value")
                and f"{properties.get('type', 'unknown')}:{properties.get('scope', 'unknown')}:{properties['value']}"
            )
            or f"{properties.get('type', 'unknown')}:{properties.get('scope', 'unknown')}"
        )
        return f"{skill_name}::CREDENTIAL::{name}"
    if ntype == NodeType.PACKAGE:
        return f"{skill_name}::PACKAGE::{properties.get('registry', 'unknown')}:{properties.get('name', '')}"
    if ntype == NodeType.USER:
        question = (
            properties.get("user_question")
            or properties.get("name")
            or properties.get("interaction_type", "")
        )
        h = hash(str(question)) & 4294967295
        return f"{skill_name}::USER::{h:08x}"
    return f"{skill_name}::{ntype.value}::unknown"


def validate_extraction(
    raw: dict[str, Any], skill_name: str = "unknown"
) -> ValidatedExtraction:
    """
    Take whatever the LLM emitted (already JSON-parsed) and produce a clean
    `ValidatedExtraction`. Each row that fails Layer 1 is dropped and logged;
    Layer 2/3 mutations are kept on the surviving rows.

    This function NEVER raises for per-row problems — it logs and continues.
    It DOES raise if the top-level shape is unrecognizable (e.g. not a dict).
    """
    if not isinstance(raw, dict):
        raise ValueError(f"raw extraction must be a dict, got {type(raw).__name__}")
    try:
        envelope = RawExtraction.model_validate(raw)
    except ValidationError as e:
        logger.error("top-level extraction shape invalid: %s", e)
        raise
    nodes: list[NodeBase] = []
    dropped_entities: list[dict[str, str]] = []
    for ent in envelope.entities:
        try:
            ntype_raw = ent.get("type", "")
            type_norm = EDGE_TYPE_ALIASES.get(ntype_raw, ntype_raw)
            ntype = _normalize_node_type(ntype_raw)
            if ntype is None:
                raise ValueError(f"unknown entity type: {ntype_raw!r}")
            model_cls = NODE_MODELS[ntype]
            ent_for_model = {**ent, "type": ntype.value}
            ent_for_model.setdefault("_meta", {})
            ent_for_model["_meta"]["raw_output"] = ent.get("properties", {})
            node = model_cls.model_validate(ent_for_model)
            nodes.append(node)
        except (ValidationError, ValueError) as e:
            logger.warning("drop entity %r: %s", ent.get("name"), e)
            dropped_entities.append(
                {"name": str(ent.get("name", "")), "reason": str(e)}
            )
    valid_endpoints = (
        {n.name for n in nodes} | _TOOL_SOURCE_NAMES | set(GLOBAL_FILE_PATHS)
    )
    edges: list[EdgeRow] = []
    dropped_edges: list[dict[str, str]] = []
    for rel in envelope.relationships:
        try:
            edge = EdgeRow.model_validate(rel)
            if edge.source not in valid_endpoints:
                raise ValueError(
                    f"edge source {edge.source!r} not in extracted nodes or known tools"
                )
            if edge.target not in valid_endpoints:
                raise ValueError(
                    f"edge target {edge.target!r} not in extracted nodes or known tools"
                )
            edges.append(edge)
        except (ValidationError, ValueError) as e:
            logger.warning(
                "drop edge %r -> %r: %s", rel.get("source"), rel.get("target"), e
            )
            dropped_edges.append(
                {
                    "source": str(rel.get("source", "")),
                    "target": str(rel.get("target", "")),
                    "reason": str(e),
                }
            )
    for node in nodes:
        node.canonical_id = compute_canonical_id(skill_name, node.type, node.properties)
    return ValidatedExtraction(
        nodes=nodes,
        edges=edges,
        dropped_entities=dropped_entities,
        dropped_edges=dropped_edges,
        meta=envelope.meta,
        skill_name=skill_name,
    )


_NODE_TYPE_ALIASES: dict[str, NodeType] = {
    "file": NodeType.FILE,
    "FILE": NodeType.FILE,
    "File": NodeType.FILE,
    "network": NodeType.NETWORK,
    "NETWORK": NodeType.NETWORK,
    "Network": NodeType.NETWORK,
    "host": NodeType.NETWORK,
    "endpoint": NodeType.NETWORK,
    "url": NodeType.NETWORK,
    "credential": NodeType.CREDENTIAL,
    "CREDENTIAL": NodeType.CREDENTIAL,
    "Credential": NodeType.CREDENTIAL,
    "secret": NodeType.CREDENTIAL,
    "auth": NodeType.CREDENTIAL,
    "package": NodeType.PACKAGE,
    "PACKAGE": NodeType.PACKAGE,
    "Package": NodeType.PACKAGE,
    "library": NodeType.PACKAGE,
    "dependency": NodeType.PACKAGE,
    "user": NodeType.USER,
    "USER": NodeType.USER,
    "User": NodeType.USER,
    "human": NodeType.USER,
    "prompt": NodeType.USER,
    "interaction": NodeType.USER,
}


def _normalize_node_type(raw: str) -> NodeType | None:
    return _NODE_TYPE_ALIASES.get(raw)


_TOOL_SOURCE_NAMES: frozenset[str] = frozenset(
    (tool for tools in EDGE_TOOL_USED.values() for tool in tools)
)
_TOOL_SOURCE_NAMES = _TOOL_SOURCE_NAMES | frozenset(
    {
        "User",
        "user",
        "Agent",
        "agent",
        "SubAgent",
        "subagent",
        "Unknown",
        "unknown",
        "System",
        "system",
    }
)
