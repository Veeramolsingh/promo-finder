"""Re-rank a finished run with the current config.yaml.

    python rerank.py --run outputs/full_run

Use it to try different ranking weights or shortlist rules on the same AI answers.
Makes no new find/judge calls. The only AI call is the cheap once-per-show story-moment
grouping, and that comes from the cache when its input has not changed.
The raw find/judge outputs in <run>/raw/ are left untouched.
"""

import argparse
import json
import sys
from pathlib import Path

import yaml
from dotenv import load_dotenv

from promo_finder.check import Promo
from promo_finder.llm import LLM, LLMError
from promo_finder.parse import load_show
from promo_finder.pipeline import build_shortlist
from promo_finder.rank import rank_all, shortlist
from promo_finder.report import write_outputs


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--config", default="config.yaml")
    a = ap.parse_args()

    load_dotenv()
    cfg = yaml.safe_load(Path(a.config).read_text(encoding="utf-8"))
    out = Path(a.run)
    data = json.loads((out / "promos.json").read_text(encoding="utf-8"))
    meta = data["run"]
    promos = [Promo.from_dict(p) for p in data["promos"]]
    ranked = rank_all(promos, cfg["ranking"])
    try:
        judge = LLM(meta["judge_model"], cfg["providers"], cfg.get("model_params", {}).get(meta["judge_model"], {}))
        short = build_shortlist(ranked, cfg, judge, meta.get("show", ""), out / "raw")
    except LLMError as e:
        print(f"Story-moment grouping unavailable ({e}); shortlist without it.")
        short = shortlist(ranked, cfg["ranking"])
    eps = {e.number: e for e in load_show(meta["input_dir"])}
    write_outputs(out, promos, ranked, short, meta, cfg, eps)
    print(f"Re-ranked {len(ranked)} promos; shortlist of {len(short)}. Updated {out}/report.md, "
          f"promos.csv, promos.json")


if __name__ == "__main__":
    main()
