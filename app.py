"""Promo Finder - web app for an editor.

    streamlit run app.py

Upload a .zip of episode scripts, press Run, get ranked promos.
Scripts are processed in a temporary folder and never stored by the app.
"""

from __future__ import annotations

import io
import json
import os
import re
import tempfile
import zipfile
from pathlib import Path

import streamlit as st
import yaml
from dotenv import load_dotenv

from promo_finder.pipeline import run

ROOT = Path(__file__).parent
load_dotenv(ROOT / ".env")
# In a web app, don't sit for 20 minutes waiting for a free-tier daily quota to refill:
# wait briefly, then show what finished plus a clear "limit reached" note.
os.environ.setdefault("MAX_QUOTA_WAIT_SECONDS", "90")
CFG = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))

st.set_page_config(page_title="Promo Finder", page_icon="🎬", layout="wide")
st.title("🎬 Promo Finder")
st.caption("Finds 20-60 second promo clips in microdrama screenplays: hook in the first 3 seconds, "
           "cliffhanger at the cut, makes sense to a stranger.")

# ---- keys: Streamlit secrets (hosted) > .env (local) > typed in the sidebar
for k in ("GROQ_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "OPENAI_API_KEY",
          "CUSTOM_API_KEY", "CUSTOM_BASE_URL"):
    try:
        if k in st.secrets and not os.environ.get(k):
            os.environ[k] = st.secrets[k]
    except Exception:
        pass

with st.sidebar:
    st.header("Settings")
    find_model = st.text_input("Finder model", CFG["models"]["find"],
                               help="provider:model, e.g. groq:openai/gpt-oss-120b, anthropic:claude-opus-5-5")
    judge_model = st.text_input("Judge model", CFG["models"]["judge"])
    episodes = st.text_input("Episodes (blank = all)", "1-5",
                             help="A full 50-episode show uses about 200K tokens, which is the whole "
                                  "free daily allowance of one Groq model. Start with a few episodes.")
    title = st.text_input("Show title (used for spoiler judgement)", "",
                          placeholder="defaults to the folder name in the zip")
    with st.expander("API keys"):
        st.caption("Leave blank to use keys configured on the server.")
        for k in ("GROQ_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY"):
            v = st.text_input(k, type="password", key=f"key_{k}")
            if v:
                os.environ[k] = v
    st.markdown("**Rules (from config.yaml)**")
    r = CFG["rules"]
    st.caption(f"{r['min_seconds']}-{r['max_seconds']}s long · hook within {r['hook_within_seconds']}s · "
               f"up to {r['max_segments']} stretches")


def extract_zip(data: bytes, dest: Path) -> tuple[Path, str]:
    """Extract only .txt files, flattened (no paths from the zip are trusted)."""
    folder_names = set()
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for info in z.infolist():
            name = info.filename.replace("\\", "/")
            if info.is_dir() or not name.lower().endswith(".txt"):
                continue
            parts = name.split("/")
            if len(parts) > 1:
                folder_names.add(parts[-2])
            (dest / Path(parts[-1]).name).write_bytes(z.read(info))
    guess = folder_names.pop() if len(folder_names) == 1 else ""
    return dest, guess


tab_run, tab_view = st.tabs(["Run on a show", "Open a previous run"])

with tab_run:
    up = st.file_uploader("Upload a .zip of episode scripts (.txt)", type=["zip"])
    go = st.button("Find promos", type="primary", disabled=up is None)
    if go and up is not None:
        tmp = Path(tempfile.mkdtemp(prefix="promo_"))
        (tmp / "scripts").mkdir()
        scripts, guessed = extract_zip(up.getvalue(), tmp / "scripts")
        out = tmp / "out"
        log_box = st.empty()
        lines: list[str] = []

        def log(msg: str) -> None:
            lines.append(msg)
            log_box.code("\n".join(lines[-15:]))

        try:
            with st.spinner("Reading scripts and asking the models..."):
                run(str(scripts), str(out), CFG, episodes=episodes or None,
                    find_model=find_model, judge_model=judge_model,
                    title=title or guessed or None, log=log)
            st.session_state["result_dir"] = str(out)
        except Exception as e:  # show the reason instead of a stack trace
            st.error(f"Run failed: {e}")


