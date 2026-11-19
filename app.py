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
    choices = list(dict.fromkeys([CFG["models"]["find"], CFG["models"]["judge"],
                                  *CFG.get("model_params", {}).keys(), "Other (type below)"]))

    def pick_model(label: str, default: str, key: str) -> str:
        choice = st.selectbox(label, choices, index=choices.index(default), key=key,
                              help="provider:model. Each Groq model has its own free daily allowance.")
        if choice.startswith("Other"):
            return st.text_input(f"{label} (provider:model)", "anthropic:claude-opus-5-5", key=key + "_txt")
        return choice

    find_model = pick_model("Finder model", CFG["models"]["find"], "find_model")
    judge_model = pick_model("Judge model", CFG["models"]["judge"], "judge_model")
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


@st.cache_data(show_spinner=False)
def episodes_in_zip(data: bytes) -> list[int]:
    """Episode numbers in an uploaded zip, read the same way the pipeline reads them."""
    from promo_finder.parse import load_show
    tmp = Path(tempfile.mkdtemp(prefix="peek_"))
    folder, _ = extract_zip(data, tmp)
    return [e.number for e in load_show(folder)]


# Saved runs live in outputs/, which is local only (it quotes the scripts, so it is never deployed).
# Show the "previous run" tab only where saved runs exist, i.e. when running on your own machine.
SAVED_RUNS = sorted([p.parent for p in (ROOT / "outputs").glob("*/promos.json")],
                    key=lambda p: p.stat().st_mtime, reverse=True) if (ROOT / "outputs").exists() else []
if SAVED_RUNS:
    tab_run, tab_view = st.tabs(["Run on a show", "Open a previous run"])
else:
    tab_run, tab_view = st.container(), None

with tab_run:
    up = st.file_uploader("Upload a .zip of episode scripts (.txt)", type=["zip"])
    episodes = None
    nums: list[int] = []
    if up is not None:
        try:
            nums = episodes_in_zip(up.getvalue())
        except zipfile.BadZipFile:
            st.error("That file isn't a valid zip.")
        if not nums and up is not None:
            st.warning("No .txt episode scripts found in this zip.")
        elif len(nums) == 1:
            st.caption(f"1 episode found (episode {nums[0]}).")
        else:
            lo, hi = min(nums), max(nums)
            a, b = st.slider("Episodes to process", lo, hi, (lo, min(lo + 4, hi)),
                             help="Each episode uses about 4K tokens. Groq's free tier allows about "
                                  "200K per model per day, so roughly 45 episodes a day.")
            chosen = [n for n in nums if a <= n <= b]
            st.caption(f"{len(nums)} episodes in the zip · processing {len(chosen)} "
                       f"(episode {a} to {b}).")
            episodes = f"{a}-{b}"
    go = st.button("Find promos", type="primary", disabled=not nums)
    if go and up is not None:
        tmp = Path(tempfile.mkdtemp(prefix="promo_"))
        (tmp / "scripts").mkdir()
        scripts, guessed = extract_zip(up.getvalue(), tmp / "scripts")
        out = tmp / "out"
        with st.status("Finding promos...", expanded=True) as status:
            log_box = st.empty()
            lines: list[str] = []

            def log(msg: str) -> None:
                lines.append(msg)
                log_box.code("\n".join(lines[-12:]))

            try:
                meta = run(str(scripts), str(out), CFG, episodes=episodes,
                           find_model=find_model, judge_model=judge_model,
                           title=title or guessed or None, log=log)
                st.session_state["result_dir"] = str(out)
                status.update(label=f"Done: {meta['episodes_processed']} episode(s) in "
                                    f"{meta['seconds']:.0f}s", state="complete", expanded=False)
            except Exception as e:  # show the reason instead of a stack trace
                status.update(label="Run failed", state="error", expanded=True)
                st.error(f"Run failed: {e}")


def _esc(s: str) -> str:
    """Stop '$' in script text being read as maths by Streamlit's markdown."""
    return (s or "").replace("$", "\\$")


def _where(p: dict) -> str:
    return "  +  ".join(seg.replace("-", " → ") for seg in p["timestamps"].split("; "))


SPOILER_TEXT = {"none": "no spoiler", "premise": "shows the title's premise", "major": "major spoiler"}


def _promo_details(p: dict) -> None:
    """Title, hook, cliffhanger, why, and the judge's view: the same for best and other candidates."""
    s = p["scores"]
    st.markdown(f"**{_esc(p['title'])}**  \n"
                f"**Hook:** {_esc(p['hook'])}  \n"
                f"**Cliffhanger:** {_esc(p['cliffhanger'])}  \n"
                f"**Why it could work:** {_esc(p['why'])}")
    st.caption(f"Judge: hook {s.get('hook')}/5 · cliffhanger {s.get('cliffhanger')}/5 · "
               f"clarity {s.get('clarity')}/5 · {SPOILER_TEXT.get(s.get('spoiler'), '')}. "
               f"{_esc(s.get('verdict', ''))}")
    if s.get("confusing"):
        st.caption(f"Might confuse a new viewer: {_esc(s['confusing'])}")


