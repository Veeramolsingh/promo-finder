"""Step 1 - READ (plain code, no AI).

Turns one screenplay .txt file into a list of timed lines.

The scripts follow a fixed format:

    **HEROINE (Confused, Breathless):**
    Start: (00:13.50) | End: (00:14.30)
    "Why?"

Block types: CAMERA (a shot), ACTION, SOUND FX, and everything else is a
character speaking. Timestamps are mm:ss.xx from the start of the episode.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from pathlib import Path

HEADER_RE = re.compile(r"^\*\*(.+?):\*\*\s*$")
TIME_RE = re.compile(r"^Start:\s*\((\d+):(\d+(?:\.\d+)?)\)\s*\|\s*End:\s*\((\d+):(\d+(?:\.\d+)?)\)\s*$")
SCENE_RE = re.compile(r"^SCENE\s+(\d+)")
META_RE = re.compile(r"^(EPISODE NUMBER|EPISODE LOGLINE|CONTINUITY NOTES):\s*(.*)$")
FILE_EP_RE = re.compile(r"episode-(\d+)", re.I)

SPECIAL_KINDS = {"CAMERA": "camera", "ACTION": "action", "SOUND FX": "sfx"}
VOICE_MODIFIERS = {"O.S.", "V.O.", "O.C.", "CONT'D", "CONTINUES"}


@dataclass
class Line:
    idx: int                 # position in the episode (0-based, script order)
    kind: str                # camera | action | sfx | dialogue
    start: float             # seconds from episode start
    end: float
    text: str
    speaker: str | None = None
    tone: str | None = None  # e.g. "Cold, Commanding"
    scene: int = 1

    @property
    def is_spoken_or_seen(self) -> bool:
        """Lines the AI reads. Camera lines are only used by code, for cut points."""
        return self.kind != "camera"


@dataclass
class Episode:
    number: int
    source_file: str
    logline: str
    continuity: str
    lines: list[Line] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return max((l.end for l in self.lines), default=0.0)

    @property
    def shots(self) -> list[Line]:
        return [l for l in self.lines if l.kind == "camera"]

    @property
    def content_lines(self) -> list[Line]:
        return [l for l in self.lines if l.is_spoken_or_seen]

    def line(self, idx: int) -> Line:
        return self.lines[idx]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["duration"] = self.duration
        return d


def _secs(mm: str, ss: str) -> float:
    return round(int(mm) * 60 + float(ss), 2)


def _split_header(header: str) -> tuple[str, str | None, str | None]:
    """'MATRIARCH (O.S.) (Firm)' -> ('dialogue', 'MATRIARCH', 'Firm')."""
    if header.strip().upper() in SPECIAL_KINDS:
        return SPECIAL_KINDS[header.strip().upper()], None, None
    name = header.split("(")[0].strip()
    parens = re.findall(r"\(([^)]*)\)", header)
    tones = []
    for p in parens:
        parts = [x.strip() for x in p.split("/")]
        parts = [x for x in parts if x.upper() not in VOICE_MODIFIERS]
        if parts:
            tones.append(" / ".join(parts))
    return "dialogue", name, (", ".join(tones) or None)


def fmt_time(t: float) -> str:
    m, s = divmod(t, 60)
    return f"{int(m):02d}:{s:05.2f}"


def parse_episode(path: str | Path) -> Episode:
    path = Path(path)
    raw = path.read_text(encoding="utf-8", errors="replace").splitlines()

    meta = {}
    for row in raw[:10]:
        m = META_RE.match(row.strip())
        if m:
            meta[m.group(1)] = m.group(2).strip()

    # Episode number: the script's own header first, then the file name; 0 = unknown
    # (load_show then numbers unknown episodes by file order).
    num = 0
    m = re.search(r"(\d+)", meta.get("EPISODE NUMBER", ""))
    if m:
        num = int(m.group(1))
    else:
        m = FILE_EP_RE.search(path.name) or re.search(r"(\d+)", path.stem)
        num = int(m.group(1)) if m else 0

    ep = Episode(number=num, source_file=path.name,
                 logline=meta.get("EPISODE LOGLINE", ""),
                 continuity=meta.get("CONTINUITY NOTES", ""))

    scene = 1
    i = 0
    while i < len(raw):
        row = raw[i].strip()
        sm = SCENE_RE.match(row)
        if sm:
            scene = int(sm.group(1))
            i += 1
            continue
        hm = HEADER_RE.match(row)
        if not hm:
            i += 1
            continue
        header = hm.group(1)
        # next non-empty row must be the timestamp
        j = i + 1
        while j < len(raw) and not raw[j].strip():
            j += 1
        tm = TIME_RE.match(raw[j].strip()) if j < len(raw) else None
        if not tm:
            ep.warnings.append(f"line {i+1}: block '{header}' has no timestamp - skipped")
            i += 1
            continue
        start, end = _secs(tm.group(1), tm.group(2)), _secs(tm.group(3), tm.group(4))
        # text = following rows until a blank row / next header
        k = j + 1
        text_rows = []
        while k < len(raw) and raw[k].strip() and not HEADER_RE.match(raw[k].strip()):
            text_rows.append(raw[k].strip())
            k += 1
        kind, speaker, tone = _split_header(header)
        text = " ".join(text_rows).strip()
        if end < start:
            ep.warnings.append(f"line {i+1}: end {end} before start {start} - swapped")
            start, end = end, start
        ep.lines.append(Line(idx=len(ep.lines), kind=kind, start=start, end=end,
                             text=text, speaker=speaker, tone=tone, scene=scene))
        i = k

    if not ep.lines:
        ep.warnings.append("no timed lines found - is this the expected script format?")
    return ep


def load_show(folder: str | Path) -> list[Episode]:
    files = sorted(Path(folder).glob("*.txt"))
    eps = [parse_episode(f) for f in files]
    nums = [e.number for e in eps]
    if any(n <= 0 for n in nums) or len(set(nums)) != len(nums):
        # Missing or clashing episode numbers: fall back to alphabetical file order (1, 2, 3...)
        for i, e in enumerate(eps, 1):
            if e.number != i:
                e.warnings.append(f"no unique episode number found - numbered {i} by file order")
            e.number = i
    eps.sort(key=lambda e: e.number)
    return eps


def show_title(folder: str | Path) -> str:
    """The show title is taken from the folder name (that is how the input pack ships)."""
    name = Path(folder).resolve().name
    return name if name.lower() not in {"scripts", "data", "input"} else ""


def render_for_ai(ep: Episode) -> str:
    """Compact numbered view the AI reads: dialogue, action and sound - no camera notes."""
    out = []
    for l in ep.content_lines:
        t = f"[{fmt_time(l.start)}-{fmt_time(l.end)}]"
        if l.kind == "dialogue":
            tone = f" ({l.tone})" if l.tone else ""
            out.append(f"L{l.idx} {t} {l.speaker}{tone}: {l.text}")
        elif l.kind == "action":
            out.append(f"L{l.idx} {t} ACTION: {l.text}")
        else:
            out.append(f"L{l.idx} {t} SOUND: {l.text}")
    return "\n".join(out)


if __name__ == "__main__":
    import sys
    eps = load_show(sys.argv[1])
    total = 0
    for e in eps:
        kinds = {}
        for l in e.lines:
            kinds[l.kind] = kinds.get(l.kind, 0) + 1
        total += len(e.lines)
        print(f"ep {e.number:3d}  {fmt_time(e.duration)}  lines={len(e.lines):3d} {kinds}  warnings={len(e.warnings)}")
        for w in e.warnings:
            print("    !", w)
    print("episodes:", len(eps), "total blocks:", total)
