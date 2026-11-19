"""Step 4 - CHECK (plain code, no AI).

The AI proposes promos as line numbers. This module turns them into real
timestamps and enforces the brief's rules, fixing what it safely can:

  * the hook line starts within the first 3 seconds of the clip
  * the clip ends right after the cliffhanger line (a short silent reaction may stay)
  * no cut lands in the middle of someone's sentence
  * cuts snap to a camera shot change when one is close (that is where editors cut)
  * total length is 20-60 seconds

Every adjustment is written into `fixes`, every rejection into `problems`,
so an editor can see exactly what the code changed and why.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict

from .parse import Episode, Line, fmt_time

EPS = 0.05


@dataclass
class Segment:
    start: float
    end: float

    @property
    def duration(self) -> float:
        return round(self.end - self.start, 2)


@dataclass
class Promo:
    id: str
    episode: int
    source: str                      # "ai" or a baseline name
    title: str = ""
    hook: str = ""
    cliffhanger: str = ""
    why: str = ""
    hook_line: int | None = None
    cliffhanger_line: int | None = None
    segments: list[Segment] = field(default_factory=list)
    valid: bool = False
    problems: list[str] = field(default_factory=list)
    fixes: list[str] = field(default_factory=list)
    # filled in later by the judge / ranker
    scores: dict = field(default_factory=dict)
    rule_hook: float = 0.0
    final_score: float | None = None
    story_group: int | None = None   # promos with the same number show the same story moment

    @property
    def duration(self) -> float:
        return round(sum(s.duration for s in self.segments), 2)

    def timestamps(self) -> str:
        return "; ".join(f"{fmt_time(s.start)}-{fmt_time(s.end)}" for s in self.segments)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["duration"] = self.duration
        d["timestamps"] = self.timestamps()
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Promo":
        keep = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        keep["segments"] = [Segment(s["start"], s["end"]) for s in d.get("segments", [])]
        return cls(**keep)


# ---------------------------------------------------------------- helpers
def _dialogue(ep: Episode) -> list[Line]:
    return [l for l in ep.lines if l.kind == "dialogue"]


def _straddling(ep: Episode, t: float) -> Line | None:
    """A spoken line that is still being said at time t (a cut here would chop it)."""
    for l in _dialogue(ep):
        if l.start + EPS < t < l.end - EPS:
            return l
    return None


def _shot_start_before(ep: Episode, t: float, lookback: float) -> float | None:
    starts = [s.start for s in ep.shots if t - lookback - EPS <= s.start <= t + EPS]
    return max(starts) if starts else None


def _shot_end_after(ep: Episode, t: float, lookahead: float) -> float | None:
    ends = [s.end for s in ep.shots if t - EPS <= s.end <= t + lookahead + EPS and s.start < t]
    return min(ends) if ends else None


def _speech_starts_between(ep: Episode, a: float, b: float) -> bool:
    return any(a + EPS < l.start < b - EPS for l in _dialogue(ep))


def clip_lines(ep: Episode, segments: list[Segment]) -> list[list[Line]]:
    """Content lines (no camera notes) that fall inside each segment."""
    out = []
    for s in segments:
        out.append([l for l in ep.content_lines if s.start - EPS <= l.start < s.end - EPS])
    return out


def clip_text(ep: Episode, segments: list[Segment]) -> str:
    """Readable clip script with clip-relative times, used by the judge and the reports."""
    rows, offset = [], 0.0
    for i, (seg, lines) in enumerate(zip(segments, clip_lines(ep, segments))):
        if i > 0:
            rows.append("[CUT]")
        for l in lines:
            t = offset + (l.start - seg.start)
            if l.kind == "dialogue":
                tone = f" ({l.tone})" if l.tone else ""
                rows.append(f"+{t:4.1f}s {l.speaker}{tone}: {l.text}")
            elif l.kind == "action":
                rows.append(f"+{t:4.1f}s ACTION: {l.text}")
            else:
                rows.append(f"+{t:4.1f}s SOUND: {l.text}")
        offset += seg.duration
    return "\n".join(rows)


# ---------------------------------------------------------------- start / end rules
def snap_hook_start(ep: Episode, hook: Line, proposed: float | None, cfg: dict,
                    fixes: list[str]) -> float:
    max_lead = cfg["hook_within_seconds"]
    if proposed is not None and hook.start - max_lead - EPS <= proposed <= hook.start + EPS:
        start = proposed
    else:
        shot = _shot_start_before(ep, hook.start, lookback=cfg["snap_window_seconds"])
        start = shot if shot is not None else hook.start
        if proposed is not None and proposed < hook.start - max_lead:
            fixes.append(f"trimmed {start - proposed:.1f}s of slow lead-in so the "
                         f"hook lands in the first {max_lead:.0f}s")
    l = _straddling(ep, start)
    if l is not None and l.idx != hook.idx:
        if l.start >= hook.start - max_lead - EPS:
            start = l.start
        else:
            start = min(l.end, hook.start)
        fixes.append("moved start so it does not cut into a spoken line")
    return round(start, 2)


def snap_end(ep: Episode, last_line: Line, cfg: dict, fixes: list[str]) -> float:
    end = last_line.end
    shot_end = _shot_end_after(ep, end, lookahead=cfg["snap_window_seconds"])
    if shot_end is not None and not _speech_starts_between(ep, end, shot_end):
        end = shot_end
    l = _straddling(ep, end)
    if l is not None:
        if l.start >= last_line.end - EPS:
            end = max(last_line.end, l.start)     # cut before the next line starts
        else:
            end = l.end                            # finish the overlapping line
            fixes.append("extended end so it does not cut into a spoken line")
    return round(end, 2)


def snap_mid_start(ep: Episode, first: Line, cfg: dict, fixes: list[str]) -> float:
    start = first.start
    shot = _shot_start_before(ep, start, lookback=1.0)
    if shot is not None:
        start = shot
    l = _straddling(ep, start)
    if l is not None:
        start = l.start
        fixes.append("moved a cut so it does not chop a spoken line")
    return round(start, 2)


# ---------------------------------------------------------------- main check
def check_candidate(ep: Episode, cand: dict, pid: str, cfg: dict, source: str = "ai") -> Promo:
    p = Promo(id=pid, episode=ep.number, source=source,
              title=str(cand.get("title", ""))[:80], hook=str(cand.get("hook", "")),
              cliffhanger=str(cand.get("cliffhanger", "")), why=str(cand.get("why", "")))
    n = len(ep.lines)

    def as_int(x):
        try:
            return int(str(x).lstrip("Ll"))
        except Exception:
            return None

    raw_segs = cand.get("segments") or []
    segs = []
    for s in raw_segs[: cfg["max_segments"]]:
        a, b = as_int(s.get("start_line")), as_int(s.get("end_line"))
        if a is None or b is None or not (0 <= a < n) or not (0 <= b < n):
            p.problems.append(f"segment refers to a line that does not exist: {s}")
            continue
        if b < a:
            a, b = b, a
        segs.append([a, b])
    if len(raw_segs) > cfg["max_segments"]:
        p.fixes.append(f"kept only the first {cfg['max_segments']} stretches")
    hook_i, cliff_i = as_int(cand.get("hook_line")), as_int(cand.get("cliffhanger_line"))
    if not segs:
        p.problems.append("no usable segments")
        return p
    if hook_i is None or not (0 <= hook_i < n):
        hook_i = segs[0][0]
        p.fixes.append("hook line missing - used the first line of the clip")
    if cliff_i is None or not (0 <= cliff_i < n):
        cliff_i = segs[-1][1]
        p.fixes.append("cliffhanger line missing - used the last line of the clip")
    p.hook_line, p.cliffhanger_line = hook_i, cliff_i

    # order + overlap of stretches (by line number)
    segs.sort()
    for i in range(1, len(segs)):
        if segs[i][0] <= segs[i - 1][1]:
            p.problems.append("stretches overlap")
            return p

    # hook must be in the first stretch, cliffhanger in the last
    if hook_i < segs[0][0]:
        segs[0][0] = hook_i
        p.fixes.append("extended first stretch back to include the hook line")
    if hook_i > segs[0][1]:
        p.problems.append(f"hook line L{hook_i} is not in the first stretch")
        return p
    if cliff_i > segs[-1][1]:
        segs[-1][1] = cliff_i
        p.fixes.append("extended last stretch to include the cliffhanger line")
    if cliff_i < segs[-1][0]:
        p.problems.append(f"cliffhanger line L{cliff_i} is not in the last stretch")
        return p

    hook, cliff = ep.line(hook_i), ep.line(cliff_i)

    # end right after the cliffhanger: keep only a short silent reaction after it
    last_line = cliff
    tail = [l for l in ep.content_lines if segs[-1][0] <= l.idx <= segs[-1][1] and l.idx > cliff_i
            and l.start >= cliff.start]
    keep_tail = tail and all(l.kind != "dialogue" for l in tail) and \
        max(l.end for l in tail) <= cliff.end + cfg["reaction_tail_seconds"]
    if keep_tail:
        last_line = max(tail, key=lambda l: l.end)
    elif tail:
        p.fixes.append("cut right after the cliffhanger line (dropped what came after it)")

    times = []
    for i, (a, b) in enumerate(segs):
        first_l = ep.line(a)
        if i == 0:
            start = snap_hook_start(ep, hook, first_l.start, cfg, p.fixes)
        else:
            start = snap_mid_start(ep, first_l, cfg, p.fixes)
        if i == len(segs) - 1:
            end = snap_end(ep, last_line, cfg, p.fixes)
        else:
            seg_lines = [ep.line(k) for k in range(a, b + 1) if ep.line(k).kind != "camera"]
            end_line = max(seg_lines, key=lambda l: l.end) if seg_lines else ep.line(b)
            end = snap_end(ep, end_line, cfg, p.fixes)
        times.append(Segment(start, end))

    for i, s in enumerate(times):
        if s.end - s.start < 1.0:
            p.problems.append(f"stretch {i+1} is too short to use ({s.duration}s)")
        if i and s.start < times[i - 1].end - EPS:
            p.problems.append("stretches overlap in time")
    p.segments = times

    lo, hi = cfg["min_seconds"], cfg["max_seconds"]
    if p.duration < lo:
        p.problems.append(f"too short: {p.duration:.1f}s (minimum {lo}s)")
    if p.duration > hi:
        p.problems.append(f"too long: {p.duration:.1f}s (maximum {hi}s)")
    if hook.start - p.segments[0].start > cfg["hook_within_seconds"] + EPS:
        p.problems.append("hook does not start in the first 3 seconds")
    p.valid = not p.problems
    return p


def overlap_ratio(a: Promo, b: Promo) -> float:
    """Shared seconds / length of the shorter promo (same episode only)."""
    if a.episode != b.episode or not a.segments or not b.segments:
        return 0.0
    inter = 0.0
    for x in a.segments:
        for y in b.segments:
            inter += max(0.0, min(x.end, y.end) - max(x.start, y.start))
    return inter / max(min(a.duration, b.duration), 1e-6)
