# Humanonn — Complete Project Details

> **Tagline:** Make it feel human.

---

## 1. What It Is

Humanonn is a developer tool that detects whether a website looks "vibe coded" (AI-generated via platforms like Bolt, Lovable, v0, etc.) and tells developers exactly what makes it look that way — with a Vibe Score, per-signal breakdown, and actionable fix suggestions.

The core insight: vibe-coded sites don't just share visual aesthetics. They share structural fingerprints — component library defaults that survive even when a developer tries to touch up the output. Humanonn detects those fingerprints.

---

## 2. Core User Flow

```
User pastes a live deployed URL
        ↓
Playwright (headless browser) crawls the homepage
        ↓
Rule-based engine scores against 50 vibe-coded signals
        ↓
Groq LLM handles ambiguous / uncertain cases as fallback
        ↓
Output: Vibe Score (0–100) + Humanness Score (inverse) + per-signal breakdown + fix suggestions + full-page screenshot
```

---

## 3. The Detection Problem

### Why 100% accuracy is impossible

Humanonn detects **aesthetic and structural patterns**, not git history. The line between "vibe coded" and "human built" is genuinely blurry:

- A senior dev can ship a site that looks identical to a Bolt output
- A vibe coder can spend 3 hours fixing every Tier 4 signal
- A human dev might deliberately choose glassmorphism + dark theme as a design choice

**This is fine.** Humanonn doesn't need to be a lie detector. It needs to be a **quality mirror.**

### The real value proposition

> "This site has 11 signals that make it feel unpolished and untrustworthy to users — here's exactly what they are and how to fix them."

That's useful whether the site was vibe coded, agency built, or hand crafted by a senior dev who was rushing. The "vibe coded" framing is the hook that makes it shareable and memorable. The actual product is a **polish checker.**

### Honest output framing

Don't say: *"This site WAS vibe coded."*

Say: *"This site scores 78 on our Vibe Index — it shares 14 patterns commonly found in AI-generated sites."*

That's defensible, accurate, and still punchy.

---

## 4. The Core Detection Insight — Signal Clustering

The real differentiator is not any single signal. It's **signal clustering.**

A human developer who chooses glassmorphism makes one deliberate choice. They'll do everything else intentionally too — custom easing, proper focus states, tight microcopy, consistent spacing.

A vibe-coded site has glassmorphism **and** pill buttons **and** mesh gradient **and** Inter-only font **and** missing focus states **and** generic CTAs. They cluster because they all came from the same template defaults.

### Three categories of sites

| Site Type | Signal Pattern |
|---|---|
| Fully vibe coded | 15+ signals, heavily clustered across all tiers |
| Vibe coded + dev touched | 8–14 signals, Tier 1–2 mostly intact, Tier 3–4 partially fixed |
| Human built with similar aesthetics | 3–6 signals, scattered, no Tier 4 failures |

**Tier 4 signals are the strongest discriminator.** A real developer almost never ships with broken autofill on dark mode, missing `:active` states, and `outline: none` on inputs simultaneously. That combination is a vibe code fingerprint.

### Signals that humans almost always get right

These are the ones that separate deliberate design from template output:

- Focus states — any accessibility-aware dev adds these
- Spacing rhythm — humans feel when it's off
- Active/pressed states on buttons — handwritten UI always has this
- Font pairing — humans almost always pair a display font with body
- Microcopy — "Get started" and "Learn more" together is a near-certain tell

If a site has glassmorphism BUT has all of these correct — it's almost certainly human built. Score it low.

---

## 5. Signal Detection Architecture

### Two types of signals

#### Type 1 — Measurable / Deterministic
These have exact computed values. Pure rule engine. No LLM needed. Fast, free, reliable.

Examples:
- `border-radius >= 24px` on buttons → pill flag
- `backdropFilter` contains `blur` → glassmorphism flag
- `backgroundImage` has 3+ `radial-gradient()` calls → mesh gradient flag
- Font family is exactly `Inter` or `Geist` and nothing else → flag
- `outline: none` on inputs with no replacement → focus state missing
- `transition` is `none` or missing on interactive elements → flag
- `letter-spacing: 0` on uppercase text → flag