def show_results(out: Path, key: str) -> None:
    data = json.loads((out / "promos.json").read_text(encoding="utf-8"))
    run_meta, promos = data["run"], data["promos"]
    top_picks = set(data["shortlist"])
    ranked = sorted((p for p in promos if p.get("rank")), key=lambda p: p["rank"])
    by_ep: dict[int, list[dict]] = {}
    for p in ranked:                       # each episode's promos, best first
        by_ep.setdefault(p["episode"], []).append(p)

    stopped = run_meta.get("stopped_early")
    if stopped and "daily limit" in stopped:
        m = re.search(r"try again in ([\dhms.]+)", stopped)
        when = f" It refills in about {m.group(1).split('.')[0]}s." if m else ""
        used = run_meta.get("find_model", "")
        alt = next((c for c in ("groq:openai/gpt-oss-120b", "groq:openai/gpt-oss-20b") if c != used), "")
        st.warning(f"**The free daily token allowance ran out** after "
                   f"{run_meta['episodes_processed']} episode(s).{when} To keep going now, pick a different "
                   f"**Finder model** in the sidebar (for example `{alt}`, which has its own allowance), "
                   f"or try again later. Anything already finished is shown below.")
        with st.expander("Technical detail"):
            st.code(stopped)
    elif stopped:
        st.warning(stopped)

    # ---- one card per episode: that episode's best promo
    if not by_ep:
        st.info("No usable promo was found in this run.")
        return
    head_l, head_r = st.columns([3, 2])
    head_l.subheader("Best promo in each episode")
    order = head_r.radio("Order", ["Best first", "Episode order"], horizontal=True,
                         key=f"{key}_order", label_visibility="collapsed")
    best = [lst[0] for lst in by_ep.values()]
    best.sort(key=(lambda p: p["rank"]) if order == "Best first" else (lambda p: p["episode"]))
    st.caption(f"{len(best)} episode(s) with a usable promo. \"Post first\" marks the top picks to "
               f"publish first. Score is out of 5.")

    # promos showing the same story moment as a better one (consecutive episodes replay scenes)
    first_in_group: dict[int, int] = {}
    same_as: dict[str, int] = {}
    for p in sorted(best, key=lambda p: p["rank"]):
        g = p.get("story_group")
        if g is None:
            continue
        if g in first_in_group:
            same_as[p["id"]] = first_in_group[g]
        else:
            first_in_group[g] = p["episode"]

    for p in best:
        with st.container(border=True):
            left, right = st.columns([5, 1])
            tag = "  :green-background[Post first]" if p["id"] in top_picks else ""
            left.markdown(f"**Episode {p['episode']}** · {_where(p)} · {p['duration']:.0f}s{tag}")
            right.markdown(f"<div style='text-align:right'>Score <b>{p['final_score']}</b></div>",
                           unsafe_allow_html=True)
            _promo_details(p)
            if p["id"] in same_as:
                st.caption(f"Same story moment as the Episode {same_as[p['id']]} promo.")
            alts = by_ep[p["episode"]][1:]
            if alts:
                n = len(alts)
                with st.expander(f"{n} other candidate{'s' if n > 1 else ''} in this episode (lower score)"):
                    for i, a in enumerate(alts, 2):
                        if i > 2:
                            st.divider()
                        al, ar = st.columns([5, 1])
                        al.markdown(f"**Option {i}** · {_where(a)} · {a['duration']:.0f}s")
                        ar.markdown(f"<div style='text-align:right'>Score <b>{a['final_score']}</b></div>",
                                    unsafe_allow_html=True)
                        _promo_details(a)

    processed = sorted(run_meta.get("episodes_requested") or []) if not stopped else \
        sorted({p["episode"] for p in promos})
    empty = [e for e in processed if e not in by_ep]
    if empty:
        st.caption(f"No usable promo in episode(s): {', '.join(map(str, empty))}.")
    st.caption(f"Finder `{run_meta.get('find_model', '?')}` · judge `{run_meta.get('judge_model', '?')}`")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in out.rglob("*"):
            if f.is_file():
                z.write(f, f.relative_to(out))
    d1, d2, _ = st.columns([1, 2, 2])
    d1.download_button("Download CSV", (out / "promos.csv").read_bytes(), "promos.csv", key=f"{key}_csv")
    d2.download_button("Download full results (report + raw AI outputs)", buf.getvalue(),
                       "promo_run.zip", key=f"{key}_zip")


with tab_run:
    if st.session_state.get("result_dir"):
        show_results(Path(st.session_state["result_dir"]), key="current")

if tab_view is not None:
    with tab_view:
        pick = st.selectbox("Run", SAVED_RUNS, format_func=lambda p: p.name)
        show_results(pick, key="previous")
