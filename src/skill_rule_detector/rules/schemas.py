"""Horn atoms, rules, and mining audit metadata."""

from pydantic import BaseModel, Field


class HornAtom(BaseModel):
    """Typed unary ML_NODE or binary behavior/ML_EDGE predicate."""

    predicate: str = Field(description="edge_type, e.g. EXEC, FETCH")
    arg1: str = Field(description="type label of subject, e.g. AGENT, FILE")
    arg2: str | None = Field(default=None, description="Second typed argument; absent for unary ML_NODE predicates")

    def __str__(self) -> str:
        args = self.arg1 if self.arg2 is None else f'{self.arg1}, {self.arg2}'
        return f'{self.predicate}({args})'


class HornRule(BaseModel):
    """A mined Horn rule: head ← body (conjunction of atoms).

    All metrics are per-skill deduplicated (a skill counts once for a rule
    regardless of how many times the rule's atoms ground in it), so large
    skills don't dominate.

    - support:            # skills where body ∧ head both hold.
    - body_support:       # skills where body holds (denominator of confidence).
    - head_support:       # skills where head holds.
    - standard_confidence: support / body_support.
    - head_coverage:      support / head_support (AMIE's primary support).
    """

    rule_id: str
    rule_key: str = Field(
        default="",
        description="content-addressed identity: sha256(canonical rule signature)[:16]; stable across re-minings and ID reorderings, unlike the display rule_id",
    )
    head: HornAtom
    body: list[HornAtom] = Field(default_factory=list)
    support: int = Field(ge=0, description="# skills satisfying body ∧ head")
    body_support: int = Field(ge=0, description="# skills satisfying body")
    head_support: int = Field(ge=0, description="# skills satisfying head")
    standard_confidence: float = Field(ge=0.0, le=1.0)
    head_coverage: float = Field(ge=0.0, le=1.0)
    example_skills: list[str] = Field(default_factory=list)

    def signature(self) -> str:
        body_str = " ∧ ".join((str(a) for a in self.body)) if self.body else "∅"
        return f"{self.head} ← {body_str}"


class HornMiningMeta(BaseModel):
    """Audit block for a Horn-rule mining run (written into _horn_rules.json)."""

    skills_total: int
    triples_total: int = Field(
        description="# typed triples across all skills (after per-skill dedup)"
    )
    predicates: list[str] = Field(
        default_factory=list, description="edge_types seen, sorted"
    )
    rules_found: int
    min_support: int
    min_confidence: float
    max_body_len: int
    min_head_coverage: float = Field(
        default=0.0,
        description="Primary anti-monotone pruning ratio (AMIE/AMIE+/AMIE3 default 0.01)",
    )
    min_head_support: int = Field(
        default=0,
        description="Minimum head-atom skill count to be head-eligible (AMIE -minis, default 100 there)",
    )
    connected_only: bool = Field(
        default=True,
        description="Whether body atoms must share a variable with the head (AMIE language bias)",
    )
    workers: int = Field(
        default=1, ge=1, description="CPU workers used for head-sharded mining"
    )
    start_method: str = Field(
        default="serial", description="Process start method or serial path"
    )
    note: str = Field(
        default="AMIE-style level-wise search with anti-monotone pruning; metrics per-skill deduplicated."
    )
