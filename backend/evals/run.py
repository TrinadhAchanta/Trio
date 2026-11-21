"""Trio evaluation runner.

Usage (from backend/):
    python -m evals.run --suite routing                       # free, no model calls
    python -m evals.run --suite quality --models mock:mock-echo,mock:mock-deep
    python -m evals.run --suite quality --models anthropic:claude-haiku-5-5 --allow-spend --max-usd 0.50 --save

Rules:
- Models other than the mock are only called with ``--allow-spend``; the worst-case cost is estimated first
  and the run aborts if it is above ``--max-usd``.
- Model-written code is only executed with ``--exec-code`` (in a resource-limited subprocess). Run that on a
  disposable dev/CI machine, never on the production server.
- ``--save`` stores the run in the database; saved quality results feed the Trio Router's quality evidence.

Metrics per model: task completion (all checks passed), quality (mean fraction of checks passed), pass rate
per dimension (citation accuracy, tool accuracy, safety, ...), error rate, p50 latency, total cost.
User satisfaction comes from thumbs-up/down ratings and is reported by the API (``/api/evals/summary``).
"""

import argparse
import asyncio
import json
import statistics
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

from app.providers.base import ChatMessage, ProviderError
from app.providers.mock_provider import estimate_tokens
from app.providers.registry import get_registry
from app.services.router import TASK_TYPES, classify_task

from .graders import grade

DATASETS = Path(__file__).parent / "datasets"
SUITES = {"routing": "routing_v1.jsonl", "quality": "quality_v1.jsonl"}
SYSTEM = "You are a helpful assistant. Follow the user's formatting instructions exactly."
MAX_TOKENS = 800


def load(suite: str) -> tuple[str, list[dict]]:
    name = SUITES[suite]
    rows = [json.loads(line) for line in (DATASETS / name).read_text().splitlines() if line.strip()]
    return name.removesuffix(".jsonl"), rows


# ---- routing suite ----
def run_routing(rows: list[dict]) -> dict:
    confusion: dict[str, Counter] = defaultdict(Counter)
    results = []
    for r in rows:
        got, conf = classify_task(r["prompt"])
        confusion[r["task_type"]][got] += 1
        results.append({"case_id": r["id"], "task_type": r["task_type"], "predicted": got, "confidence": conf,
                        "passed": got == r["task_type"]})
    correct = sum(1 for x in results if x["passed"])
    per_class = {t: round(confusion[t][t] / sum(confusion[t].values()), 3) for t in TASK_TYPES if confusion[t]}
    return {"accuracy": round(correct / len(rows), 3), "cases": len(rows), "per_class_accuracy": per_class,
            "misclassified": [{"id": x["case_id"], "expected": x["task_type"], "got": x["predicted"]}
                              for x in results if not x["passed"]], "results": results}


# ---- quality suite ----
def worst_case_cost(model_ids: list[str], rows: list[dict]) -> float:
    reg = get_registry()
    total = 0.0
    for mid in model_ids:
        _, info = reg.resolve(mid)
        for r in rows:
            c = info.cost(estimate_tokens(SYSTEM + r["prompt"]), MAX_TOKENS)
            total += c or 0.0
    return total


async def _call(model_id: str, prompt: str) -> dict:
    reg = get_registry()
    provider, info = reg.resolve(model_id)
    start = time.perf_counter()
    parts, usage = [], None
    try:
        async for ev in provider.stream(info.model, [ChatMessage("user", prompt)], system=SYSTEM,
                                        max_tokens=MAX_TOKENS, reasoning="balanced"):
            if ev.type == "text":
                parts.append(ev.text)
            else:
                usage = ev
        err = None
    except ProviderError as e:
        err = e.code
    text = "".join(parts)
    in_tok = usage.input_tokens if usage else estimate_tokens(SYSTEM + prompt)
    out_tok = usage.output_tokens if usage else estimate_tokens(text)
    return {"text": text, "error": err, "latency_ms": int((time.perf_counter() - start) * 1000),
            "cost_usd": info.cost(in_tok, out_tok)}


async def run_quality(model_ids: list[str], rows: list[dict], *, exec_code: bool) -> dict:
    per_model = {}
    for mid in model_ids:
        results = []
        for r in rows:
            out = await _call(mid, r["prompt"])
            g = grade(out["text"], r["checks"], exec_code=exec_code) if not out["error"] else \
                {"score": 0.0, "passed": False, "checks": {}}
            results.append({"case_id": r["id"], "task_type": r["task_type"], "dimensions": r.get("dimensions", []),
                            "passed": g["passed"], "score": g["score"], "checks": g["checks"], "error": out["error"],
                            "latency_ms": out["latency_ms"], "cost_usd": out["cost_usd"],
                            "output_excerpt": out["text"][:500]})
        per_model[mid] = {"summary": summarize(results), "results": results}
    return per_model


