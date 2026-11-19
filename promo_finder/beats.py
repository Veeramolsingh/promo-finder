"""Step 6b - group the shortlist candidates by story moment (one AI call per show).

Consecutive microdrama episodes replay or continue the previous cliffhanger, so two
promos from episodes 31 and 32 can show the same moment with different lines. Code
can't tell that from the text, so the judge model groups the top candidates by story moment, and
the shortlist keeps only the best promo from each group.
"""

from __future__ import annotations

from .check import Promo
from .llm import LLM, LLMError

BEATS_SYSTEM = """You are a promo editor planning a week of social posts for one show.
Below are candidate promo clips. Consecutive episodes often replay or continue the same
scene, so two clips can show the SAME story moment even if worded differently.

Group the clips that show the same story moment (the same confrontation, threat or event).
Clips about different events go in different groups. Use every id exactly once.

Reply with JSON only: {"groups": [["<id>", "<id>"], ["<id>"], ...]}"""


def beats_user(title: str, promos: list[Promo]) -> str:
    rows = [f"SHOW: {title or '(untitled)'}\n"]
    for p in sorted(promos, key=lambda p: (p.episode, p.id)):
        rows.append(f"- {p.id} (episode {p.episode}): {p.title}. Opens: {p.hook} Ends: {p.cliffhanger}")
    return "\n".join(rows)


def group_story_beats(promos: list[Promo], llm: LLM, title: str, max_tokens: int = 2000,
                      log=print, raw_path=None) -> dict[str, int]:
    """Return {promo id: group number}. On any failure every promo is its own group."""
    import json
    fallback = {p.id: i for i, p in enumerate(promos)}
    if len(promos) < 2:
        return fallback
    user = beats_user(title, promos)
    try:
        res = llm.json_call(BEATS_SYSTEM, user, max_tokens, tag="beats")
    except LLMError as e:
        log(f"  story-moment grouping skipped: {e}")
        return fallback
    if raw_path is not None:
        raw_path.write_text(json.dumps({
            "step": "story_moment_grouping", "model": res.model, "cached": res.cached,
            "usage": res.usage, "error": res.error, "prompt_system": BEATS_SYSTEM,
            "prompt_user": user, "response_text": res.text}, indent=2, ensure_ascii=False),
            encoding="utf-8")
    groups = (res.data or {}).get("groups")
    if not isinstance(groups, list):
        log("  story-moment grouping skipped: model reply was not usable")
        return fallback
    known = {p.id for p in promos}
    out: dict[str, int] = {}
    for gi, g in enumerate(groups):
        for pid in (g if isinstance(g, list) else []):
            pid = str(pid).strip()
            if pid in known and pid not in out:
                out[pid] = gi
    nxt = len(groups)
    for p in promos:  # anything the model left out stands alone
        if p.id not in out:
            out[p.id] = nxt
            nxt += 1
    return out
