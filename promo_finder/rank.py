"""Step 6 - RANK (plain code, no AI).

final score (1-5 scale) =
    0.35 * hook + 0.30 * cliffhanger + 0.25 * clarity      (judge scores, 1-5)
  + 0.10 * rule signal (keyword hook strength, mapped to 1-5)
  - 1.0 if the judge says the clip gives away a major spoiler

The "post first" shortlist then takes the best promos, stops at a minimum score
(3.0) so a weak clip is never recommended just to fill the list, skips any a
stranger could not follow (clarity < 3), and allows at most one promo per
episode so the first week of posting covers different story beats.
"""

from __future__ import annotations

from .check import Promo


def score_promo(p: Promo, cfg: dict) -> float | None:
    s = p.scores
    if not s or any(k not in s for k in ("hook", "cliffhanger", "clarity")):
        return None
    w = cfg["weights"]
    rules_1to5 = 1 + 4 * p.rule_hook
    total = (w["hook"] * s["hook"] + w["cliffhanger"] * s["cliffhanger"]
             + w["clarity"] * s["clarity"] + w["rules"] * rules_1to5)
    if s.get("spoiler") == "major":
        total -= cfg["major_spoiler_penalty"]
    return round(total, 2)


def rank_reason(p: Promo) -> str:
    s = p.scores
    bits = [f"hook {s.get('hook')}/5", f"cliffhanger {s.get('cliffhanger')}/5",
            f"clarity {s.get('clarity')}/5"]
    if s.get("spoiler") == "major":
        bits.append("major spoiler (penalised)")
    elif s.get("spoiler") == "premise":
        bits.append("shows the title's premise (fine)")
    if p.rule_hook >= 0.5:
        bits.append("strong physical/verbal opener")
    return ", ".join(bits)


def rank_all(promos: list[Promo], cfg: dict) -> list[Promo]:
    for p in promos:
        p.final_score = score_promo(p, cfg) if p.valid else None
    ranked = [p for p in promos if p.final_score is not None]
    # ties: prefer the clearer clip, then the earlier episode
    ranked.sort(key=lambda p: (-p.final_score, -p.scores.get("clarity", 0), p.episode))
    return ranked


def shortlist_candidates(ranked: list[Promo], cfg: dict) -> list[Promo]:
    """Promos good enough to be considered for the shortlist (score and clarity bars)."""
    return [p for p in ranked
            if p.final_score >= cfg.get("min_score_for_shortlist", 0)
            and p.scores.get("clarity", 0) >= cfg["min_clarity_for_shortlist"]]


def shortlist(ranked: list[Promo], cfg: dict, beat_of: dict[str, int] | None = None) -> list[Promo]:
    out, per_ep, beats_used = [], {}, set()
    for p in shortlist_candidates(ranked, cfg):
        if per_ep.get(p.episode, 0) >= cfg["max_per_episode_in_shortlist"]:
            continue
        beat = beat_of.get(p.id) if beat_of else None
        if beat is not None and beat in beats_used:
            continue  # a better promo of the same story moment is already in
        out.append(p)
        per_ep[p.episode] = per_ep.get(p.episode, 0) + 1
        if beat is not None:
            beats_used.add(beat)
        if len(out) >= cfg["top_n"]:
            break
    return out
