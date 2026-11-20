"""Step 2 - FLAG (plain code, no AI).

Cheap keyword/emotion rules that score how "hooky" each line is (0 to 1).
They are used three ways:
  1. as hints to the AI ("look at these lines"),
  2. as a small part of the final ranking score,
  3. as a no-AI baseline, to check the AI actually adds value.

The built-in word lists are genre-neutral: they hold words that signal shock in any drama
(a slap, a divorce, "who are you"). Words that only matter in one show (a secret duke, a
billionaire CEO, an alpha wolf) are learned per show from its episode summaries; see
show_words.py. Rules are deliberately simple so anyone can read and tune them.
"""

from __future__ import annotations

import re

from .parse import Episode, Line

# Physical shocks that stop a scroll (matched in ACTION / SOUND lines)
PHYSICAL = [
    "slap", "punch", "hit", "strike", "struck", "shove", "push", "grab", "choke", "kiss",
    "kneel", "collapse", "faint", "fall", "blood", "bleed", "knife", "dagger", "sword",
    "gun", "pistol", "gunshot", "shot", "fire", "burn", "rips", "tears the", "tear up", "throw",
    "splash", "spill", "smash", "shatter", "drag", "arrest", "handcuff", "chain", "wound",
    "poison", "dies", "corpse", "explod", "crash", "seize", "restrain", "kick", "stab", "fight",
]
# Words that signal a reveal, a humiliation or a shocking line in almost any drama
REVEAL = [
    "pregnant", "baby", "divorce", "dead", "alive", "the truth", "a lie", "liar", "never loved",
    "betray", "cheat", "affair", "married", "who are you", "how dare", "get out", "kneel",
    "beg", "worthless", "nothing to offer", "trash", "murder", "kill", "stole", "steal",
    "secret", "humiliat", "fired", "expose", "revenge", "disown", "kicked out", "arrest",
]
# Speaker tone tags that mean high emotion
INTENSE_TONES = [
    "scream", "shout", "furious", "outrage", "hysteric", "shock", "gasp", "sob", "cry",
    "horrif", "venom", "cruel", "desperate", "enrag", "terrif", "mock", "rage",
    "threat", "seething", "panic", "anguish", "livid",
]


def _has_any(text: str, words) -> list[str]:
    t = text.lower()
    return [w for w in words if re.search(r"\b" + re.escape(w), t)]


def line_signal(line: Line, show_words: tuple[str, ...] = ()) -> tuple[float, list[str]]:
    """Return (score 0-1, reasons) for one line. `show_words` are this show's own reveal words."""
    if line.kind == "camera":
        return 0.0, []
    score, why = 0.0, []
    phys = _has_any(line.text, PHYSICAL)
    if phys and line.kind in ("action", "sfx"):
        score += 0.5
        why.append("physical: " + ", ".join(phys[:3]))
    rev = _has_any(line.text, REVEAL) + _has_any(line.text, show_words)
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


def episode_hints(ep: Episode, top_n: int = 6, show_words: tuple[str, ...] = ()) -> list[dict]:
    """Top-scoring lines, given to the AI as hints (not requirements)."""
    scored = []
    for l in ep.content_lines:
        s, why = line_signal(l, show_words)
        if s > 0:
            scored.append({"line": l.idx, "score": round(s, 2), "why": "; ".join(why)})
    scored.sort(key=lambda d: -d["score"])
    return scored[:top_n]


def hook_signal_at(ep: Episode, start: float, window: float = 3.0,
                   show_words: tuple[str, ...] = ()) -> float:
    """Best rule score among lines that begin in the first `window` seconds of a clip."""
    best = 0.0
    for l in ep.content_lines:
        if start - 0.05 <= l.start <= start + window:
            best = max(best, line_signal(l, show_words)[0])
    return best
