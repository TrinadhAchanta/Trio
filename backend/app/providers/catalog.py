"""Default model catalog.

Pricing and model ids change often. Everything here can be overridden without a code change
by pointing ``TRIO_MODEL_CATALOG`` at a JSON file with the same shape (see
``models.example.json`` in the backend folder). Anthropic entries come from Anthropic's current
model reference; other vendors' entries are defaults to verify against each vendor's live
``/models`` listing, which the API exposes at ``GET /api/models?verify=true`` once a key is set.
"""

import json
import os

from .base import ModelInfo

DEFAULT_CATALOG: list[dict] = [
    # Anthropic (documented ids and prices, USD per million tokens)
    {"provider": "anthropic", "model": "claude-opus-5-5", "label": "Claude Opus 5.5", "tier": "deep",
     "vision": True, "context_window": 1_000_000, "input_price_per_mtok": 4.0, "output_price_per_mtok": 20.0,
     "source": "documented"},
    {"provider": "anthropic", "model": "claude-sonnet-5-5", "label": "Claude Sonnet 5.5", "tier": "balanced",
     "vision": True, "context_window": 1_000_000, "input_price_per_mtok": 2.0, "output_price_per_mtok": 10.0,
     "source": "documented"},
    {"provider": "anthropic", "model": "claude-haiku-5-5", "label": "Claude Haiku 5.5", "tier": "fast",
     "vision": True, "context_window": 1_000_000, "input_price_per_mtok": 0.10, "output_price_per_mtok": 0.50,
     "source": "documented"},
    # OpenAI (catalog defaults: verify ids and prices)
    {"provider": "openai", "model": "gpt-5", "label": "GPT-5", "tier": "deep", "vision": True,
     "input_price_per_mtok": 1.25, "output_price_per_mtok": 10.0},
    {"provider": "openai", "model": "gpt-5-mini", "label": "GPT-5 mini", "tier": "balanced", "vision": True,
     "input_price_per_mtok": 0.25, "output_price_per_mtok": 2.0},
    {"provider": "openai", "model": "gpt-5-nano", "label": "GPT-5 nano", "tier": "fast", "vision": True,
     "input_price_per_mtok": 0.05, "output_price_per_mtok": 0.40},
    # Google Gemini (catalog defaults: verify ids and prices)
    {"provider": "gemini", "model": "gemini-2.5-pro", "label": "Gemini 2.5 Pro", "tier": "deep", "vision": True,
     "input_price_per_mtok": 1.25, "output_price_per_mtok": 10.0},
    {"provider": "gemini", "model": "gemini-2.5-flash", "label": "Gemini 2.5 Flash", "tier": "balanced",
     "vision": True, "input_price_per_mtok": 0.30, "output_price_per_mtok": 2.50},
    # xAI (catalog defaults: verify ids and prices)
    {"provider": "xai", "model": "grok-4", "label": "Grok 4", "tier": "deep", "vision": True,
     "input_price_per_mtok": 3.0, "output_price_per_mtok": 15.0},
    {"provider": "xai", "model": "grok-3-mini", "label": "Grok 3 mini", "tier": "fast", "vision": False,
     "input_price_per_mtok": 0.30, "output_price_per_mtok": 0.50},
    # Mock (local demo/testing only, never billed)
    {"provider": "mock", "model": "mock-echo", "label": "Mock (demo)", "tier": "fast", "vision": True,
     "input_price_per_mtok": 0.0, "output_price_per_mtok": 0.0, "source": "local"},
    {"provider": "mock", "model": "mock-deep", "label": "Mock Deep (demo)", "tier": "deep", "vision": True,
     "input_price_per_mtok": 0.0, "output_price_per_mtok": 0.0, "source": "local"},
]


def load_catalog() -> list[ModelInfo]:
    path = os.environ.get("TRIO_MODEL_CATALOG")
    raw = DEFAULT_CATALOG
    if path:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    return [ModelInfo(**entry) for entry in raw]
