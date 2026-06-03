from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .model_routing import ModelCandidate, ModelTask, route_for


@dataclass(frozen=True)
class Settings:
    groq_api_key: str | None
    groq_api_key_2: str | None
    gemini_api_key: str | None
    gemini_api_key_2: str | None
    openrouter_api_key: str | None
    openrouter_api_key_2: str | None
    deepseek_api_key: str | None
    deepseek_api_key_2: str | None
    huggingface_api_key: str | None
    huggingface_api_key_2: str | None
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
    force_no_llm: bool = False
    llm_adjustment_gate_enabled: bool = False
    llm_adjustment_multiplier_enabled: bool = True
    llm_adjustment_evidence_floor: float = 0.35
    llm_adjustment_single_source_cap: float = 5.0
    llm_adjustment_headroom_enabled: bool = True
    smart_scoring_gate_enabled: bool = False
    smart_summary_enabled: bool = True
    dynamic_findings_enabled: bool = True
    live_site_scraping_enabled: bool = True
    # Smart scoring / selective sampling controls
    smart_conf_low: float = 0.35
    smart_conf_high: float = 0.65
    smart_center_low: float = 0.45
    smart_center_high: float = 0.55
    smart_top_k: int = 10
    smart_max_per_repo: int = 20
    smart_global_cap: int = 100
    smart_sample_outside_pct: float = 0.05
    smart_cache_ttl_days: int = 7
    # LLM cache controls
    llm_cache_enabled: bool = False
    llm_cache_ttl_days: int = 7
    github_token: str | None = None
    production: bool = False

    @property
    def llm_enabled(self) -> bool:
        if self.force_no_llm:
            return False
        return any(self.api_keys_for(candidate.provider) for candidate in route_for("main_orchestrator"))

    def api_key_for(self, provider: str) -> str | None:
        keys = self.api_keys_for(provider)
        return keys[0] if keys else None

    def api_keys_for(self, provider: str) -> list[str]:
        return {
            "groq": [key for key in (self.groq_api_key, self.groq_api_key_2) if key],
            "gemini": [key for key in (self.gemini_api_key, self.gemini_api_key_2) if key],
            "openrouter": [key for key in (self.openrouter_api_key, self.openrouter_api_key_2) if key],
            "deepseek": [key for key in (self.deepseek_api_key, self.deepseek_api_key_2) if key],
            "huggingface": [key for key in (self.huggingface_api_key, self.huggingface_api_key_2) if key],
            "jina": [key for key in (self.jina_api_key,) if key],
            "mixedbread": [key for key in (self.mixedbread_api_key,) if key],
            "voyage": [key for key in (self.voyage_api_key,) if key],
        }.get(provider, [])

    def api_key_labels_for(self, provider: str) -> list[tuple[str, str]]:
        return {
            "groq": [(name, value) for name, value in (("GROQ_API_KEY", self.groq_api_key), ("GROQ_API_KEY_2", self.groq_api_key_2)) if value],
            "gemini": [(name, value) for name, value in (("GEMINI_API_KEY", self.gemini_api_key), ("GEMINI_API_KEY_2", self.gemini_api_key_2)) if value],
            "openrouter": [(name, value) for name, value in (("OPENROUTER_API_KEY", self.openrouter_api_key), ("OPENROUTER_API_KEY_2", self.openrouter_api_key_2)) if value],
            "deepseek": [(name, value) for name, value in (("DEEPSEEK_API_KEY", self.deepseek_api_key), ("DEEPSEEK_API_KEY_2", self.deepseek_api_key_2)) if value],
            "huggingface": [(name, value) for name, value in (("HUGGINGFACE_API_KEY", self.huggingface_api_key), ("HUGGINGFACE_API_KEY_2", self.huggingface_api_key_2)) if value],
            "jina": [("JINA_API_KEY", self.jina_api_key)] if self.jina_api_key else [],
            "mixedbread": [("MIXEDBREAD_API_KEY", self.mixedbread_api_key)] if self.mixedbread_api_key else [],
            "voyage": [("VOYAGE_API_KEY", self.voyage_api_key)] if self.voyage_api_key else [],
        }.get(provider, [])

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
        groq_api_key_2=os.getenv("GROQ_API_KEY_2"),
        gemini_api_key=os.getenv("GEMINI_API_KEY"),
        gemini_api_key_2=os.getenv("GEMINI_API_KEY_2"),
        openrouter_api_key=os.getenv("OPENROUTER_API_KEY"),
        openrouter_api_key_2=os.getenv("OPENROUTER_API_KEY_2"),
        deepseek_api_key=os.getenv("DEEPSEEK_API_KEY"),
        deepseek_api_key_2=os.getenv("DEEPSEEK_API_KEY_2"),
        huggingface_api_key=os.getenv("HUGGINGFACE_API_KEY"),
        huggingface_api_key_2=os.getenv("HUGGINGFACE_API_KEY_2"),
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
        embeddings_provider=os.getenv("HUMANONN_EMBEDDINGS_PROVIDER", "groq"),
        embeddings_model=os.getenv("HUMANONN_EMBEDDINGS_MODEL", "llama-3.1-8b-instant"),
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
        force_no_llm=os.getenv("HUMANONN_NO_LLM", "false").lower() == "true",
        llm_adjustment_gate_enabled=os.getenv("HUMANONN_LLM_ADJUSTMENT_GATE", "false").lower() == "true",
        llm_adjustment_multiplier_enabled=os.getenv("HUMANONN_LLM_ADJUSTMENT_MULTIPLIER_ENABLED", "true").lower() != "false",
        llm_adjustment_evidence_floor=float(os.getenv("HUMANONN_LLM_ADJUSTMENT_EVIDENCE_FLOOR", "0.35")),
        llm_adjustment_single_source_cap=float(os.getenv("HUMANONN_LLM_ADJUSTMENT_SINGLE_SOURCE_CAP", "5.0")),
        llm_adjustment_headroom_enabled=os.getenv("HUMANONN_LLM_ADJUSTMENT_HEADROOM_ENABLED", "true").lower() != "false",
        smart_scoring_gate_enabled=os.getenv("HUMANONN_SMART_SCORING_GATE", "false").lower() == "true",
        smart_summary_enabled=os.getenv("HUMANONN_SMART_SUMMARY", "true").lower() != "false",
        dynamic_findings_enabled=os.getenv("HUMANONN_DYNAMIC_FINDINGS", "true").lower() != "false",
        live_site_scraping_enabled=os.getenv("HUMANONN_LIVE_SITE_SCRAPING", "true").lower() != "false",
        # Smart sampling envs
        smart_conf_low=float(os.getenv("HUMANONN_SMART_CONF_LOW", "0.35")),
        smart_conf_high=float(os.getenv("HUMANONN_SMART_CONF_HIGH", "0.65")),
        smart_center_low=float(os.getenv("HUMANONN_SMART_CENTER_LOW", "0.45")),
        smart_center_high=float(os.getenv("HUMANONN_SMART_CENTER_HIGH", "0.55")),
        smart_top_k=int(os.getenv("HUMANONN_SMART_TOP_K", "10")),
        smart_max_per_repo=int(os.getenv("HUMANONN_SMART_MAX_PER_REPO", "20")),
        smart_global_cap=int(os.getenv("HUMANONN_SMART_GLOBAL_CAP", "100")),
        smart_sample_outside_pct=float(os.getenv("HUMANONN_SMART_SAMPLE_OUTSIDE_PCT", "0.05")),
        smart_cache_ttl_days=int(os.getenv("HUMANONN_SMART_CACHE_TTL_DAYS", "7")),
        llm_cache_enabled=os.getenv("HUMANONN_LLM_CACHE_ENABLED", "false").lower() == "true",
        llm_cache_ttl_days=int(os.getenv("HUMANONN_LLM_CACHE_TTL_DAYS", "7")),
        github_token=os.getenv("GITHUB_TOKEN"),
        production=os.getenv("HUMANONN_PRODUCTION", "false").lower() == "true",
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


def resolve_prompt_path(filename: str) -> Path:
    if os.getenv("HUMANONN_PRODUCTION", "false").lower() == "true":
        env_path = os.getenv("HUMANONN_PROMPT_PATH")
        if env_path and env_path.endswith(filename):
            return Path(env_path)
        return Path(os.getenv("HUMANONN_PROMPT_PATH_ROOT", "/app/prompts")) / filename
    return Path(__file__).resolve().parents[2] / "prompts" / filename

