# Humanonn Build Stages

## Stage 1: CLI Agent Skeleton and Deterministic Baseline

Goal: make the whole project shape real before adding UI or persistence.

Milestones:

- Define the agent contract, tool schemas, system prompts, and report prompt assets.
- Implement the CLI flow: paste URL, crawl homepage, analyze, score, print report.
- Build Playwright extraction tools for rendered styles, DOM structure, copy, accessibility hints, and screenshots.
- Implement the 50-signal registry with deterministic evaluators for high-confidence signals and placeholder metadata for the rest.
- Wire Groq function-calling orchestration as an optional path, with deterministic fallback when no API key is configured.
- Add JSON report output so future UI/API layers can consume the same result.

## Stage 2: Accuracy Pass on the Full Signal Set

Goal: turn placeholders into reliable detectors and validate against real sites.

Milestones:

- Expand deterministic coverage across all measurable signals.
- Add focused Type 2 ambiguity payloads for Groq instead of sending raw DOM.
- Add fixtures and regression tests for known vibe-coded, touched-up, and human-built examples.
- Improve confidence scoring and reasons for each flagged signal.

## Stage 3: Backend API

Goal: expose the same agent through a service boundary.

Milestones:

- Add FastAPI scan endpoint using the existing CLI internals.
- Add request validation, timeout handling, screenshot serving, and JSON response contracts.
- Add worker-safe browser lifecycle handling.
- Add basic rate limiting and error taxonomy.

## Stage 4: Report UI

Goal: make the results inspectable and shareable.

Milestones:

- Build a lightweight frontend that posts a URL and renders score, screenshot, and per-signal breakdown.
- Add severity grouping, fix suggestions, and agent reasoning.
- Add copy/export/share affordances.

## Stage 5: Auto-Fix Suggestions and Patch Packs

Goal: convert findings into actionable implementation help.

Milestones:

- Generate CSS patch snippets for the top seven auto-fixable signals.
- Add framework-aware guidance for Tailwind, plain CSS, and component libraries.
- Add before/after explanation blocks without editing user code automatically.

