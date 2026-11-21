"""Provider registry and automatic model routing."""

from ..config import Settings, get_settings
from .anthropic_provider import AnthropicProvider
from .base import ModelInfo, Provider, ReasoningMode  # noqa: F401
from .catalog import load_catalog
from .gemini_provider import GeminiProvider
from .mock_provider import MockProvider
from .openai_provider import OpenAIProvider, XAIProvider


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

    def route(self, prompt: str, mode: ReasoningMode, needs_vision: bool = False, *, evidence=None,
              allowed_providers: list[str] | None = None, preferred_providers: list[str] | None = None):
        """Rank configured models for this request with the Trio Router (see services/router.py)."""
        from ..services.health import health
        from ..services.router import rank_models

        return rank_models(self.available_models(), prompt, mode, needs_vision=needs_vision, health=health,
                           evidence=evidence, allowed_providers=allowed_providers,
                           preferred_providers=preferred_providers)


_registry: Registry | None = None


def get_registry() -> Registry:
    global _registry
    if _registry is None:
        _registry = Registry(get_settings())
    return _registry


def reset_registry() -> None:
    global _registry
    _registry = None
