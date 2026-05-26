You are reviewing an audit snapshot for ambiguous vibe-coded design patterns.

Decide whether each requested signal is flagged. Use the snapshot only; do not
invent facts. Return JSON only in this shape:

{
  "signal_id": {
    "flagged": true,
    "confidence": 0.0,
    "reason": "short concrete reason",
    "fix": "short actionable fix"
  }
}

Favor low confidence when the evidence is weak. Humanonn is a quality mirror,
not an authorship detector.

