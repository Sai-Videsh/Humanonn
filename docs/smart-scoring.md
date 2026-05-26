# Smart Scoring Mode

Humanonn now has two scoring paths.

## Deterministic

Used when `--no-llm` is passed.

Flow:

1. Crawl the site and capture artifacts
2. Run the fixed rule engine
3. Compute the deterministic score

## Smart LLM

Used by default when LLM mode is enabled and provider keys are available.

Flow:

1. Crawl the site and capture artifacts
2. Build the deterministic baseline findings
3. Run fast ambiguity classification on ambiguous signals
4. Run vision analysis on the main screenshot and sampled section screenshots
5. Run embedding similarity against local archetype signatures
6. Run a JSON aggregation model to merge all evidence into:
   - score adjustment
   - signal overrides
   - dynamic findings
   - final summary
7. Save LLM evidence under `reports/data/<scan-id>/`

## Artifacts

Smart scoring writes:

- `llm_evidence.json`
- `smart_summary.json`

inside the scan artifact folder alongside the section/component captures.

## Model Roles

- `fast_ambiguity`: quick signal classification for ambiguous patterns
- `vision`: screenshot understanding
- `embeddings`: similarity against vibe-coded / touched-up / human-built archetypes
- `json_classification`: final smart aggregation and scoring adjustment

