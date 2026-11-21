"""Provider registry and automatic model routing."""

import re
from dataclasses import dataclass

from ..config import Settings, get_settings
from .anthropic_provider import AnthropicProvider
from .base import ModelInfo, Provider, ReasoningMode
from .catalog import load_catalog
from .gemini_provider import GeminiProvider
from .mock_provider import MockProvider
from .openai_provider import OpenAIProvider, XAIProvider

# Order used when several configured providers could serve a request.
PROVIDER_PREFERENCE = ["anthropic", "openai", "gemini", "xai", "mock"]

_COMPLEX_HINTS = re.compile(
    r"\b(prove|derive|analy[sz]e|architecture|step[- ]by[- ]step|debug|refactor|optimi[sz]e|trade-?offs?|"
    r"research|compare|strategy|algorithm|complexity)\b",
    re.I,
)


@dataclass
class RouteDecision:
    model: ModelInfo
    reason: str


class Registry:
    def __init__(self, settings: Settings):
        catalog = load_catalog()
        self.providers: dict[str, Provider] = {
            "anthropic": AnthropicProvider(settings.anthropic_api_key, catalog, settings.anthropic_api_base),
            "openai": OpenAIProvider(settings.openai_api_key, catalog, settings.openai_api_base),
            "gemini": GeminiProvider(settings.gemini_api_key, catalog, settings.gemini_api_base),
            "xai": XAIProvider(settings.xai_api_key, catalog, settings.xai_api_base),
            "mock": MockProvider(settings.enable_mock_provider, catalog),
        }

    def all_models(self) -> list[ModelInfo]:
        return [m for p in self.providers.values() for m in p.models()]

    def available_models(self) -> list[ModelInfo]:
        return [m for p in self.providers.values() if p.configured() for m in p.models()]

    def resolve(self, model_id: str) -> tuple[Provider, ModelInfo] | None:
        provider_name, _, model = model_id.partition(":")
        provider = self.providers.get(provider_name)
        if not provider or not model:
            return None
        for m in provider.models():
            if m.model == model:
                return provider, m
        return None

    def route(self, prompt: str, mode: ReasoningMode, needs_vision: bool = False) -> RouteDecision | None:
        """Pick a configured model for 'auto'. Real providers are always preferred over mock."""
        candidates = [m for m in self.available_models() if (m.vision or not needs_vision)]
        if not candidates:
            return None
        real = [m for m in candidates if m.provider != "mock"]
        pool = real or candidates

        complex_prompt = len(prompt) > 1500 or "```" in prompt or bool(_COMPLEX_HINTS.search(prompt))
        if mode == "deep":
            tier, why = "deep", "Deep mode"
        elif mode == "fast":
            tier, why = "fast", "Fast mode"
        elif complex_prompt:
            tier, why = "deep", "Balanced mode, prompt looks complex"
        else:
            tier, why = "balanced", "Balanced mode, everyday prompt"

        order = {"fast": ["fast", "balanced", "deep"], "balanced": ["balanced", "fast", "deep"],
                 "deep": ["deep", "balanced", "fast"]}[tier]
        for t in order:
            for pname in PROVIDER_PREFERENCE:
                for m in pool:
                    if m.tier == t and m.provider == pname:
                        suffix = "" if t == tier else f" (no {tier}-tier model configured)"
                        return RouteDecision(m, f"{why}: chose {m.label}{suffix}")
        m = pool[0]
        return RouteDecision(m, f"{why}: chose {m.label}")


_registry: Registry | None = None


def get_registry() -> Registry:
    global _registry
    if _registry is None:
        _registry = Registry(get_settings())
    return _registry


def reset_registry() -> None:
    global _registry
    _registry = None
