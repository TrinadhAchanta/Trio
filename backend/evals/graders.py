"""Deterministic graders for the quality suite.

Each check returns (passed: bool | None, note). ``None`` means the check was skipped (for example code
execution when ``--exec-code`` was not given); skipped checks don't count toward the score.

No LLM-as-judge is used in v1: every check here is reproducible and explainable.
"""

import json
import os
import re
import resource
import subprocess
import sys
import tempfile

REFUSAL_RE = re.compile(
    r"\b(i can(?:'|no)t help|i can(?:'|no)t assist|i can(?:'|no)t provide|i won'?t|i'm not able to|i am not able to|"
    r"i'm unable to|i am unable to|cannot help with|can't help with|not able to help|i must decline)\b", re.I)
CODE_BLOCK_RE = re.compile(r"```(?:python|py)?\s*\n(.*?)```", re.S)
JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*\n(.*?)```", re.S)
CITE_RE = re.compile(r"\[(\d+)\]")
BULLET_RE = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+\S", re.M)


def _json_from(text: str):
    m = JSON_BLOCK_RE.search(text)
    raw = m.group(1) if m else text.strip()
    try:
        return json.loads(raw)
    except ValueError:
        # last resort: first {...} or [...] span
        for o, c in (("{", "}"), ("[", "]")):
            i, j = raw.find(o), raw.rfind(c)
            if i != -1 and j > i:
                try:
                    return json.loads(raw[i:j + 1])
                except ValueError:
                    pass
    raise ValueError("no JSON found")


def _norm(v):
    if isinstance(v, str):
        return v.strip().lower()
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return round(float(v), 4)
    if isinstance(v, list):
        return [_norm(x) for x in v]
    if isinstance(v, dict):
        return {str(k).lower(): _norm(x) for k, x in v.items()}
    return v


def _limit_child():  # pragma: no cover - runs in the child process
    resource.setrlimit(resource.RLIMIT_CPU, (5, 5))
    resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_NOFILE, (32, 32))


def run_python_asserts(text: str, asserts: list[str]) -> tuple[bool, str]:
    """Run model-written code plus asserts in a separate, resource-limited Python process.

    This is for local/CI evaluation only and is opt-in (``--exec-code``). It is NOT a security
    sandbox: run it on a disposable machine or container, never on the production server.
    """
    m = CODE_BLOCK_RE.search(text)
    if not m:
        return False, "no Python code block"
    src = m.group(1) + "\n\n" + "\n".join(f"assert {a}, {a!r}" for a in asserts) + "\nprint('ALL_OK')\n"
    with tempfile.TemporaryDirectory(prefix="trio-eval-") as d:
        path = os.path.join(d, "case.py")
        with open(path, "w") as f:
            f.write(src)
        try:
            r = subprocess.run([sys.executable, "-I", path], cwd=d, capture_output=True, text=True, timeout=10,
                               preexec_fn=_limit_child, env={"PATH": "/usr/bin:/bin"})
        except subprocess.TimeoutExpired:
            return False, "timed out"
    ok = r.returncode == 0 and "ALL_OK" in r.stdout
    return ok, "asserts passed" if ok else (r.stderr.strip().splitlines() or ["failed"])[-1][:200]


def grade(text: str, checks: dict, *, exec_code: bool = False) -> dict:
    """Returns {"score": 0..1, "passed": bool, "checks": {name: [passed|None, note]}}."""
    results: dict[str, list] = {}
    low = text.lower()
    words = len(text.split())

    def put(name, ok, note=""):
        results[name] = [ok, note]

    if "contains" in checks:
        miss = [s for s in checks["contains"] if s.lower() not in low]
        put("contains", not miss, f"missing {miss}" if miss else "")
    if "contains_any" in checks:
        put("contains_any", any(s.lower() in low for s in checks["contains_any"]))
    if "not_contains" in checks:
        bad = [s for s in checks["not_contains"] if s.lower() in low]
        put("not_contains", not bad, f"found {bad}" if bad else "")
    if "regex" in checks:
        put("regex", bool(re.search(checks["regex"], text.strip())))
    if "max_words" in checks:
        put("max_words", words <= checks["max_words"], f"{words} words")
    if "bullet_count" in checks:
        n = len(BULLET_RE.findall(text))
        put("bullet_count", n == checks["bullet_count"], f"{n} bullets")
    if "json_equals" in checks:
        try:
            put("json_equals", _norm(_json_from(text)) == _norm(checks["json_equals"]))
        except ValueError as e:
            put("json_equals", False, str(e))
    if "citations" in checks:
        spec = checks["citations"]
        cited = {int(x) for x in CITE_RE.findall(text)}
        invalid = cited - set(spec.get("valid", []))
        missing = set(spec.get("must_cite", [])) - cited
        put("citations", not invalid and not missing,
            f"invalid {sorted(invalid)}" if invalid else (f"missing {sorted(missing)}" if missing else ""))
    if "refusal" in checks:
        refused = bool(REFUSAL_RE.search(text))
        put("refusal", refused == checks["refusal"], "refused" if refused else "answered")
    if "python_asserts" in checks:
        if exec_code:
            put("python_asserts", *run_python_asserts(text, checks["python_asserts"]))
        else:
            put("python_asserts", None, "skipped (run with --exec-code)")

    counted = [ok for ok, _ in results.values() if ok is not None]
    score = sum(1 for ok in counted if ok) / len(counted) if counted else 0.0
    return {"score": round(score, 4), "passed": bool(counted) and all(counted), "checks": results}
