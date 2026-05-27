# Humanonn — Scoring Architecture Brief (Continuation Prompt)

> Context: A signals markdown file (`humanonn_signals.md`) has already been provided with all 91 signals, their tiers, and bucket classifications. This document explains the full scoring architecture to implement. Only touch scoring-related files — do not modify crawler, agent orchestration, or frontend.

---

## 1. Two Buckets

Every signal belongs to one of two buckets:

- **Origin** — proves the site was AI-generated. Strong evidence of vibe coding.
- **Polish** — any developer (human or AI) might miss. Measures finish quality, not origin.

Each bucket has a score multiplier applied on top of tier base points:
- Origin = **1.5×**
- Polish = **1.0×**

---

## 2. Tier Base Points

| Tier | Base Points | What it represents |
|---|---|---|
| Tier 1 | 10 | Loudest AI fingerprints |
| Tier 2 | 6 | Strong visual AI patterns |
| Tier 3 | 3 | Human-touch gaps |
| Tier 4 | 2 | Deep polish gaps |

---

## 3. Per-Signal Score Formula

```
signal_contribution = tier_base_points × bucket_multiplier × confidence
```

Where `confidence` is a float 0.0–1.0 output by each rule. Deterministic rules output 1.0 or a ratio (e.g. 2/3 buttons flagged = 0.67). Ambiguous rules output a heuristic float, confirmed by Groq if borderline.

---

## 4. Deterministic vs Probabilistic

Each signal has a detection mode defined in the signals file:

- **`deterministic`** — computed directly from DOM/CSS values. Confidence derived from ratio of flagged elements (e.g. 3/3 pill buttons = 1.0, 2/3 = 0.67). No LLM involved.
- **`ambiguous`** — heuristic confidence score. If that signal's confidence lands between 0.4–0.7, pass it to Groq for a second-opinion adjustment only on that signal.

Do not send all signals to Groq — only ambiguous ones with uncertain confidence.

---

## 5. Normalisation

Max possible raw score across all 91 signals (from signals file) = **347.5**

```python
normalised_score = clamp(0, 100, round((raw_score / 347.5) * 100))
vibe_score = normalised_score
humanness_score = 100 - vibe_score
```

---

## 6. Cluster Bonus

Apply after raw sum, before normalisation:

```python
if tier1_flagged_count >= 3:
    raw_score += 15
if tier1_flagged_count >= 1 and tier4_flagged_count >= 1:
    raw_score += 10
```

Remove the old `+20 if 4 Tier 4 signals flagged` rule — it was directionally wrong (Tier 4 flags indicate polish neglect, not stronger AI origin).

---

## 7. LLM Adjustment (Smart Scoring Mode)

Groq final adjustment applies only when `normalised_score` is between **38–52** (the genuine borderline zone). Outside this range, `llm_adjustment = 0`.

```python
if 38 <= normalised_score <= 52:
    llm_adjustment = get_groq_adjustment(signals)  # returns -15 to +15
else:
    llm_adjustment = 0

vibe_score = clamp(0, 100, normalised_score + llm_adjustment)
```

---

## 8. Category Classification

Derived from final `vibe_score` after LLM adjustment:

```python
def get_category(vibe_score):
    if vibe_score >= 65:
        return "Fully Vibe Coded"
    elif vibe_score >= 40:
        return "Slight Dev Effort"
    elif vibe_score >= 16:
        return "Decent Dev Effort"
    else:
        return "Human Built"
```

Category is not hardcoded separately — it is always derived from score.

---

## 9. What to Change in the Project

| What | Where | Change |
|---|---|---|
| Signal weights | signal definitions / config | Replace flat weights with `tier_base × bucket_multiplier` per signal |
| Normalisation divisor | `scoring.py` | Change to 347.5 |
| Cluster bonus | `scoring.py` | Remove Tier 4 `+20` bonus, keep the other two |
| Category thresholds | `scoring.py` | Add `get_category()` function using 65/40/16 thresholds |
| Groq trigger condition | `smart_scoring.py` | Wrap LLM call in `if 38 <= normalised_score <= 52` |
| Ambiguous signal Groq calls | rule engine | Only pass signals with mode=`ambiguous` and confidence 0.4–0.7 to Groq |

---

Do not touch: crawler, agent tool definitions, frontend, report generation, or any signal's detection logic. Only scoring math and classification.