def table(rows: list[dict]) -> None:
    """Sortable table when pandas/pyarrow load; plain table otherwise (e.g. very long Windows paths)."""
    if not rows:
        st.caption("None.")
        return
    try:
        import pyarrow.compute  # noqa: F401  (st.dataframe needs it)
        st.dataframe(rows, use_container_width=True, hide_index=True)
    except Exception:
        cols = list(rows[0])
        md = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
        for r in rows:
            md.append("| " + " | ".join(str(r[c]).replace("|", "/") for c in cols) + " |")
        st.markdown("\n".join(md))


def show_results(out: Path, key: str) -> None:
    data = json.loads((out / "promos.json").read_text(encoding="utf-8"))
    run_meta, promos = data["run"], data["promos"]
    by_id = {p["id"]: p for p in promos}
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Episodes", run_meta["episodes_processed"])
    c2.metric("Ideas from AI", data["quality"]["ideas_from_ai"])
    c3.metric("Passed checks", data["quality"]["passed_checks"])
    c4.metric("AI calls", run_meta["calls"])
    stopped = run_meta.get("stopped_early")
    if stopped and "daily limit" in stopped:
        m = re.search(r"try again in ([\dhms.]+)", stopped)
        when = f" It refills in about {m.group(1).split('.')[0]}s." if m else ""
        st.warning(f"**The free daily token allowance ran out** after "
                   f"{run_meta['episodes_processed']} episode(s).{when} To keep going now, change the "
                   f"**Finder model** in the sidebar (for example `groq:openai/gpt-oss-20b`, which has its "
                   f"own allowance), or try again later. Anything already finished is shown below.")
        with st.expander("Technical detail"):
            st.code(stopped)
    elif stopped:
        st.warning(stopped)

    st.subheader("Post these first")
    if not data["shortlist"]:
        st.info("No promo made the shortlist in this run.")
    for i, pid in enumerate(data["shortlist"], 1):
        p = by_id[pid]
        s = p["scores"]
        with st.container(border=True):
            st.markdown(f"**{i}. {p['title']}** — score {p['final_score']} · {p['duration']:.0f}s")
            st.markdown("**Where:** " + "; then ".join(
                f"Episode {p['episode']} {seg}" for seg in p["timestamps"].split("; ")))
            st.markdown(f"**Hook:** {p['hook']}  \n**Cliffhanger:** {p['cliffhanger']}  \n"
                        f"**Why it works:** {p['why']}")
            st.caption(f"Judge: hook {s.get('hook')}/5 · cliffhanger {s.get('cliffhanger')}/5 · "
                       f"clarity {s.get('clarity')}/5 · spoiler {s.get('spoiler')} — {s.get('verdict', '')}")
            if p["fixes"]:
                st.caption("Code adjusted the cut: " + "; ".join(p["fixes"]))

    st.subheader("All ranked promos")
    rows = [{"rank": p["rank"], "id": p["id"], "episode": p["episode"], "timestamps": p["timestamps"],
             "seconds": p["duration"], "title": p["title"], "hook": p["scores"].get("hook"),
             "cliff": p["scores"].get("cliffhanger"), "clarity": p["scores"].get("clarity"),
             "spoiler": p["scores"].get("spoiler"), "score": p["final_score"]}
            for p in sorted((p for p in promos if p.get("rank")), key=lambda p: p["rank"])]
    table(rows)

    rejected = [p for p in promos if not p["valid"]]
    with st.expander(f"Ideas the code rejected ({len(rejected)})"):
        table([{"id": p["id"], "why": "; ".join(p["problems"])} for p in rejected])

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in out.rglob("*"):
            if f.is_file():
                z.write(f, f.relative_to(out))
    d1, d2, d3 = st.columns(3)
    d1.download_button("Download CSV", (out / "promos.csv").read_bytes(), "promos.csv", key=f"{key}_csv")
    d2.download_button("Download report", (out / "report.md").read_bytes(), "report.md", key=f"{key}_md")
    d3.download_button("Download everything (incl. raw AI outputs)", buf.getvalue(), "promo_run.zip",
                       key=f"{key}_zip")


with tab_run:
    if st.session_state.get("result_dir"):
        show_results(Path(st.session_state["result_dir"]), key="current")

with tab_view:
    runs = sorted([p.parent for p in (ROOT / "outputs").glob("*/promos.json")],
                  key=lambda p: p.stat().st_mtime, reverse=True) if (ROOT / "outputs").exists() else []
    if not runs:
        st.info("No previous runs on this machine.")
    else:
        pick = st.selectbox("Run", runs, format_func=lambda p: p.name)
        show_results(pick, key="previous")
