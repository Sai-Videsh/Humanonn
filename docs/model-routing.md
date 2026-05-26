# Model Routing Plan

Humanonn should spend inference budget in layers. Groq handles most repetitive
agent work. Premium or rate-limited providers are reserved for tasks where they
add clear value.

## Routing Policy

| Task Section | Primary | Direct Fallbacks | Last Fallback | Bug Tags |
|---|---|---|---|---|
| Main orchestrator / reasoning | Groq Llama 3.3 70B | Google Gemini 2.5 Flash | OpenRouter Qwen 3 32B | `MODEL_MAIN_*` |
| Vision / screenshot understanding | Google Gemini 2.5 Flash Vision | OpenRouter Qwen2.5 VL 72B | OpenRouter GLM-4V | `MODEL_VISION_*` |
| Fast ambiguity checking | Groq Llama 3.1 8B Instant | Groq Gemma 2 9B | OpenRouter Phi-3 Medium | `MODEL_FAST_*` |
| CSS / Tailwind / fix generation | DeepSeek V3 | Qwen2.5 Coder 32B via OpenRouter | Groq Llama 3.3 70B | `MODEL_FIX_*` |
| Structured JSON / classification | Groq Llama 3.3 70B | Google Gemini 2.5 Flash | OpenRouter Qwen 3 32B | `MODEL_JSON_*` |
| Embeddings / similarity | Jina embeddings v3 | Mixedbread mxbai-embed-large | Voyage lite | `MODEL_EMBED_*` |

## Cost Discipline

- Groq should handle roughly 70-85% of requests.
- Gemini/OpenRouter/DeepSeek should be called only for high-value cases:
  visual reasoning, complex UX interpretation, long-context analysis, final
  polished summaries, uncertain detections, or CSS patch generation.
- OpenRouter routes are last fallback unless the target task has no direct API
  available in the current runtime.
- Every model candidate has a stable bug tag. Logs and report notes should use
  that tag when a provider fails so failures are easy to isolate.

## Future Implementation Notes

- Stage 2 should wire fast ambiguity checks through the `fast_ambiguity` route.
- Stage 2 or 3 should add provider clients for Gemini, OpenRouter, DeepSeek,
  and Jina instead of putting HTTP calls inside detectors.
- Vision routes should receive the screenshot path plus a compact deterministic
  snapshot, not full DOM.
- Fix generation should receive only flagged findings and framework context.
- Embeddings should be used for similarity against known site archetypes and
  repeated copy/layout patterns, not for the primary scan loop.

