from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .model_routing import ModelCandidate, ModelTask, route_for


@dataclass(frozen=True)
class Settings:
    groq_api_key: str | None
    gemini_api_key: str | None
    openrouter_api_key: str | None
    deepseek_api_key: str | None
    jina_api_key: str | None
    mixedbread_api_key: str | None
    voyage_api_key: str | None
    main_provider: str
    main_model: str
    fast_provider: str
    fast_model: str
    json_provider: str
    json_model: str
    vision_provider: str
    vision_model: str
    fix_provider: str
    fix_model: str
    embeddings_provider: str
    embeddings_model: str
    headless: bool
    timeout_ms: int
    navigation_timeout_ms: int
    networkidle_timeout_ms: int
    screenshot_dir: str
    terminal_logs: bool
    render_wait_ms: int
    interaction_wait_ms: int
    active_wait_ms: int
    stability_wait_ms: int
    hover_timeout_ms: int
    stability_checks: int
    pass2_wait_multiplier: float
    pass3_wait_multiplier: float

    @property
    def llm_enabled(self) -> bool:
        return bool(self.api_key_for(self.main_provider))

    def api_key_for(self, provider: str) -> str | None:
        return {
            "groq": self.groq_api_key,
            "gemini": self.gemini_api_key,
            "openrouter": self.openrouter_api_key,
            "deepseek": self.deepseek_api_key,
            "jina": self.jina_api_key,
            "mixedbread": self.mixedbread_api_key,
            "voyage": self.voyage_api_key,
        }.get(provider)

    def primary_candidate(self, task: ModelTask) -> ModelCandidate:
        configured = {
            "main_orchestrator": (self.main_provider, self.main_model),
            "fast_ambiguity": (self.fast_provider, self.fast_model),
            "json_classification": (self.json_provider, self.json_model),
            "vision": (self.vision_provider, self.vision_model),
            "fix_generation": (self.fix_provider, self.fix_model),
            "embeddings": (self.embeddings_provider, self.embeddings_model),
        }[task]
        provider, model = configured
        for candidate in route_for(task):
            if candidate.provider == provider and candidate.model == model:
                return candidate
        return ModelCandidate(task, provider, model, "configured", f"MODEL_CUSTOM_{task.upper()}")


def load_settings() -> Settings:
    _load_dotenv()
    return Settings(
        groq_api_key=os.getenv("GROQ_API_KEY"),
        gemini_api_key=os.getenv("GEMINI_API_KEY"),
        openrouter_api_key=os.getenv("OPENROUTER_API_KEY"),
        deepseek_api_key=os.getenv("DEEPSEEK_API_KEY"),
        jina_api_key=os.getenv("JINA_API_KEY"),
        mixedbread_api_key=os.getenv("MIXEDBREAD_API_KEY"),
        voyage_api_key=os.getenv("VOYAGE_API_KEY"),
        main_provider=os.getenv("HUMANONN_MAIN_PROVIDER", "groq"),
        main_model=os.getenv("HUMANONN_MAIN_MODEL", "llama-3.3-70b-versatile"),
        fast_provider=os.getenv("HUMANONN_FAST_PROVIDER", "groq"),
        fast_model=os.getenv("HUMANONN_FAST_MODEL", "llama-3.1-8b-instant"),
        json_provider=os.getenv("HUMANONN_JSON_PROVIDER", "groq"),
        json_model=os.getenv("HUMANONN_JSON_MODEL", "llama-3.3-70b-versatile"),
        vision_provider=os.getenv("HUMANONN_VISION_PROVIDER", "gemini"),
        vision_model=os.getenv("HUMANONN_VISION_MODEL", "gemini-2.5-flash"),
        fix_provider=os.getenv("HUMANONN_FIX_PROVIDER", "deepseek"),
        fix_model=os.getenv("HUMANONN_FIX_MODEL", "deepseek-chat"),
        embeddings_provider=os.getenv("HUMANONN_EMBEDDINGS_PROVIDER", "jina"),
        embeddings_model=os.getenv("HUMANONN_EMBEDDINGS_MODEL", "jina-embeddings-v3"),
        headless=os.getenv("HUMANONN_HEADLESS", "true").lower() != "false",
        timeout_ms=int(os.getenv("HUMANONN_TIMEOUT_MS", "30000")),
        navigation_timeout_ms=int(os.getenv("HUMANONN_NAVIGATION_TIMEOUT_MS", os.getenv("HUMANONN_TIMEOUT_MS", "30000"))),
        networkidle_timeout_ms=int(os.getenv("HUMANONN_NETWORKIDLE_TIMEOUT_MS", "12000")),
        screenshot_dir=os.getenv("HUMANONN_SCREENSHOT_DIR", "reports/screenshots"),
        terminal_logs=os.getenv("HUMANONN_TERMINAL_LOGS", "true").lower() != "false",
        render_wait_ms=int(os.getenv("HUMANONN_RENDER_WAIT_MS", "700")),
        interaction_wait_ms=int(os.getenv("HUMANONN_INTERACTION_WAIT_MS", "180")),
        active_wait_ms=int(os.getenv("HUMANONN_ACTIVE_WAIT_MS", "140")),
        stability_wait_ms=int(os.getenv("HUMANONN_STABILITY_WAIT_MS", "220")),
        hover_timeout_ms=int(os.getenv("HUMANONN_HOVER_TIMEOUT_MS", "4000")),
        stability_checks=int(os.getenv("HUMANONN_STABILITY_CHECKS", "8")),
        pass2_wait_multiplier=float(os.getenv("HUMANONN_PASS2_WAIT_MULTIPLIER", "1.6")),
        pass3_wait_multiplier=float(os.getenv("HUMANONN_PASS3_WAIT_MULTIPLIER", "2.4")),
    )


def _load_dotenv(path: str = ".env") -> None:
    env_path = Path(path)
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ[key.strip()] = value.strip().strip('"').strip("'")
