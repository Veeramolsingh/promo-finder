"""Find promos in a folder of microdrama scripts.

Examples
    python run.py --input data/scripts
    python run.py --input data/scripts --episodes 1-5 --out outputs/test
    python run.py --input new_show/ --find-model groq:openai/gpt-oss-20b
    python run.py --input new_show/ --find-model anthropic:claude-opus-5-5 --judge-model groq:qwen/qwen3.8-27b
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path

import yaml
from dotenv import load_dotenv

from promo_finder.pipeline import run


def main() -> None:
    ap = argparse.ArgumentParser(description="Find and rank promo clips in microdrama scripts.")
    ap.add_argument("--input", required=True, help="folder of episode .txt scripts")
    ap.add_argument("--out", help="output folder (default: outputs/<show>_<timestamp>)")
    ap.add_argument("--episodes", help="subset, e.g. '1-5,8' (default: all)")
    ap.add_argument("--find-model", help="override finder model, e.g. groq:openai/gpt-oss-120b")
    ap.add_argument("--judge-model", help="override judge model, e.g. groq:qwen/qwen3.8-27b")
    ap.add_argument("--title", help="show title (default: the input folder's name)")
    ap.add_argument("--config", default="config.yaml")
    a = ap.parse_args()

    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows consoles
    load_dotenv()
    cfg = yaml.safe_load(Path(a.config).read_text(encoding="utf-8"))
    out = a.out or f"outputs/run_{datetime.now():%Y%m%d_%H%M%S}"
    run(a.input, out, cfg, episodes=a.episodes, find_model=a.find_model,
        judge_model=a.judge_model, title=a.title)


if __name__ == "__main__":
    main()
