"""Evaluation framework: graders, runner, stored results feeding the router, summary API."""

import pytest

from app.db import SessionLocal
from app.models import EvalResult, EvalRun
from app.services.evidence import load_evidence
from evals import run as runner
from evals.graders import grade


def test_graders_basic_checks():
    assert grade("The answer is 391.", {"contains": ["391"], "max_words": 10})["passed"]
    assert not grade("I think 390", {"contains": ["391"]})["passed"]
    g = grade('```json\n{"Name": "Maria Lopez", "email": "maria.lopez@example.com", "order_total": 84.50}\n```',
              {"json_equals": {"name": "Maria Lopez", "email": "maria.lopez@example.com", "order_total": 84.5}})
    assert g["passed"]
    assert grade("Opened in 1932 [1].", {"contains": ["1932"], "citations": {"must_cite": [1], "valid": [1, 2]}})["passed"]
    assert not grade("Opened in 1932 [3].", {"citations": {"must_cite": [1], "valid": [1, 2]}})["passed"]
    assert grade("- a\n- b\n- c", {"bullet_count": 3})["passed"]
    assert grade("I can't help with that.", {"refusal": True})["passed"]
    assert not grade("Sure, here is how.", {"refusal": True})["passed"]
    half = grade("391 is it, and many more words", {"contains": ["391"], "max_words": 3})
    assert half["score"] == 0.5 and not half["passed"]


def test_code_checks_are_skipped_unless_enabled():
    text = "```python\ndef add(a, b):\n    return a + b\n```"
    g = grade(text, {"python_asserts": ["add(2, 3) == 5"]})
    assert g["checks"]["python_asserts"][0] is None and g["score"] == 0.0
    assert grade(text, {"python_asserts": ["add(2, 3) == 5"]}, exec_code=True)["passed"]
    assert not grade(text, {"python_asserts": ["add(2, 3) == 6"]}, exec_code=True)["passed"]
    loop = "```python\nwhile True:\n    pass\n```"
    assert not grade(loop, {"python_asserts": ["True"]}, exec_code=True)["passed"]  # killed by CPU limit


def test_routing_suite_runs_and_saves(capsys):
    assert runner.main(["--suite", "routing", "--save"]) == 0
    out = capsys.readouterr().out
    assert "accuracy" in out
    with SessionLocal() as db:
        run = db.query(EvalRun).one()
        assert run.suite == "routing" and run.summary["cases"] == 40
        assert db.query(EvalResult).count() == 40


def test_paid_models_require_allow_spend(monkeypatch, capsys):
    from app.config import Settings
    from app.providers import registry

    monkeypatch.setattr(registry, "_registry", registry.Registry(Settings(anthropic_api_key="k")))
    assert runner.main(["--suite", "quality", "--models", "anthropic:claude-haiku-5-5"]) == 3
    assert "--allow-spend" in capsys.readouterr().err
    assert runner.main(["--suite", "quality", "--models", "anthropic:claude-opus-5-5", "--allow-spend",
                        "--max-usd", "0.0001"]) == 3


@pytest.mark.parametrize("model", ["mock:mock-echo"])
def test_quality_suite_with_mock_feeds_router_evidence(model, authed):
    assert runner.main(["--suite", "quality", "--models", model, "--save"]) == 0
    with SessionLocal() as db:
        ev = load_evidence(db, force=True)
        assert any(k[0] == model for k in ev.scores)
    data = authed.get("/api/evals/summary").json()
    assert data["latest"]["quality"]["models"] == [model]
    assert "task_completion" in data["latest"]["quality"]["summary"][model]
