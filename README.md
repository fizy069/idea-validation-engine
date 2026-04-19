# OASIS Idea Validator

A small Python CLI that uses [OASIS](https://docs.oasis.camel-ai.org/introduction)
(Open Agent Social Interaction Simulations) by camel-ai to validate ideas.

You hand it an idea. It posts that idea into a simulated Reddit-like
community of LLM-driven personas, lets them react (upvote, downvote,
comment), then privately interviews a sample of agents about the idea.
The transcripts and engagement signals are combined into a hybrid
0-100 score with a written summary, top praises, and top concerns.

## How it works

```text
       idea text
           |
           v
   +-----------------+        +-------------------+
   |   validate.py   | -----> |  OASIS simulator  |
   |  (Click CLI)    |        | (Reddit platform) |
   +-----------------+        +---------+---------+
                                        |
                                        v
                          +---------------------------+
                          | ~20-50 LLM persona agents |
                          |  upvote / downvote /      |
                          |  comment / interview      |
                          +-------------+-------------+
                                        |
                                        v
                              +-------------------+
                              |  SQLite run DB    |
                              +---------+---------+
                                        |
                       +----------------+----------------+
                       v                                 v
              engagement scorer                  LLM judge
              (likes/dislikes/comments)          (sentiment + summary)
                       \\                                /
                        \\                              /
                         v                            v
                       hybrid score (0-100) + report
```

## Setup

Requires Python **3.10 or 3.11**. `camel-oasis` pins `python <3.12`, so
3.12+ will fail at install time. On Windows you can install 3.11 with:

```powershell
winget install --id Python.Python.3.11 -e
```

Then create the venv with that interpreter explicitly:

```powershell
# Windows PowerShell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1

# or macOS/Linux
# python3.11 -m venv .venv
# source .venv/bin/activate

pip install --upgrade pip
pip install -r requirements.txt
```

Create a `.env` file in the project root (already gitignored):

```dotenv
OPENAI_API_KEY=sk-...
# Optional: must be HTTPS if set
# OPENAI_API_BASE_URL=https://api.openai.com/v1
```

The OpenAI API key is read **only** from the environment; it is never
written to disk by the tool, never logged, and never echoed.

### Corporate proxies / TLS interception (Windows)

If you see `[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed:
unable to get local issuer certificate` when the simulation tries to
call OpenAI, you are almost certainly behind a TLS-intercepting proxy
(Zscaler, Netskope, corporate firewall, etc.). Python ships its own
`certifi` bundle that does not include your company's root CA.

The fix is to teach Python to trust the **Windows OS certificate
store**, which already has the corporate root installed. The
`pip-system-certs` package does exactly that and is included in
`requirements.txt` on Windows. Verify with:

```powershell
.\.venv\Scripts\python.exe -c "import urllib.request; print(urllib.request.urlopen('https://api.openai.com/v1/models', timeout=10).status)"
```

A `401 Unauthorized` response is fine — it means TLS succeeded and you
just don't have an API key in that one-liner. A
`CERTIFICATE_VERIFY_FAILED` error means the cert chain still can't be
built (re-check `pip-system-certs` is installed in the active venv).

## Usage

Cheap smoke run (~5 agents, 2 steps, 3 interviews on `gpt-4o-mini` —
costs a few cents and finishes in well under a minute):

```bash
python validate.py --agents 5 --steps 2 --interviews 3 --model gpt-4o-mini --seed 42 \
  "A SaaS that turns Slack threads into searchable internal docs"
```

Default run (20 agents, 3 reaction steps, ~$0.05-0.20 per run on
`gpt-4o-mini`):

```bash
python validate.py "A SaaS that turns Slack threads into searchable internal docs"
```

Customize the audience size and number of reaction steps:

```bash
python validate.py --agents 30 --steps 5 --interviews 10 \
  "A subscription box that ships one classic novel a month with handwritten margin notes"
```

Use a different model:

```bash
python validate.py --model gpt-4o "Your idea here"
```

Get JSON output (for piping into other tools):

```bash
python validate.py --json "Your idea here" > result.json
```

### CLI options

| Flag | Default | Description |
| --- | --- | --- |
| `IDEA` (positional) | required | The idea to validate (1-4000 chars) |
| `--agents` | 20 | Audience size (2-1000) |
| `--steps` | 3 | LLM reaction timesteps (1-50) |
| `--interviews` | 8 | Agents to interview at the end |
| `--model` | `gpt-4o-mini` | OpenAI model used by agents and judge |
| `--judge-model` | same as `--model` | Override judge model |
| `--personas` | `data/personas.json` | Persona JSON file (Reddit format) |
| `--db` | auto in `data/runs/` | Path for the SQLite DB |
| `--seed` | none | Seed for interview-sample selection |
| `--json` | off | Emit machine-readable JSON |
| `-v` / `--verbose` | off | Debug logging |

## Output

A typical console run prints:

- **Final score** (0-100) with a verdict label (Strong / Promising / Mixed / Weak / Poor)
- **Engagement sub-score** with raw counts (likes, dislikes, comments, shares)
- **Sentiment sub-score** from the LLM judge
- **Summary, audience fit, top praises, top concerns**
- A few sample comments and interview answers
- Path to the SQLite run DB (so you can inspect raw data later)

The hybrid score is computed as:

```text
final_score = 0.5 * engagement_score + 0.5 * sentiment_score
```

The exact engagement formula is documented in
[oasis_validator/scorer.py](oasis_validator/scorer.py).

## Customizing the audience

The default audience lives in [data/personas.json](data/personas.json).
It is 30 hand-curated Reddit-format personas spanning roles (engineer,
PM, designer, founder, investor, lawyer, SMB owner, students, etc.),
ages, MBTI types, and countries.

You can supply your own audience with `--personas path/to/your.json`.
The schema follows OASIS's Reddit format:

```json
[
  {
    "realname": "...",
    "username": "...",
    "bio": "...",
    "persona": "Detailed multi-sentence personality + interests + values",
    "age": 30,
    "gender": "female",
    "mbti": "INTJ",
    "country": "US"
  }
]
```

## Project layout

```text
.
├── validate.py              # Click CLI entry point
├── oasis_validator/
│   ├── __init__.py
│   ├── pipeline.py          # simulate -> score orchestration
│   ├── simulator.py         # OASIS env setup and simulation loop
│   ├── scorer.py            # engagement + LLM judge + hybrid score
│   └── report.py            # console / JSON rendering
├── data/
│   ├── personas.json        # default fixed audience
│   └── runs/                # auto-created SQLite DBs (gitignored)
├── requirements.txt
├── .env.example             # template for the real .env
└── .gitignore
```

## Caveats

- This is a **simulation**, not real users. Treat scores as one signal
  among many, not as ground truth.
- LLM agents share biases of the underlying model; rerun with different
  seeds and audience compositions to triangulate.
- Cost scales linearly with `agents * (steps + 1) * 1` LLM calls plus
  one judge call. Start small.
- This tool only uses the OpenAI backend out of the box; OASIS itself
  also supports local models via VLLM if you want to extend it.

## References

- OASIS docs: <https://docs.oasis.camel-ai.org/introduction>
- OASIS quickstart: <https://docs.oasis.camel-ai.org/quickstart>
- Interview action cookbook: <https://docs.oasis.camel-ai.org/cookbooks/twitter_interview>
- OASIS GitHub: <https://github.com/camel-ai/oasis>
