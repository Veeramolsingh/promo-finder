"""Step 7 - REPORT: writes what an editor (and a reviewer) reads.

  report.md     ranked "post first" shortlist + every promo + rejected ideas + quality stats
  promos.csv    one row per ranked promo (opens in Excel / Sheets)
  promos.json   everything, including rejected ideas and run details
  raw/          every AI request and reply, unedited
"""

from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path

from .check import Promo, clip_text
from .parse import Episode
from .rank import rank_reason


def _seg_lines(p: Promo) -> str:
    return "<br>".join(f"Ep {p.episode} {s.start // 60:02.0f}:{s.start % 60:05.2f} -> "
                       f"{s.end // 60:02.0f}:{s.end % 60:05.2f}" for s in p.segments)


def _md_escape(s: str) -> str:
    return (s or "").replace("|", "/").replace("\n", " ")


def quality_stats(all_promos: list[Promo], ranked: list[Promo]) -> dict:
    reasons = Counter()
    for p in all_promos:
        for prob in p.problems:
            key = prob.split(":")[0]
            key = "near-duplicate" if key.startswith("near-duplicate") else key
            reasons[key] += 1
    fixes = Counter()
    for p in all_promos:
        for f in p.fixes:
            fixes[f.split(" so ")[0].split("(")[0].strip()] += 1
    sc = [p.scores for p in ranked]
    return {
        "ideas_from_ai": len(all_promos),
        "passed_checks": sum(1 for p in all_promos if p.valid),
        "ranked": len(ranked),
        "rejection_reasons": dict(reasons.most_common()),
        "automatic_fixes": dict(fixes.most_common()),
        "spoiler_major": sum(1 for s in sc if s.get("spoiler") == "major"),
        "spoiler_premise": sum(1 for s in sc if s.get("spoiler") == "premise"),
        "low_clarity_(<3)": sum(1 for s in sc if s.get("clarity", 5) < 3),
        "multi_stretch_promos": sum(1 for p in ranked if len(p.segments) > 1),
        "avg_duration_s": round(sum(p.duration for p in ranked) / len(ranked), 1) if ranked else 0,
        "score_spread": {k: dict(Counter(s.get(k) for s in sc)) for k in ("hook", "cliffhanger", "clarity")},
    }


def write_outputs(out: Path, all_promos: list[Promo], ranked: list[Promo], short: list[Promo],
                  meta: dict, cfg: dict, episodes: dict[int, Episode]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    stats = quality_stats(all_promos, ranked)
    rank_of = {p.id: i + 1 for i, p in enumerate(ranked)}

    # ---- JSON (everything)
    payload = {
        "run": meta, "quality": stats,
        "shortlist": [p.id for p in short],
        "promos": [dict(p.to_dict(), rank=rank_of.get(p.id)) for p in all_promos],
    }
    (out / "promos.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    # ---- CSV (ranked promos)
    cols = ["rank", "shortlist", "id", "episode", "segments", "duration_s", "title", "hook",
            "cliffhanger", "why_it_works", "score", "hook_score", "cliffhanger_score",
            "clarity_score", "spoiler", "rule_signal", "why_ranked_here", "judge_verdict",
            "confusing_for_new_viewer", "code_fixes"]
    short_ids = {p.id for p in short}
    with open(out / "promos.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for p in ranked:
            s = p.scores
            w.writerow([rank_of[p.id], "yes" if p.id in short_ids else "", p.id, p.episode,
                        p.timestamps(), p.duration, p.title, p.hook, p.cliffhanger, p.why,
                        p.final_score, s.get("hook"), s.get("cliffhanger"), s.get("clarity"),
                        s.get("spoiler"), p.rule_hook, rank_reason(p), s.get("verdict"),
                        s.get("confusing"), " | ".join(p.fixes)])

    # ---- Markdown report
    L = []
    L.append(f"# Promo Finder report: {meta.get('show') or 'show'}\n")
    L.append(f"Finder model `{meta['find_model']}`, judge model `{meta['judge_model']}`. "
             f"{meta['episodes_processed']} episodes processed in {meta['seconds']}s "
             f"({meta['calls']} AI calls, {meta['cached_calls']} served from cache, "
             f"{meta['input_tokens']:,} input + {meta['output_tokens']:,} output tokens).")
    if meta.get("stopped_early"):
        L.append(f"\n> **Run stopped early:** {meta['stopped_early']}. Re-run the same command to resume; "
                 f"finished episodes come from the cache.")
    L.append("\nTimestamps are mm:ss.xx from the start of each episode. Scores are 1-5. "
             "Final score = 0.35 hook + 0.30 cliffhanger + 0.25 clarity + 0.10 keyword signal, "
             "minus 1 for a major spoiler.\n")

    L.append("## Post these first\n")
    L.append("Best promos overall, at most one per episode, and only ones a stranger can follow "
             "(clarity 3+).\n")
    for i, p in enumerate(short, 1):
        s = p.scores
        L.append(f"### {i}. {p.title or p.id}  (score {p.final_score}, {p.duration:.0f}s)")
        L.append(f"- **Where:** " + "; then ".join(
            f"Episode {p.episode}, {seg}" for seg in p.timestamps().split("; ")))
        L.append(f"- **Hook (first 3s):** {p.hook}")
        L.append(f"- **Cliffhanger (the cut):** {p.cliffhanger}")
        L.append(f"- **Why it could work:** {p.why}")
        L.append(f"- **Why it ranks here:** {rank_reason(p)}. Judge: {s.get('verdict', '')}")
        if s.get("confusing"):
            L.append(f"- **Might confuse a new viewer:** {s['confusing']}")
        if p.fixes:
            L.append(f"- **Code adjusted the AI's cut:** {'; '.join(p.fixes)}")
        ep = episodes.get(p.episode)
        if ep:
            L.append("\n<details><summary>Clip script</summary>\n\n```\n" +
                     clip_text(ep, p.segments) + "\n```\n</details>\n")
    if not short:
        L.append("_No promo met the shortlist bar._\n")

    L.append("\n## All ranked promos\n")
    L.append("| Rank | ID | Where | Len | Title | Hook | Cliff | Clarity | Spoiler | Score |")
    L.append("|---|---|---|---|---|---|---|---|---|---|")
    for p in ranked:
        s = p.scores
        L.append(f"| {rank_of[p.id]} | {p.id} | {_seg_lines(p)} | {p.duration:.0f}s | "
                 f"{_md_escape(p.title)} | {s.get('hook')} | {s.get('cliffhanger')} | "
                 f"{s.get('clarity')} | {s.get('spoiler')} | {p.final_score} |")

    L.append("\n## Ideas the code rejected\n")
    rej = [p for p in all_promos if not p.valid]
    if rej:
        L.append("| ID | Why rejected |")
        L.append("|---|---|")
        for p in rej:
            L.append(f"| {p.id} | {_md_escape('; '.join(p.problems))} |")
    else:
        L.append("_None._")

    L.append("\n## Quality summary (automatic)\n")
    L.append("```json\n" + json.dumps(stats, indent=2) + "\n```")
    missing = sorted(set(meta.get("episodes_requested", [])) - {p.episode for p in ranked})
    if missing:
        L.append(f"\nEpisodes with no ranked promo: {', '.join(map(str, missing))}")
    (out / "report.md").write_text("\n".join(L) + "\n", encoding="utf-8")
