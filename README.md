# Promo Finder

Reads microdrama screenplays and finds the best 20-60 second promo clips: each opens on a
hook in the first 3 seconds, ends on a cliffhanger, and makes sense to someone who has never
seen the show. Output: every promo's episode and segment timestamps, hook, cliffhanger, why
it works, and a ranked "post these first" list.

## How it works

```
scripts (.txt) -> 1 READ (code) -> 2 FLAG (code) -> 3 FIND (AI) -> 4 CHECK (code)
               -> 5 JUDGE (AI) -> 6 RANK (code) -> report.md / promos.csv / promos.json / raw/
```

| Step | Who | What |
|---|---|---|
| 1 Read | code | Parses each script into timed lines: speaker, tone, action, sound, camera shots. |
| 2 Flag | code | Keyword and emotion rules score likely hooks (slap, reveal, shouting). Given to the AI as hints and used as a no-AI baseline. |
| 3 Find | AI | Reads one episode and proposes 2-3 promos **as line numbers**, plus hook, cliffhanger and why. |
| 4 Check | code | Turns line numbers into timestamps. Enforces the brief: hook within 3s, ends on the cliffhanger, 20-60s, no cut mid-sentence, cuts snapped to camera shot changes. Rejects or fixes, and records why. |
| 5 Judge | a different AI | Scores each clip as a stranger scrolling TikTok would: hook, cliffhanger, clarity (1-5) and spoiler level. It sees only the clip and the show title. |
| 6 Rank | code (+1 AI call per show) | `0.35 hook + 0.30 cliffhanger + 0.25 clarity + 0.10 keyword signal - 1 for a major spoiler`. The shortlist keeps score 3+, clarity 3+, one promo per episode, and one per story moment. Consecutive episodes replay the same scene, so one judge call groups the top candidates by moment. |

Key design choices:
- **The AI never writes timestamps.** It points at line numbers and code computes the times, so it cannot make them up.
- **The finder and judge are different model families**, so the judge is not grading its own work.
- **Any model can be swapped in with one setting:** `--find-model provider:model`. Groq, Gemini, OpenAI, Anthropic and any OpenAI-compatible endpoint are already configured.
- **Every AI request and reply is saved unedited** in `raw/`, and answers are cached, so re-running costs nothing.
- **Two AI calls per episode** (find + judge), plus one retry when all of an episode's ideas fail the checks, plus one call per show to group story moments. Measured cost: about $0.16 per 50-episode show at Groq's paid prices ($0 on the free tier).

## Run it

```bash
python -m venv .venv
.venv\Scripts\activate            # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
```

Create a `.env` file with a free Groq key (https://console.groq.com/keys):

```
GROQ_API_KEY=your_key_here
```

Command line:

```bash
python run.py --input "data/<Show Title>"                     # all episodes
python run.py --input "data/<Show Title>" --episodes 1-5      # a subset
python run.py --input new_show/ --find-model anthropic:claude-opus-5-5   # stronger model (needs ANTHROPIC_API_KEY)
python run.py --input new_show/ --find-model custom:<model>   # any OpenAI-compatible API (set CUSTOM_BASE_URL + CUSTOM_API_KEY)
```

Try it without any confidential data: `samples/fake_show_sample.zip` is an invented one-episode
show in the same script format (`python run.py --input "samples/My Stepsister Stole My Fiance. I Bought His Company"`).

Episode numbers come from each script's `EPISODE NUMBER:` line, then from a number in the file name.
Files with neither get the next free numbers in file order, with a warning. Files that aren't in
the timed script format are flagged and skipped, so they never cost an AI call.

Web app (live: https://veersinghkuku.streamlit.app; upload a .zip of scripts, get ranked promos):

```bash
streamlit run app.py
```

The input folder's name is used as the show title (the judge needs it to tell a premise
reveal from a real spoiler). Override it with `--title`.

## Check quality

```bash
python -m promo_finder.labelkit --input "data/<Show Title>"   # makes gold/ label sheets
# hand-pick promos for those episodes in gold/gold.csv, BEFORE looking at the tool's output
python evaluate.py --run outputs/<run> --gold gold/gold.csv
```

`evaluate.py` scores the AI pipeline **and** two no-AI baselines ("last 45 seconds of the
episode", "keyword rules") against your picks. If the AI cannot beat the baselines, it is not
adding value.

`python rerank.py --run outputs/<run>` re-ranks a finished run with the current `config.yaml`
and makes no AI calls.

## Files

| Path | What |
|---|---|
| `run.py`, `app.py` | command line and web app |
| `config.yaml` | models, the brief's rules, ranking weights |
| `promo_finder/parse.py` | 1 Read |
| `promo_finder/signals.py` | 2 Flag |
| `promo_finder/prompts.py` | the two prompts (find, judge) |
| `promo_finder/llm.py` | the only code that talks to a model; caching; free-tier quota waits |
| `promo_finder/check.py` | 4 Check |
| `promo_finder/rank.py`, `promo_finder/beats.py` | 6 Rank, grouping by story moment |
| `promo_finder/pipeline.py`, `report.py` | glue and outputs |
| `promo_finder/baselines.py`, `evaluate.py`, `promo_finder/labelkit.py` | quality checking |

## Limits (known, by design)

- It reads scripts, not video, so acting, pacing and visuals are judged from the text only.
- Promos join stretches within one episode only, not across episodes.
- The judge is an AI too. Its scores give a direction, not the truth, which is why the hand-picked comparison exists.
- Free tier: about 200K tokens/day per model on Groq, and a full 50-episode show needs about 230K. The tool waits for the allowance to refill rather than failing.

**Confidentiality:** the input scripts (`data/`) and run outputs (`outputs/`) are excluded from git. Never publish them.
