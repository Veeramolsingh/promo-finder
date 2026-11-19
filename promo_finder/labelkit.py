"""Makes the hand-labelling kit for the gold set.

    python -m promo_finder.labelkit --input "data/<show>" --episodes 1,3,8,15,25,35,42,50

Writes gold/label_sheets/epNNN.md (readable script with timestamps) and gold/gold.csv
(empty rows to fill in). Label BEFORE looking at the tool's output, so you are not anchored.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

from .parse import fmt_time, load_show

GUIDE = """# How to hand-pick promos (about 10 minutes per episode)

Read the episode below as a promo editor. Pick the 1-2 best promo clips:

- **Hook:** something in the first 3 seconds that stops a scrolling viewer
  (slap, reveal, cruel or shocking line). Your clip must start on it.
- **Cliffhanger:** the clip ends at an unresolved moment. Cut before the answer.
- **Stands alone:** a stranger can follow it.
- **20-60 seconds.** It can be one stretch, or up to 3 stretches from this episode.

Write each pick as one row in `gold/gold.csv`:

| episode | segments | hook | cliffhanger | notes |
|---|---|---|---|---|
| 1 | 00:11.3-00:52.0 | "You should turn around" | "You knew this..." | why you picked it |

Use the start time of your first line and the end time of your last line.
For several stretches: `00:11.3-00:30.0; 01:57.0-02:18.0`.

Do this **before** you look at the tool's output.
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--episodes", default="1,3,8,15,25,35,42,50")
    ap.add_argument("--out", default="gold")
    a = ap.parse_args()

    wanted = [int(x) for x in a.episodes.split(",")]
    eps = {e.number: e for e in load_show(a.input)}
    out = Path(a.out)
    sheets = out / "label_sheets"
    sheets.mkdir(parents=True, exist_ok=True)
    (out / "HOW_TO_LABEL.md").write_text(GUIDE, encoding="utf-8")

    for n in wanted:
        ep = eps[n]
        rows = [f"# Episode {n}  (length {fmt_time(ep.duration)})\n",
                f"**Summary:** {ep.logline}\n", f"**Context:** {ep.continuity}\n", "```"]
        for l in ep.lines:  # every block, including camera shots, so the timeline has no gaps
            t = f"{fmt_time(l.start)}-{fmt_time(l.end)}"
            if l.kind == "camera":
                rows.append(f"{t}  [SHOT] {l.text}")
            elif l.kind == "dialogue":
                tone = f" ({l.tone})" if l.tone else ""
                rows.append(f"{t}  {l.speaker}{tone}: {l.text}")
            elif l.kind == "action":
                rows.append(f"{t}  [ACTION] {l.text}")
            else:
                rows.append(f"{t}  [SOUND] {l.text}")
        rows.append("```")
        (sheets / f"ep{n:03d}.md").write_text("\n".join(rows) + "\n", encoding="utf-8")

    gold = out / "gold.csv"
    if not gold.exists():
        with open(gold, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["episode", "segments", "hook", "cliffhanger", "notes"])
            for n in wanted:
                w.writerow([n, "", "", "", ""])
    print(f"Label kit written to {out}/ ({len(wanted)} episodes)")


if __name__ == "__main__":
    main()
