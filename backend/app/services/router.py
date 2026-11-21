"""Trio Router: scores every available model for a request and picks the best one.

Inputs per request: task type (classified from the prompt), reasoning mode, whether images are
attached, the user's provider allow-list and preferences. Inputs per model: expected quality for
that task type, estimated cost, expected latency, and live availability.

Expected quality starts from a prior by model tier and task type and is updated with evidence:
offline evaluation results (``evals/``) and users' thumbs-up/down ratings, combined as a
Beta-style weighted average so a few data points cannot swing the choice.

utility = quality - w_cost * cost_norm - w_latency * latency_norm    (+ small preference bonus)

The weights depend on the mode (Fast favors latency, Deep favors quality). The decision returns
a ranked list, which also serves as the fallback order.
"""

import re
from dataclasses import dataclass, field

from ..providers.base import ModelInfo
from .health import HealthMonitor

TASK_TYPES = ("code", "math", "writing", "research", "extraction", "analysis", "chat")

_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("code", re.compile(
        r"```|\b(code|function|bug|debug|stack ?trace|exception|compile|refactor|regex|python|javascript|typescript|"
        r"java|golang|rust|sql|api|endpoint|unit tests?|react|css|html|script|algorithm)\b", re.I)),
    ("math", re.compile(
        r"\b(solve|equation|integral|derivative|prove|proof|probability|theorem|calculate|matrix)\b|\d+\s*[\+\-\*/\^]\s*\d+",
        re.I)),
    ("research", re.compile(
        r"\b(latest|news|research|sources?|citations?|cite|recent|trend|market|competitors?|according to)\b", re.I)),
    ("extraction", re.compile(r"\b(extract|parse|convert .* to json|as json|structured|fields?)\b", re.I)),
    ("writing", re.compile(
        r"\b(write|draft|essay|email|blog|story|poem|rewrite|proofread|cover letter|resume|tagline|slogan)\b", re.I)),
    ("analysis", re.compile(r"\b(summari[sz]e|analy[sz]e|compare|pros and cons|explain|evaluate|review)\b", re.I)),
]

# Prior expected quality (0..1) by tier and task type. Deliberately conservative; evidence moves them.
PRIORS: dict[str, dict[str, float]] = {
    "deep": {"default": 0.90, "code": 0.95, "math": 0.95, "research": 0.92, "analysis": 0.92},
    "balanced": {"default": 0.80, "code": 0.82, "math": 0.80, "chat": 0.80},
    "fast": {"default": 0.65, "code": 0.60, "math": 0.55, "chat": 0.68, "extraction": 0.70},
}
DEFAULT_LATENCY_MS = {"fast": 1500.0, "balanced": 3000.0, "deep": 8000.0}
EXPECTED_OUTPUT_TOKENS = {"code": 900, "writing": 700, "research": 900, "analysis": 600, "math": 500,
                          "extraction": 300, "chat": 300}
WEIGHTS = {  # (cost, latency)
    "fast": (0.15, 0.80),
    "balanced": (0.10, 0.10),
    "deep": (0.02, 0.02),
}
PRIOR_STRENGTH = 5.0  # how many observations the prior is worth
MOCK_QUALITY_FACTOR = 0.1  # the mock provider must never outrank a real model
PREFERENCE_BONUS = 0.05


def classify_task(prompt: str, needs_vision: bool = False) -> tuple[str, float]:
    """Cheap, deterministic task classifier. Returns (task_type, confidence)."""
    hits = [(name, len(p.findall(prompt))) for name, p in _PATTERNS]
    hits = [(n, c) for n, c in hits if c]
    if not hits:
        return ("analysis" if needs_vision else "chat"), 0.5
    hits.sort(key=lambda x: -x[1])  # stable: earlier patterns win ties
    total = sum(c for _, c in hits)
    return hits[0][0], round(hits[0][1] / total, 2)


@dataclass
class Evidence:
    """Observed quality per (model_id, task_type): list of scores in 0..1."""

    scores: dict[tuple[str, str], list[float]] = field(default_factory=dict)

    def add(self, model_id: str, task_type: str, score: float) -> None:
        self.scores.setdefault((model_id, task_type), []).append(score)


@dataclass
class Candidate:
    model: ModelInfo
    utility: float
    quality: float
    est_cost_usd: float | None
    est_latency_ms: float
    state: str


@dataclass
class Decision:
    task_type: str
    confidence: float
    ranked: list[Candidate]

    @property
    def model(self) -> ModelInfo:
        return self.ranked[0].model

    @property
    def reason(self) -> str:
        c = self.ranked[0]
        cost = "free" if not c.est_cost_usd else f"~${c.est_cost_usd:.4f}"
        return (f"{self.task_type.capitalize()} task: chose {c.model.label} "
                f"(quality {c.quality:.2f}, {cost}, ~{c.est_latency_ms / 1000:.1f}s)")


def expected_quality(m: ModelInfo, task_type: str, evidence: Evidence | None) -> float:
    pri = PRIORS[m.tier].get(task_type, PRIORS[m.tier]["default"])
    obs = (evidence.scores.get((m.id, task_type), []) if evidence else [])
    q = (pri * PRIOR_STRENGTH + sum(obs)) / (PRIOR_STRENGTH + len(obs))
    return q * (MOCK_QUALITY_FACTOR if m.provider == "mock" else 1.0)


def estimate_cost(m: ModelInfo, prompt_tokens: int, task_type: str) -> float | None:
    return m.cost(prompt_tokens, EXPECTED_OUTPUT_TOKENS.get(task_type, 400))


def _norm(values: list[float]) -> list[float]:
    lo, hi = min(values), max(values)
    return [0.0 if hi == lo else (v - lo) / (hi - lo) for v in values]


def rank_models(
    models: list[ModelInfo],
    prompt: str,
    mode: str,
    *,
    needs_vision: bool = False,
    health: HealthMonitor | None = None,
    evidence: Evidence | None = None,
    allowed_providers: list[str] | None = None,
    preferred_providers: list[str] | None = None,
) -> Decision | None:
    task_type, conf = classify_task(prompt, needs_vision)
    pool = [m for m in models if (m.vision or not needs_vision)]
    if allowed_providers:
        pool = [m for m in pool if m.provider in allowed_providers or m.provider == "mock"]
    states = {m.provider: (health.state(m.provider) if health else "unknown") for m in pool}
    usable = [m for m in pool if states[m.provider] != "down"]
    pool = usable or pool  # if everything is down, still try (the circuit may have recovered)
    if not pool:
        return None

    prompt_tokens = max(1, len(prompt) // 4)
    costs = [estimate_cost(m, prompt_tokens, task_type) for m in pool]
    known = [c for c in costs if c is not None]
    fill = max(known) if known else 0.0
    cost_vals = [c if c is not None else fill for c in costs]
    lat_vals = [((health.p50_latency(m.provider, m.model) if health else None) or DEFAULT_LATENCY_MS[m.tier])
                for m in pool]
    wc, wl = WEIGHTS.get(mode, WEIGHTS["balanced"])
    cn, ln = _norm(cost_vals), _norm(lat_vals)
    cands = []
    for i, m in enumerate(pool):
        q = expected_quality(m, task_type, evidence)
        u = q - wc * cn[i] - wl * ln[i]
        if preferred_providers and m.provider in preferred_providers:
            u += PREFERENCE_BONUS
        if states[m.provider] in ("degraded", "recovering"):
            u -= 0.15
        cands.append(Candidate(m, round(u, 4), round(q, 4), costs[i], lat_vals[i], states[m.provider]))
    cands.sort(key=lambda c: -c.utility)
    return Decision(task_type, conf, cands)
