import os
import tempfile

# Configure the app for tests BEFORE importing it.
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://trio:trio@localhost:5432/trio_test")
os.environ["ENVIRONMENT"] = "test"
os.environ["STORAGE_DIR"] = tempfile.mkdtemp(prefix="trio-test-uploads-")
os.environ["RATE_LIMIT_AUTH_PER_MINUTE"] = "1000"
os.environ["RATE_LIMIT_CHAT_PER_MINUTE"] = "1000"
# Make sure no real provider is ever called from tests, even if keys exist in the shell.
for k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "XAI_API_KEY"):
    os.environ[k] = ""

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.db import Base, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.providers.registry import get_registry  # noqa: E402
from app.security import limiter  # noqa: E402
from app.services import evidence  # noqa: E402
from app.services.health import health  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _schema():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    get_registry().providers["mock"].delay_s = 0
    yield
    Base.metadata.drop_all(engine)


@pytest.fixture(autouse=True)
def _clean():
    yield
    with engine.begin() as conn:
        names = ", ".join(t.name for t in Base.metadata.sorted_tables)
        conn.execute(text(f"TRUNCATE {names} CASCADE"))
    limiter.reset()
    health.reset()
    evidence.invalidate()


def make_client() -> TestClient:
    return TestClient(app)


def register(client: TestClient, email="ada@example.com", password="correct-horse-42", name="Ada"):
    r = client.post("/api/auth/register", json={"email": email, "password": password, "display_name": name})
    assert r.status_code == 201, r.text
    return r.json()


@pytest.fixture
def client():
    return make_client()


@pytest.fixture
def authed(client):
    register(client)
    return client


def parse_sse(body: str) -> list[tuple[str, dict]]:
    import json

    events = []
    for block in body.strip().split("\n\n"):
        ev, data = None, None
        for line in block.splitlines():
            if line.startswith("event: "):
                ev = line[7:]
            elif line.startswith("data: "):
                data = json.loads(line[6:])
        if ev:
            events.append((ev, data))
    return events
