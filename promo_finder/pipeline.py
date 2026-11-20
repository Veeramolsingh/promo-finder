"""Runs the whole pipeline for one show.

  READ (code) -> FLAG (code) -> FIND (AI) -> CHECK (code) -> JUDGE (AI) -> RANK (code) -> REPORT

Two AI calls per episode (one find, one judge), plus one retry for an episode
whose ideas all fail the checks. Every AI request and reply is saved unedited
in <out>/raw/.
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

from .check import Promo, check_candidate, clip_text, overlap_ratio
from .llm import LLM, DailyLimitError, LLMError
from .parse import Episode, load_show, render_for_ai, show_title
from .prompts import FIND_SYSTEM, JUDGE_SYSTEM, find_user, judge_user
from .beats import BEATS_SYSTEM, group_story_beats
from .rank import rank_all, shortlist, shortlist_candidates
from .report import write_outputs
from .show_words import WORDS_SYSTEM, learn_show_words
from .signals import episode_hints, hook_signal_at


def build_shortlist(ranked: list[Promo], cfg: dict, judge: LLM | None, title: str,
                    raw_dir: Path | None, log=print, stats: dict | None = None) -> list[Promo]:
    """Rank -> group the top candidates by story moment (1 AI call) -> pick the shortlist."""
    rcfg = cfg["ranking"]
    beat_of = None
    if judge is not None and rcfg.get("group_story_moments", True):
        cands = shortlist_candidates(ranked, rcfg)[: rcfg.get("story_group_candidates", 25)]
        if len(cands) >= 2 and stats is not None:
            stats["calls"] += 1  # the grouping call (only made when there is something to group)
        beat_of = group_story_beats(cands, judge, title, log=log,
                                    raw_path=(raw_dir / "show_story_groups.json") if raw_dir else None)
        for p in ranked:
            p.story_group = beat_of.get(p.id)
    return shortlist(ranked, rcfg, beat_of)


def _save_raw(raw_dir: Path, name: str, payload: dict) -> None:
    (raw_dir / name).write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _make_llm(spec: str, cfg: dict) -> LLM:
    return LLM(spec, cfg["providers"], cfg.get("model_params", {}).get(spec, {}))


def _find(ep: Episode, n_eps: int, title: str, finder: LLM, cfg: dict, raw_dir: Path,
          stats: dict, feedback: str = "", suffix: str = "find",
          show_words: tuple[str, ...] = ()) -> list[dict] | None:
    user = find_user(title, ep.number, n_eps, ep.logline, ep.continuity,
                     episode_hints(ep, show_words=show_words), render_for_ai(ep), feedback)
    res = finder.json_call(FIND_SYSTEM, user, cfg["llm"]["find_max_tokens"],
                           tag=f"ep{ep.number}-{suffix}")
    stats["calls"] += 1
    stats["cached_calls"] += int(res.cached)
    for k in ("input_tokens", "output_tokens"):
        stats[k] += res.usage.get(k) or 0
    _save_raw(raw_dir, f"ep{ep.number:03d}_{suffix}.json", {
        "episode": ep.number, "step": suffix, "model": res.model, "cached": res.cached,
        "seconds": res.seconds, "usage": res.usage, "error": res.error,
        "prompt_user": user, "response_text": res.text})
    if res.data is None:
        return None
    promos = res.data.get("promos")
    return promos if isinstance(promos, list) else None


def process_episode(ep: Episode, n_eps: int, title: str, finder: LLM, judge: LLM,
                    cfg: dict, raw_dir: Path, stats: dict, log=print,
                    show_words: tuple[str, ...] = ()) -> list[Promo]:
    rules = cfg["rules"]
    cands = _find(ep, n_eps, title, finder, cfg, raw_dir, stats, show_words=show_words)
    promos: list[Promo] = []
    if cands is None:
        log(f"  ep {ep.number}: finder returned no valid JSON")
    else:
        promos = [check_candidate(ep, c, f"E{ep.number:02d}-P{i+1}", rules)
                  for i, c in enumerate(cands) if isinstance(c, dict)]

    # One retry with the code's feedback when nothing usable came back
    if not any(p.valid for p in promos):
        problems = [f"- {p.id}: " + "; ".join(p.problems) for p in promos] or \
                   ["- your reply was not valid JSON in the required shape"]
        cands2 = _find(ep, n_eps, title, finder, cfg, raw_dir, stats,
                       feedback="\n".join(problems), suffix="find_retry", show_words=show_words)
        stats["retries"] += 1
        if cands2:
            promos += [check_candidate(ep, c, f"E{ep.number:02d}-R{i+1}", rules)
                       for i, c in enumerate(cands2) if isinstance(c, dict)]

    # Drop near-duplicates (keep the one the finder ranked higher)
    kept: list[Promo] = []
    for p in promos:
        if not p.valid:
            continue
        dup = next((k for k in kept if overlap_ratio(p, k) > rules["dedupe_overlap"]), None)
        if dup:
            p.valid = False
            p.problems.append(f"near-duplicate of {dup.id}")
        else:
            kept.append(p)

    for p in kept:
        p.rule_hook = round(hook_signal_at(ep, p.segments[0].start, show_words=show_words), 2)

    # JUDGE: all of this episode's valid promos in one call
    if kept:
        clips = [{"id": p.id, "duration": p.duration, "text": clip_text(ep, p.segments)} for p in kept]
        user = judge_user(title, clips)
        res = judge.json_call(JUDGE_SYSTEM, user, cfg["llm"]["judge_max_tokens"],
                              tag=f"ep{ep.number}-judge")
        stats["calls"] += 1
        stats["cached_calls"] += int(res.cached)
        for k in ("input_tokens", "output_tokens"):
            stats[k] += res.usage.get(k) or 0
        _save_raw(raw_dir, f"ep{ep.number:03d}_judge.json", {
            "episode": ep.number, "step": "judge", "model": res.model, "cached": res.cached,
            "seconds": res.seconds, "usage": res.usage, "error": res.error,
            "prompt_user": user, "response_text": res.text})
        by_id = {}
        for s in (res.data or {}).get("scores", []) or []:
            if isinstance(s, dict) and "id" in s:
                by_id[str(s["id"]).strip()] = s
        for p in kept:
            s = by_id.get(p.id)
            if not s:
                p.problems.append("judge did not score this promo")
                continue
            clean = {}
            for k in ("hook", "cliffhanger", "clarity"):
                try:
                    clean[k] = max(1, min(5, int(round(float(s.get(k))))))
                except Exception:
                    pass
            sp = str(s.get("spoiler", "none")).lower().strip()
            clean["spoiler"] = sp if sp in ("none", "premise", "major") else "none"
            clean["confusing"] = str(s.get("confusing", "") or "")
            clean["verdict"] = str(s.get("verdict", "") or "")
            p.scores = clean

    valid_n = sum(p.valid for p in promos)
    log(f"  ep {ep.number:3d}: {len(promos)} ideas, {valid_n} passed checks, "
        f"{sum(1 for p in kept if p.scores)} judged")
    return promos


def parse_episode_filter(spec: str | None) -> set[int] | None:
    if not spec:
        return None
    out = set()
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-")
            out.update(range(int(a), int(b) + 1))
        elif part:
            out.add(int(part))
    return out


def run(input_dir: str, out_dir: str, cfg: dict, episodes: str | None = None,
        find_model: str | None = None, judge_model: str | None = None,
        title: str | None = None, log=print) -> dict:
    t0 = time.time()
    out = Path(out_dir)
    raw_dir = out / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    eps = load_show(input_dir)
    if not eps:
        raise SystemExit(f"No .txt scripts found in {input_dir}")
    n_eps = max(e.number for e in eps)
    title = title or show_title(input_dir)
    wanted = parse_episode_filter(episodes)
    todo = [e for e in eps if wanted is None or e.number in wanted]

    find_spec = find_model or cfg["models"]["find"]
    judge_spec = judge_model or cfg["models"]["judge"]
    finder, judge = _make_llm(find_spec, cfg), _make_llm(judge_spec, cfg)
    (raw_dir / "_prompts.txt").write_text(
        f"=== FIND SYSTEM PROMPT ===\n{FIND_SYSTEM}\n\n=== JUDGE SYSTEM PROMPT ===\n{JUDGE_SYSTEM}\n\n"
        f"=== STORY-MOMENT GROUPING PROMPT ===\n{BEATS_SYSTEM}\n\n"
        f"=== SHOW WORDS PROMPT ===\n{WORDS_SYSTEM}\n",
        encoding="utf-8")

    log(f"Show: {title or '(untitled)'} | {len(todo)} of {len(eps)} episodes | "
        f"finder={find_spec} judge={judge_spec}")
    stats = {"calls": 0, "cached_calls": 0, "retries": 0, "input_tokens": 0, "output_tokens": 0}

    # Learn this show's own shock words from all its episode summaries (1 small call per show),
    # so the keyword hints fit any genre. The judge model does it: it's cheap and not the finder.
    show_words: tuple[str, ...] = ()
    if cfg.get("hints", {}).get("learn_show_words", True):
        try:
            show_words, usage = learn_show_words(eps, title, judge, raw_dir / "show_words.json", log)
            if usage:
                stats["calls"] += 1
                for k in ("input_tokens", "output_tokens"):
                    stats[k] += usage.get(k) or 0
        except DailyLimitError as e:
            log(f"  show words skipped (daily limit): {e}")
    all_promos: list[Promo] = []
    stopped = None
    pause = cfg["llm"].get("pause_between_calls_seconds", 0)
    for ep in todo:
        for w in ep.warnings:
            log(f"  ep {ep.number}: parse warning: {w}")
        if not ep.content_lines:  # nothing the AI could read: don't spend a call on it
            log(f"  ep {ep.number}: skipped - no timed script lines found in {ep.source_file}")
            continue
        try:
            all_promos += process_episode(ep, n_eps, title, finder, judge, cfg, raw_dir, stats, log,
                                          show_words=show_words)
        except DailyLimitError as e:
            stopped = f"stopped at episode {ep.number}: provider daily limit reached ({e})"
            log("  " + stopped)
            break
        except LLMError as e:
            log(f"  ep {ep.number}: skipped - {e}")
        if pause:
            time.sleep(pause)

    ranked = rank_all(all_promos, cfg["ranking"])
    try:
        short = build_shortlist(ranked, cfg, judge, title, raw_dir, log, stats)
    except DailyLimitError as e:
        log(f"  story-moment grouping skipped (daily limit): {e}")
        short = shortlist(ranked, cfg["ranking"])
    meta = {
        "show": title, "input_dir": str(input_dir), "episodes_total": len(eps),
        "episodes_processed": len({p.episode for p in all_promos}) if all_promos else 0,
        "episodes_requested": [e.number for e in todo],
        "find_model": find_spec, "judge_model": judge_spec, "show_words": list(show_words),
        "started": datetime.fromtimestamp(t0).isoformat(timespec="seconds"),
        "seconds": round(time.time() - t0, 1), "stopped_early": stopped, **stats,
    }
    episodes_by_num = {e.number: e for e in eps}
    write_outputs(out, all_promos, ranked, short, meta, cfg, episodes_by_num)
    log(f"Done in {meta['seconds']}s: {len(ranked)} ranked promos, shortlist of {len(short)}. "
        f"Outputs in {out}")
    return meta
