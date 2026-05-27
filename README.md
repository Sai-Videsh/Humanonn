# Humanonn

Humanonn is a CLI-first AI agent project that audits deployed websites for design and UX patterns commonly found in AI-generated or "vibe-coded" sites.

Stage 1 builds the full project skeleton and a usable deterministic scanner:

- Accepts a deployed URL from the command line
- Crawls the homepage with Playwright
- Extracts rendered styles, copy, structure, accessibility hints, and a screenshot
- Runs the 50-signal rule engine scaffolding
- Computes Vibe Score and Humanness Score
- Routes through configured model providers, with Groq as the primary low-cost inference layer

## Setup

```bash
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
python -m playwright install chromium
```

Configure providers in `.env`:

```bash
copy .env.example .env
```

Model routing and fallback policy lives in [docs/model-routing.md](docs/model-routing.md).

## Run

```bash
python -m humanonn scan https://example.com
```

Save JSON:

```bash
python -m humanonn scan https://example.com --json reports/example.json
```

## Project Stages

See [docs/stages.md](docs/stages.md).
