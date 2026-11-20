"""Learn each show's own shock words (one small AI call per show).

The built-in keyword lists in signals.py are genre-neutral. Every microdrama genre also has
its own vocabulary of big moments: a secret duke and an heir in a historical drama, a CEO,
shares and a contract marriage in a billionaire romance, an alpha and a mate in a werewolf
story. This step reads the show's title and episode summaries and asks a model for those
words, so the keyword hints adapt to any show without anyone editing a list.

If the call fails, the run carries on with the generic lists only.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .llm import LLM, LLMError
from .parse import Episode

WORDS_SYSTEM = """You help a promo editor for vertical microdramas.
From the show's title, episode summaries and sample lines, list the short words characters
actually SAY (or that an action line shows) at this show's most shocking moments.

Good examples by genre (do not copy them, find this show's own):
- historical: "duke", "heir", "imposter", "rightful", "banished"
- billionaire: "ceo", "owner", "shares", "contract", "fired"
- werewolf: "alpha", "mate", "rejected", "pack"

Rules:
- 8 to 20 items, lowercase, ONE word each (two only for a fixed term like "dna test").
- Titles, ranks, hidden identities, ownership/power words, key reveal objects.
- NOT abstract themes ("power reversal", "humiliation"), NOT places, NOT character names.
- Skip generic drama words that fit any show (slap, betray, secret, divorce, pregnant, kill).

Reply with JSON only: {"words": ["...", "..."]}"""

GENERIC_SKIP = {"slap", "betray", "betrayal", "secret", "divorce", "pregnant", "kill", "murder",
                "revenge", "truth", "lie", "love", "wedding", "marriage"}


def words_user(title: str, eps: list[Episode], max_chars_each: int = 220, n_samples: int = 30) -> str:
    from .signals import line_signal
    lines = [f"SHOW TITLE: {title or '(untitled)'}", "", "EPISODE SUMMARIES:"]
    for e in eps:
        if e.logline:
            lines.append(f"- Ep {e.number}: {e.logline[:max_chars_each]}")
    # Evidence: the show's most dramatic lines (by the generic rules), so the model picks
    # words the characters actually say rather than themes.
    scored = sorted(((line_signal(l)[0], l) for e in eps for l in e.content_lines
                     if l.kind in ("dialogue", "action")), key=lambda x: -x[0])[:n_samples]
    if scored:
        lines += ["", "SOME OF THE SHOW'S MOST DRAMATIC LINES:"]
        lines += [f"- {l.text[:140]}" for _, l in scored]
    return "\n".join(lines)


def _clean(words, eps: list[Episode]) -> tuple[str, ...]:
    """Normalise, and keep only words that actually occur in the show's lines (code check:
    a hint word that never appears in the scripts can't help, so it is dropped)."""
    corpus = " ".join(l.text.lower() for e in eps for l in e.content_lines)
    out = []
    for w in words if isinstance(words, list) else []:
        w = re.sub(r"\s+", " ", str(w).strip().lower().strip(".,!?\"'"))
        if not (2 <= len(w) <= 25 and len(w.split()) <= 2) or w in GENERIC_SKIP or w in out:
            continue
        if re.search(r"\b" + re.escape(w), corpus):
            out.append(w)
    return tuple(out[:20])


def learn_show_words(eps: list[Episode], title: str, llm: LLM, raw_path: Path | None = None,
                     log=print) -> tuple[tuple[str, ...], dict]:
    """Return (show words, usage). Empty words on any failure or when there are no summaries."""
    if not any(e.logline for e in eps):
        log("  show words: no episode summaries in the scripts - using generic keywords only")
        return (), {}
    user = words_user(title, eps)
    try:
        res = llm.json_call(WORDS_SYSTEM, user, max_tokens=1200, tag="show-words")
    except LLMError as e:
        log(f"  show words skipped: {e}")
        return (), {}
    words = _clean((res.data or {}).get("words"), eps)
    if raw_path is not None:
        raw_path.write_text(json.dumps({
            "step": "show_words", "model": res.model, "cached": res.cached, "usage": res.usage,
            "error": res.error, "prompt_system": WORDS_SYSTEM, "prompt_user": user,
            "response_text": res.text, "words_used": list(words)}, indent=2, ensure_ascii=False),
            encoding="utf-8")
    log(f"  show words ({len(words)}): {', '.join(words) if words else '(none - generic keywords only)'}")
    return words, (res.usage or {})
