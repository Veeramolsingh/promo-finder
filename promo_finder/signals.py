"""Step 2 - FLAG (plain code, no AI).

Cheap keyword/emotion rules that score how "hooky" each line is (0 to 1).
They are used three ways:
  1. as hints to the AI ("look at these lines"),
  2. as a small part of the final ranking score,
  3. as a no-AI baseline, to check the AI actually adds value.

Rules are deliberately simple so anyone can read and tune them.
"""

from __future__ import annotations

import re

from .parse import Episode, Line

# Physical shocks that stop a scroll (mostly found in ACTION / SOUND lines)
PHYSICAL = [
    "slap", "punch", "strike", "struck", "shove", "push", "grab", "choke", "kiss",
    "kneel", "collapse", "faint", "fall", "blood", "bleed", "knife", "dagger", "sword",
    "sabre", "gun", "pistol", "gunshot", "fire", "burn", "rips", "tears the", "throw",
    "smash", "shatter", "drag", "arrest", "chain", "manacle", "wound", "poison", "dies",
    "corpse", "explod", "crash", "seize", "restrain", "kick", "stab",
]
# Words that signal a reveal, a humiliation or a shocking line
REVEAL = [
    "duke", "heir", "pregnant", "divorce", "dead", "alive", "the truth", "a lie", "liar",
    "never loved", "betray", "married", "imposter", "impostor", "who are you", "how dare",
    "get out", "kneel", "arrest", "worthless", "nothing to offer", "illegitimate",
    "rightful", "murder", "stole", "secret", "beg", "humiliat",
]
# Speaker tone tags that mean high emotion
INTENSE_TONES = [
    "scream", "shout", "furious", "outrage", "hysteric", "shock", "gasp", "sob", "cry",
    "horrif", "venom", "cruel", "desperate", "enrag", "terrif", "mock", "rage",
    "threat", "seething", "panic", "anguish", "livid",
]


def _has_any(text: str, words: list[str]) -> list[str]:
    t = text.lower()
    return [w for w in words if re.search(r"\b" + re.escape(w), t)]


def line_signal(line: Line) -> tuple[float, list[str]]:
    """Return (score 0-1, reasons) for one line."""
    if line.kind == "camera":
        return 0.0, []
    score, why = 0.0, []
    phys = _has_any(line.text, PHYSICAL)
    if phys and line.kind in ("action", "sfx"):
        score += 0.5
        why.append("physical: " + ", ".join(phys[:3]))
    rev = _has_any(line.text, REVEAL)
    if rev:
        score += 0.3
        why.append("reveal/shock words: " + ", ".join(rev[:3]))
    if line.kind == "dialogue":
        tones = _has_any(line.tone or "", INTENSE_TONES)
        if tones:
            score += 0.3
            why.append("intense tone: " + (line.tone or ""))
        if "!" in line.text:
            score += 0.1
            why.append("exclamation")
    return min(score, 1.0), why


def episode_hints(ep: Episode, top_n: int = 6) -> list[dict]:
    """Top-scoring lines, given to the AI as hints (not requirements)."""
    scored = []
    for l in ep.content_lines:
        s, why = line_signal(l)
        if s > 0:
            scored.append({"line": l.idx, "score": round(s, 2), "why": "; ".join(why)})
    scored.sort(key=lambda d: -d["score"])
    return scored[:top_n]


def hook_signal_at(ep: Episode, start: float, window: float = 3.0) -> float:
    """Best rule score among lines that begin in the first `window` seconds of a clip."""
    best = 0.0
    for l in ep.content_lines:
        if start - 0.05 <= l.start <= start + window:
            best = max(best, line_signal(l)[0])
    return best