def summarize(results: list[dict]) -> dict:
    dims: dict[str, list[bool]] = defaultdict(list)
    for x in results:
        for d in x["dimensions"]:
            dims[d].append(x["passed"])
    lats = [x["latency_ms"] for x in results if not x["error"]]
    return {
        "cases": len(results),
        "task_completion": round(sum(x["passed"] for x in results) / len(results), 3),
        "quality": round(statistics.mean(x["score"] for x in results), 3),
        "dimensions": {d: round(sum(v) / len(v), 3) for d, v in sorted(dims.items())},
        "error_rate": round(sum(1 for x in results if x["error"]) / len(results), 3),
        "latency_p50_ms": int(statistics.median(lats)) if lats else None,
        "cost_usd": round(sum(x["cost_usd"] or 0 for x in results), 6),
    }


def save(suite: str, version: str, models: list[str], summary: dict, rows: list[tuple[str, dict]]) -> str:
    from app.db import SessionLocal
    from app.models import EvalResult, EvalRun
    from app.services import evidence

    with SessionLocal() as db:
        run = EvalRun(suite=suite, dataset_version=version, models=models, summary=summary)
        db.add(run)
        db.flush()
        for model_id, x in rows:
            db.add(EvalResult(run_id=run.id, model_id=model_id, case_id=x["case_id"], task_type=x["task_type"],
                              passed=x["passed"], score=float(x.get("score", 1.0 if x["passed"] else 0.0)),
                              latency_ms=x.get("latency_ms"), cost_usd=x.get("cost_usd"), error=x.get("error"),
                              output_excerpt=x.get("output_excerpt")))
        db.commit()
        evidence.invalidate()
        return str(run.id)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--suite", choices=sorted(SUITES), required=True)
    ap.add_argument("--models", default="mock:mock-echo", help="comma-separated provider:model ids (quality suite)")
    ap.add_argument("--allow-spend", action="store_true", help="allow calls to paid (non-mock) models")
    ap.add_argument("--max-usd", type=float, default=1.0, help="abort if the worst-case cost estimate is higher")
    ap.add_argument("--exec-code", action="store_true", help="execute model-written code for code cases (dev/CI only)")
    ap.add_argument("--save", action="store_true", help="store the run in the database")
    ap.add_argument("--json", action="store_true", help="print the full JSON report")
    a = ap.parse_args(argv)

    version, rows = load(a.suite)
    if a.suite == "routing":
        report = run_routing(rows)
        print(f"Routing classifier on {version}: accuracy {report['accuracy']:.1%} over {report['cases']} cases")
        for t, acc in report["per_class_accuracy"].items():
            print(f"  {t:<11} {acc:.0%}")
        for m in report["misclassified"]:
            print(f"  miss {m['id']}: expected {m['expected']}, got {m['got']}")
        if a.save:
            summary = {k: v for k, v in report.items() if k != "results"}
            rid = save("routing", version, ["router:classifier"], summary,
                       [("router:classifier", x) for x in report["results"]])
            print(f"saved run {rid}")
        if a.json:
            print(json.dumps(report, indent=2))
        return 0

    reg = get_registry()
    models = [m.strip() for m in a.models.split(",") if m.strip()]
    for mid in models:
        resolved = reg.resolve(mid)
        if not resolved:
            print(f"unknown model {mid}", file=sys.stderr)
            return 2
        if not resolved[0].configured():
            print(f"{mid}: provider not configured (no API key)", file=sys.stderr)
            return 2
    paid = [m for m in models if not m.startswith("mock:")]
    if paid:
        est = worst_case_cost(paid, rows)
        print(f"Worst-case estimated cost for {len(paid)} paid model(s) x {len(rows)} cases: ${est:.4f}")
        if not a.allow_spend:
            print("Refusing to call paid models without --allow-spend.", file=sys.stderr)
            return 3
        if est > a.max_usd:
            print(f"Estimate is above --max-usd {a.max_usd:.2f}; aborting.", file=sys.stderr)
            return 3

    per_model = asyncio.run(run_quality(models, rows, exec_code=a.exec_code))
    print(f"Quality suite {version} ({len(rows)} cases){'' if a.exec_code else '; code execution checks skipped'}")
    for mid, r in per_model.items():
        s = r["summary"]
        dims = ", ".join(f"{k} {v:.0%}" for k, v in s["dimensions"].items())
        print(f"  {mid}: completion {s['task_completion']:.0%}, quality {s['quality']:.2f}, errors {s['error_rate']:.0%}, "
              f"p50 {s['latency_p50_ms']} ms, cost ${s['cost_usd']:.4f}  [{dims}]")
    if a.save:
        summary = {mid: r["summary"] for mid, r in per_model.items()}
        rid = save("quality", version, models, summary, [(mid, x) for mid, r in per_model.items() for x in r["results"]])
        print(f"saved run {rid}")
    if a.json:
        print(json.dumps(per_model, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
