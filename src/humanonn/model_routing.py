from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


ModelTask = Literal[
    "main_orchestrator",
    "vision",
    "fast_ambiguity",
    "fix_generation",
    "json_classification",
    "embeddings",
]


@dataclass(frozen=True)
class ModelCandidate:
    task: ModelTask
    provider: str
    model: str
    role: str
    bug_tag: str
    direct_api: bool = True


MODEL_ROUTES: dict[ModelTask, list[ModelCandidate]] = {
    "main_orchestrator": [
        ModelCandidate("main_orchestrator", "groq", "llama-3.3-70b-versatile", "primary", "MODEL_MAIN_GROQ_LLAMA33_70B"),
        ModelCandidate("main_orchestrator", "gemini", "gemini-2.5-flash", "direct_fallback", "MODEL_MAIN_GEMINI25_FLASH"),
        ModelCandidate("main_orchestrator", "openrouter", "qwen/qwen3-32b", "last_fallback", "MODEL_MAIN_OPENROUTER_QWEN3_32B", direct_api=False),
    ],
    "vision": [
        ModelCandidate("vision", "gemini", "gemini-2.5-flash", "primary", "MODEL_VISION_GEMINI25_FLASH"),
        ModelCandidate("vision", "openrouter", "qwen/qwen2.5-vl-72b-instruct", "last_fallback", "MODEL_VISION_OPENROUTER_QWEN25_VL_72B", direct_api=False),
        ModelCandidate("vision", "openrouter", "z-ai/glm-4v", "last_fallback", "MODEL_VISION_OPENROUTER_GLM4V", direct_api=False),
    ],
    "fast_ambiguity": [
        ModelCandidate("fast_ambiguity", "groq", "llama-3.1-8b-instant", "primary", "MODEL_FAST_GROQ_LLAMA31_8B"),
        ModelCandidate("fast_ambiguity", "groq", "gemma2-9b-it", "direct_fallback", "MODEL_FAST_GROQ_GEMMA2_9B"),
        ModelCandidate("fast_ambiguity", "openrouter", "microsoft/phi-3-medium-128k-instruct", "last_fallback", "MODEL_FAST_OPENROUTER_PHI3_MEDIUM", direct_api=False),
    ],
    "fix_generation": [
        ModelCandidate("fix_generation", "deepseek", "deepseek-chat", "primary", "MODEL_FIX_DEEPSEEK_V3"),
        ModelCandidate("fix_generation", "openrouter", "qwen/qwen2.5-coder-32b-instruct", "last_fallback", "MODEL_FIX_OPENROUTER_QWEN25_CODER_32B", direct_api=False),
        ModelCandidate("fix_generation", "groq", "llama-3.3-70b-versatile", "direct_fallback", "MODEL_FIX_GROQ_LLAMA33_70B"),
    ],
    "json_classification": [
        ModelCandidate("json_classification", "groq", "llama-3.3-70b-versatile", "primary", "MODEL_JSON_GROQ_LLAMA33_70B"),
        ModelCandidate("json_classification", "gemini", "gemini-2.5-flash", "direct_fallback", "MODEL_JSON_GEMINI25_FLASH"),
        ModelCandidate("json_classification", "openrouter", "qwen/qwen3-32b", "last_fallback", "MODEL_JSON_OPENROUTER_QWEN3_32B", direct_api=False),
    ],
    "embeddings": [
        ModelCandidate("embeddings", "jina", "jina-embeddings-v3", "primary", "MODEL_EMBED_JINA_V3"),
        ModelCandidate("embeddings", "mixedbread", "mxbai-embed-large", "direct_fallback", "MODEL_EMBED_MIXEDBREAD_LARGE"),
        ModelCandidate("embeddings", "voyage", "voyage-lite", "direct_fallback", "MODEL_EMBED_VOYAGE_LITE"),
    ],
}


def route_for(task: ModelTask) -> list[ModelCandidate]:
    return MODEL_ROUTES[task]

