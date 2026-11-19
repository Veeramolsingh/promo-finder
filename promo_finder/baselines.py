"""Two no-AI baselines. If the AI pipeline cannot beat these, it is not adding value.

  last45  - "the last 45 seconds of every episode". Microdrama episodes are written to end
            on a cliffhanger, so this is what a hurried editor might do.
  rules   - start on the line with the strongest keyword hook signal, run ~45 seconds.

Both go through the same CHECK code as the AI's ideas, so the comparison is fair.
"""

from __future__ import annotations

from .check import Promo, check_candidate
from .parse import Episode
from .signals import line_signal

TARGET = 45.0


def _window_from(ep: Episode, start_line_idx: int, target: float) -> dict:
    lines = ep.content_lines
    first = ep.line(start_line_idx)
    in_window = [l for l in lines if l.idx >= start_line_idx and l.end <= first.start + target]
    last = in_window[-1] if in_window else first
    return {"segments": [{"start_line": first.idx, "end_line": last.idx}],
            "hook_line": first.idx, "cliffhanger_line": last.idx}


def last45(ep: Episode, cfg: dict) -> Promo:
    lines = ep.content_lines
    end = lines[-1].end
    first = next(l for l in lines if l.start >= end - TARGET)
    cand = {"segments": [{"start_line": first.idx, "end_line": lines[-1].idx}],
            "hook_line": first.idx, "cliffhanger_line": lines[-1].idx,
            "title": "Last 45 seconds"}
    return check_candidate(ep, cand, f"E{ep.number:02d}-L45", cfg, source="baseline_last45")


def rules(ep: Episode, cfg: dict) -> Promo:
    best = max(ep.content_lines, key=lambda l: (line_signal(l)[0], -l.start))
    cand = _window_from(ep, best.idx, TARGET)
    cand["title"] = "Strongest keyword line + 45s"
    return check_candidate(ep, cand, f"E{ep.number:02d}-RUL", cfg, source="baseline_rules")
