"""Provider health monitoring and circuit breaking.

Every provider call reports its outcome here. A provider that fails ``FAILURE_THRESHOLD`` times in a
row with a retryable error (outage, timeout, rate limit) is marked *down* for ``COOLDOWN_S`` seconds;
the router skips it and requests fall back to other models. After the cooldown one trial request is
allowed (*recovering*); success closes the circuit again.

State is per API process. With several API instances, move it to Redis (same interface).
"""

import statistics
import time
from collections import deque
from dataclasses import dataclass, field
from threading import Lock

FAILURE_THRESHOLD = 3
COOLDOWN_S = 60.0
WINDOW = 50


@dataclass
class _ProviderState:
    consecutive_failures: int = 0
    opened_at: float | None = None
    outcomes: deque = field(default_factory=lambda: deque(maxlen=WINDOW))  # (ok: bool, latency_ms, ts)
    latencies: dict[str, deque] = field(default_factory=dict)  # model -> recent latencies
    last_error: str | None = None


class HealthMonitor:
    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._lock = Lock()
        self._state: dict[str, _ProviderState] = {}

    def _s(self, provider: str) -> _ProviderState:
        return self._state.setdefault(provider, _ProviderState())

    def record(self, provider: str, model: str, ok: bool, latency_ms: int | None = None,
               retryable: bool = True, error: str | None = None) -> None:
        with self._lock:
            s = self._s(provider)
            s.outcomes.append((ok, latency_ms, self._clock()))
            if ok:
                s.consecutive_failures = 0
                s.opened_at = None
                if latency_ms is not None:
                    s.latencies.setdefault(model, deque(maxlen=WINDOW)).append(latency_ms)
            else:
                s.last_error = error
                if retryable:
                    s.consecutive_failures += 1
                    if s.consecutive_failures >= FAILURE_THRESHOLD:
                        s.opened_at = self._clock()

    def state(self, provider: str) -> str:
        """'healthy' | 'degraded' | 'down' | 'recovering' | 'unknown'."""
        with self._lock:
            s = self._state.get(provider)
            if not s or not s.outcomes:
                return "unknown"
            if s.opened_at is not None:
                return "down" if self._clock() - s.opened_at < COOLDOWN_S else "recovering"
            recent = list(s.outcomes)[-10:]
            fail_rate = sum(1 for ok, _, _ in recent if not ok) / len(recent)
            return "degraded" if fail_rate >= 0.3 else "healthy"

    def available(self, provider: str) -> bool:
        return self.state(provider) != "down"

    def p50_latency(self, provider: str, model: str) -> float | None:
        with self._lock:
            s = self._state.get(provider)
            lat = list(s.latencies.get(model, [])) if s else []
        return statistics.median(lat) if len(lat) >= 3 else None

    def snapshot(self) -> dict[str, dict]:
        out = {}
        for name in list(self._state):
            s = self._state[name]
            n = len(s.outcomes)
            ok = sum(1 for o, _, _ in s.outcomes if o)
            out[name] = {
                "state": self.state(name),
                "success_rate": round(ok / n, 3) if n else None,
                "samples": n,
                "consecutive_failures": s.consecutive_failures,
                "last_error": s.last_error,
            }
        return out

    def reset(self) -> None:
        with self._lock:
            self._state.clear()


health = HealthMonitor()