#### Type 2 — Structural / Visual Patterns
These need interpretation. LLM earns its place here.

Examples:
- Is the hero background "aurora-like"? (could be custom gradient that isn't technically multi-radial)
- Is the layout a "bento grid" or just a normal CSS grid?
- Are the icons "all same weight and color" or intentional design consistency?
- Does the copy feel generic? ("Get started", "Learn more" — but what if it's a legit minimal brand?)
- Is the section rhythm repetitive or just well-structured?

### Why computed styles, not source inspection

Don't parse CSS files. Use Playwright to query `getComputedStyle()` on actual rendered elements. Even if the dev changed the Tailwind config or overrode variables, you're reading what the browser actually painted.

```js
// Pill button detection
const borderRadius = await page.evaluate(() => {
  const btn = document.querySelector('button, [class*="btn"]');
  return getComputedStyle(btn).borderRadius;
});
// "9999px" or "50px" = pill. Flag it.

// Glassmorphism detection
const hasGlass = await page.evaluate(() => {
  const cards = document.querySelectorAll('[class*="card"], section > div');
  return [...cards].some(el =>
    getComputedStyle(el).backdropFilter.includes('blur')
  );
});

// Mesh gradient detection
const bg = await page.evaluate(() =>
  getComputedStyle(document.body).backgroundImage
);
// Contains multiple radial-gradient() calls = mesh gradient flag
```

### Signal weight by type

Color signals (purple accent, gradient text) are **fragile** — easy for a dev to change. Weight them lower.

Geometry + layout signals (border-radius, backdrop-filter, bento grid, section rhythm) are **resistant to touch-ups**. Devs rarely restructure layout even when they change colors. Weight these 2x.

Tier 4 failures (broken autofill, missing focus states, no active states) are the **strongest proof of vibe code origin** — no careful human ships these. Weight them highest.

---

## 6. The 50-Signal Detection Checklist

### Tier 1 — Instantly Obvious (70–90% fix contribution)

1. Purple/violet as default accent color
2. Pill-shaped buttons everywhere (`border-radius: 9999px`)
3. Mesh gradient / aurora hero background (purple-blue-pink blobs)
4. Glassmorphism cards with frosted `backdrop-filter: blur`
5. Dark mode as default with no light variant

### Tier 2 — Designers Notice (45–70% fix contribution)

6. Gradient text on hero headline (`background-clip: text`)
7. Bento grid feature section layout
8. "✦ Now in beta" badge floating above `h1`
9. Dot/grid pattern overlay on hero section
10. Glow/halo `box-shadow` on CTA buttons
11. Same section rhythm repeated every scroll (Hero→Features→Pricing→CTA)
12. Inter or Geist as the only font, no display font pairing
13. *(spare signal slot)*

### Tier 3 — Users Feel It, Can't Explain It (20–45% fix contribution)

14. No hover states on feature cards
15. Icons all same weight, size, and color (Lucide 20px, stroke-width 1.5)
16. Generic CTAs — "Get started" and "Learn more" everywhere
17. No scroll-triggered entrance animations
18. Logo is just product name in bold Inter
19. Every heading is centered, no left-aligned variation
20. Inconsistent spacing rhythm (broken 8px grid)
21. No loading/skeleton states
22. Dividers between every section instead of whitespace
23. No `cursor: pointer` on clickable cards
24. Social proof logos all grayscale at 40% opacity
25. Footer is four identical columns with bullet links

### Tier 4 — Deep Friction (5–20% fix contribution, but invisible when right and jarring when wrong)

26. No focus states on inputs (`outline: none` with no replacement)
27. Floating labels don't exist, placeholder disappears on focus
28. Button has no active/pressed state (`:active` missing)
29. Images are all Unsplash stock photos
30. Line-height too tight on body copy (1.4 instead of 1.6–1.75)
31. Letter-spacing zero on uppercase labels (FEATURES, PRICING)
32. Max-width too wide or too narrow for reading comfort
33. No transition on theme/state changes
34. Color contrast technically passing but feels wrong
35. Nav has no active/current page indicator
36. Animations are linear, no custom easing (`cubic-bezier`)
37. Scrollbar is browser default
38. Selection color is browser default blue (`::selection` not overridden)
39. No empty state design
40. Tooltips use native browser `title` attribute
41. Font size doesn't scale on mobile (no `clamp()` or fluid type)
42. No system for disabled states (just `opacity: 0.5`)
43. Images have no meaningful alt text
44. Link underlines always on or always off, no hover transition
45. No custom 404 page
46. Paragraph max-width not constrained (lines too long to read)
47. No favicon
48. Modals don't trap focus (keyboard users get lost)
49. No page transition between routes
50. Input autofill styles break dark mode (Chrome yellow on dark bg)

*(Additional: body background color doesn't extend past content)*

---

## 7. Top 7 Priority Signals to Auto-Fix

Selected on uniqueness, user discomfort, and trust impact — not just fix contribution:

| Priority | Signal | Why It Matters |
|---|---|---|
| 1 | Button active/pressed state missing | Users tap and nothing confirms. Destroys trust in the action. |
| 2 | Linear animations, no easing | Motion feels mechanical. Can auto-swap to cubic-bezier presets (spring, smooth, snappy). |
| 3 | Input autofill breaks dark mode | Chrome yellow autofill on dark UI looks completely broken. One webkit CSS override fixes it. |
| 4 | Inconsistent spacing rhythm | Nothing obviously wrong but everything feels off. Humanonn picks from 3–5 rhythm presets. |
| 5 | No focus states on inputs | Missing focus ring is an accessibility failure that sighted users also feel. |
| 6 | Letter-spacing zero on uppercase labels | Makes FEATURES and PRICING look cramped and cheap. One CSS line fixes it entirely. |
| 7 | Selection color is browser default blue | Highlighting text on dark purple site shows OS-blue. Breaks brand immersion. One-line fix. |

---

## 8. What Humanonn Can and Cannot Auto-Fix

### Can fix automatically (MVP post-scan)

- Swap linear easing to `cubic-bezier` presets
- Apply spacing rhythm presets (Tight / Airy / Editorial / Startup / Calm)
- Inject `::selection` color override
- Inject input autofill dark mode override
- Add `:active` scale/brightness on buttons
- Add focus ring styles on inputs
- Add `letter-spacing` to uppercase text elements
- Add `cursor: pointer` to interactive elements
- Override scrollbar styles
- Fix `border-radius` on buttons (pill → slightly rounded)

### Cannot fix automatically (needs human decision)

- Custom animations from scratch
- Logo design
- Real photography replacing stock images
- Microcopy / CTA text rewriting
- Font pairing decisions
- Brand color palette

---

## 9. Scoring Architecture

### Base score

```
base_score = sum of individual signal weights
```

### Cluster multiplier

```
cluster_bonus:
  + 15  if tier1_count >= 3
  + 20  if tier4_count >= 4
  + 10  if tier1 AND tier4 both triggered

final_vibe_score = base_score + cluster_bonus (capped at 100)
humanness_score = 100 - final_vibe_score
```

### Signal weighting by tier

| Tier | Weight per signal | Rationale |
|---|---|---|
| Tier 1 | 1x | Easy to change, may be intentional choice |
| Tier 2 | 1.5x | Layout patterns survive touch-ups |
| Tier 3 | 1.5x | Users feel these, cluster strongly |
| Tier 4 | 2x | Strongest proof of vibe code origin |

---

## 10. Architecture — Full Stack

### Detection pipeline

```
Playwright crawls live URL (headless, homepage only for MVP)
        ↓
Extracts: computed styles, hover states, focus states,
          transitions, fonts, colors, border-radius,
          layout structure, DOM patterns, screenshot
        ↓
Rule Engine → scores Type 1 signals (deterministic)
        ↓
Ambiguous Type 2 signals → batched into single Groq call
        ↓
Groq returns: flagged / not flagged + reason per signal (JSON)
        ↓
Score aggregation → Vibe Score + Humanness Score
        ↓
Frontend displays: score, per-signal breakdown, severity, fix description, screenshot
```

### Tech stack

| Layer | Tool | Cost |
|---|---|---|
| Crawler | Playwright (Python or JS) | Free |
| Backend | FastAPI (Python) or Express (JS) | Free |
| Frontend | Plain HTML/CSS or Next.js | Free |
| AI model | Groq (free tier) — fallback only | Free |
| Hosting | Vercel + Railway free tier | Free |
| Database | None for MVP | — |

### Groq usage strategy

Rule-based engine runs first on all 50 signals. Groq is called **only** as a fallback for ambiguous or uncertain Type 2 detections — keeping it fast and within free tier limits.

Don't send all 50 signals to Groq. Only the ~12–15 Type 2 signals. Batch into a single prompt per scan. Target: 1–2 Groq API calls per scan maximum.

### What to send Groq (structured, not full DOM)

```
Here is a website audit snapshot:
- Background image value: [computed value]
- Section count and order: Hero, Features, Pricing, CTA, Footer
- CTA button texts found: ["Get Started", "Learn More", "Try for free"]
- Icon sizes detected: all 20px, stroke-width 1.5
- Font pairings detected: Inter only

Flag which of these patterns suggest AI-generated / vibe-coded design.
Return JSON only: { signal_name: { flagged: bool, reason: string } }
```

### Groq response handling

```js
const data = await response.json();
const fullResponse = data.content
  .map(item => (item.type === "text" ? item.text : ""))
  .filter(Boolean)
  .join("\n");

const clean = fullResponse.replace(/```json|```/g, "").trim();
const parsed = JSON.parse(clean);
```

---

## 11. MVP Scope

### What it does

- User pastes a live URL
- Playwright crawls homepage only (single page)
- Rule engine scores against all 50 signals
- Groq handles edge cases for Type 2 signals
- Displays: Vibe Score (0–100), Humanness Score (inverse), per-signal breakdown with severity and fix description
- Full-page screenshot of crawled site shown alongside results

### What it does NOT do (post-MVP)

- Multi-page crawl
- Actual code patching / auto-fix in IDE
- User accounts or auth
- Payment
- Database persistence

---

## 12. Build Plan

### Day 1

- Playwright script capturing computed styles, hover states, focus states, transitions, fonts, colors, border-radius — 2–3 hours
- Rule engine scoring each signal — 1–2 hours
- FastAPI/Express endpoint connecting crawler to rules — 1 hour

### Day 2

- Frontend UI showing results cleanly — 3–4 hours
- Connect frontend to backend — 1 hour
- Test on 3–4 real Bolt/Lovable generated sites — 30 mins
- Record demo video as backup — 30 mins

---

## 13. The Demo Moment

Run Humanonn on a real Lovable or Bolt-generated site. Show:

> **"81% vibe coded — 12 specific patterns flagged"**

With the full breakdown visible. That's the pitch.

---

## 14. What Makes Humanonn the Breakthrough

The bottom 20 signals (Tier 4) are **invisible when done right and jarring when wrong.** They're all automatable in one pass. None require the developer to make design decisions.

Humanonn fixes what vibe coders never think about — and users always feel.

The insight that makes this defensible: vibe-coded platforms generate components with hardcoded structural defaults. A developer changing the *color* doesn't change the `border-radius: 9999px`. They'd have to explicitly override it. Most don't. So geometry + layout signals are highly resistant to touch-ups — and that's what Humanonn detects.

---

*Document version: MVP scope — hackathon submission*


## 10. Architecture — Agent-Based (Not a Static Pipeline)

Humanonn is built as an AI agent, not a fixed pipeline.
The difference: the agent decides which tools to call, in what order,
and how deep to go — based on what it finds at each step.

### What makes it an agent

- **Planning** — decides what to inspect next based on current findings
- **Tool use** — calls different Playwright/analysis tools based on that plan  
- **Feedback loop** — results of one tool call inform the next action
- **Adaptive depth** — goes deeper on suspicious signals, skips irrelevant checks

### Agent orchestration

Groq (LLaMA 3) is the orchestrator. It receives the URL, has access to
a set of tools, and decides the execution path dynamically.
User submits URL
↓
Agent receives URL + system prompt
↓
Agent decides: "Crawl the page first"
→ calls crawl_page(url)
↓
Agent sees: "Inter font, dark bg, multiple radial gradients detected"
Agent decides: "Check button shapes and focus states specifically"
→ calls inspect_elements(selectors=["button", "input", "a"])
↓
Agent sees: pill buttons confirmed, no focus rings
Agent decides: "Layout patterns ambiguous, check section structure"
→ calls analyze_layout()
↓
Agent sees: bento grid + Hero→Features→Pricing→CTA rhythm confirmed
Agent decides: "Enough signal. Generate report."
→ calls generate_report(signals)
↓
Vibe Score + breakdown output

### Tool definitions (what the agent can call)

| Tool | What it does |
|---|---|
| `crawl_page(url)` | Playwright crawl, returns computed styles, fonts, colors, bg |
| `inspect_elements(selectors)` | Deep dive specific DOM elements for border-radius, focus, active states |
| `analyze_layout()` | Section structure, grid patterns, heading alignment, section rhythm |
| `check_accessibility()` | Focus states, alt text, ARIA, autofill, keyboard traps |
| `analyze_copy(text_content)` | CTA language, heading copy, badge text, generic pattern detection |
| `generate_report(signals)` | Aggregates all signals, computes Vibe Score, formats output |

### Agent system prompt
You are a website quality auditor detecting vibe-coded (AI-generated) design patterns.
You have tools to crawl and inspect live websites. Use them strategically:

Start with crawl_page() on every scan
If you find strong Tier 1 signals (pill buttons, mesh gradients, glassmorphism),
go deeper with inspect_elements() and check_accessibility()
If the site looks human-built after initial crawl, confirm with
check_accessibility() before concluding — Tier 4 failures are the strongest signal
Use analyze_copy() when CTA text is ambiguous
Call generate_report() only when you have enough signal to be confident

Think step by step. Explain what you're checking and why.
Return findings as JSON from generate_report().

### Agent loop implementation (Groq function calling)

```python
messages = [{"role": "user", "content": f"Audit this site: {url}"}]

while True:
    response = groq.chat.completions.create(
        model="llama3-70b-8192",
        messages=messages,
        tools=tool_definitions,
        tool_choice="auto"
    )

    if response.choices[0].finish_reason == "stop":
        # Agent is done — extract final report
        break

    # Agent called a tool — execute it
    tool_call = response.choices[0].message.tool_calls[0]
    tool_result = execute_tool(tool_call.function.name, tool_call.function.arguments)

    # Feed result back to agent
    messages.append(response.choices[0].message)
    messages.append({
        "role": "tool",
        "tool_call_id": tool_call.id,
        "content": json.dumps(tool_result)
    })
```

### What the agent gains over a static pipeline

- Adaptive depth — more tool calls on suspicious sites, fewer on clean ones
- Can handle edge cases not pre-programmed (investigates rather than pattern matches)
- Explains its reasoning step by step
- More accurate on "touched up" vibe-coded sites — pursues signals that
  a fixed pipeline would miss after initial checks pass
- Naturally handles the three site categories differently:
  - Fully vibe coded → agent confirms fast, goes wide on Tier 4
  - Touched up → agent digs deeper, finds surviving structural fingerprints
  - Human built → agent checks accessibility to confirm, exits early

### What stays the same

The 50-signal checklist, tier weights, cluster multiplier, and scoring
formula are all unchanged. The agent is just the thing that decides
*which* of those checks to run, *when*, and *how deep* — instead of
running all of them in a fixed order every time.

---

**Where to insert it:** Replace the current Section 10 entirely with this block. Sections 11 onward stay unchanged.

The only line to update in Section 12 (Build Plan) — Day 1 becomes:

```markdown
- Define tool schemas for all 6 agent tools — 1 hour
- Implement each tool as a Playwright function — 2–3 hours  
- Wire Groq function calling loop — 1–2 hours
```

That's it.