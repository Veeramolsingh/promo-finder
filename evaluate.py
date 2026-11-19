"""Compare a run against hand-picked promos (the "gold set"), and against two no-AI baselines.

    python evaluate.py --run outputs/full_run --gold gold/gold.csv

gold.csv columns: episode, segments, hook, cliffhanger, notes
  segments = "mm:ss-mm:ss" or several joined by ";"  e.g. "00:11.3-00:52.0; 01:57.0-02:18.0"

For each hand-picked promo we ask:
  * Top-1 match: does the tool's best promo for that episode overlap it by >= 50% (IoU)?
  * Top-3 match: does any of the tool's top 3 promos for that episode?
  * Hook match: does a top-3 promo start within 3 seconds of the hand-picked start?
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path

import yaml

from promo_finder.baselines import last45, rules
from promo_finder.parse import load_show

TIME_RE = re.compile(r"(\d+):(\d+(?:\.\d+)?)\s*-\s*(\d+):(\d+(?:\.\d+)?)")


def parse_segments(s: str) -> list[tuple[float, float]]:
    out = []
    for m in TIME_RE.finditer(s or ""):
        a = int(m.group(1)) * 60 + float(m.group(2))
        b = int(m.group(3)) * 60 + float(m.group(4))
        out.append((min(a, b), max(a, b)))
    return out


def iou(a: list[tuple[float, float]], b: list[tuple[float, float]]) -> float:
    inter = sum(max(0.0, min(x2, y2) - max(x1, y1)) for x1, x2 in a for y1, y2 in b)
    total = sum(x2 - x1 for x1, x2 in a) + sum(y2 - y1 for y1, y2 in b) - inter
    return inter / total if total > 0 else 0.0


def evaluate(system: dict[int, list[list[tuple[float, float]]]], gold: list[dict]) -> dict:
    rows, top1, top3, hook = [], 0, 0, 0
    for g in gold:
        cands = system.get(g["episode"], [])
        ious = [iou(c, g["segments"]) for c in cands[:3]]
        best = max(ious, default=0.0)
        t1 = bool(ious) and ious[0] >= 0.5
        t3 = best >= 0.5
        h = any(abs(c[0][0] - g["segments"][0][0]) <= 3.0 for c in cands[:3] if c)
        top1 += t1
        top3 += t3
        hook += h
        rows.append({"episode": g["episode"], "gold": g["raw"], "best_iou_top3": round(best, 2),
                     "top1_match": t1, "top3_match": t3, "hook_match": h})
    n = max(len(gold), 1)
    return {"n": len(gold), "top1": round(top1 / n, 2), "top3": round(top3 / n, 2),
            "hook_start_within_3s": round(hook / n, 2), "rows": rows}


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True, help="output folder of a run (has promos.json)")
    ap.add_argument("--gold", default="gold/gold.csv")
    ap.add_argument("--config", default="config.yaml")
    a = ap.parse_args()

    cfg = yaml.safe_load(Path(a.config).read_text(encoding="utf-8"))
    run = json.loads((Path(a.run) / "promos.json").read_text(encoding="utf-8"))
    eps = {e.number: e for e in load_show(run["run"]["input_dir"])}

    gold = []
    with open(a.gold, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            segs = parse_segments(r.get("segments", ""))
            if r.get("episode", "").strip() and segs:
                gold.append({"episode": int(r["episode"]), "segments": segs, "raw": r["segments"]})
    if not gold:
        raise SystemExit(f"No filled-in rows in {a.gold} yet.")

    # AI system: ranked promos per episode, best first
    ai: dict[int, list] = {}
    ranked = sorted((p for p in run["promos"] if p.get("rank")), key=lambda p: p["rank"])
    for p in ranked:
        ai.setdefault(p["episode"], []).append([(s["start"], s["end"]) for s in p["segments"]])

    def baseline(fn):
        out = {}
        for g in gold:
            ep = eps[g["episode"]]
            p = fn(ep, cfg["rules"])
            out[g["episode"]] = [[(s.start, s.end) for s in p.segments]] if p.segments else []
        return out

    results = {"AI pipeline": evaluate(ai, gold),
               "Baseline: last 45s of episode": evaluate(baseline(last45), gold),
               "Baseline: keyword rules": evaluate(baseline(rules), gold)}

    lines = ["# Evaluation against hand-picked promos\n",
             f"Run: `{a.run}` | gold set: `{a.gold}` ({len(gold)} promos across "
             f"{len({g['episode'] for g in gold})} episodes)\n",
             "A match means the time ranges overlap by at least 50% (intersection over union).\n",
             "| System | Top-1 match | Top-3 match | Hook start within 3s (top 3) |",
             "|---|---|---|---|"]
    for name, r in results.items():
        lines.append(f"| {name} | {r['top1']:.0%} | {r['top3']:.0%} | {r['hook_start_within_3s']:.0%} |")
    lines.append("\n## Per hand-picked promo (AI pipeline)\n")
    lines.append("| Episode | Hand-picked | Best overlap (top 3) | Top-1 | Top-3 | Hook |")
    lines.append("|---|---|---|---|---|---|")
    for r in results["AI pipeline"]["rows"]:
        lines.append(f"| {r['episode']} | {r['gold']} | {r['best_iou_top3']} | "
                     f"{'yes' if r['top1_match'] else 'no'} | {'yes' if r['top3_match'] else 'no'} | "
                     f"{'yes' if r['hook_match'] else 'no'} |")
    out = Path(a.run) / "evaluation.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[:9 + len(results)]))
    print(f"\nWritten to {out}")


if __name__ == "__main__":
    main()
