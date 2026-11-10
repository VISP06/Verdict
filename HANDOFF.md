# Buy or Wait? — project handoff

Read this first. It is the single source of context for continuing the project in a new session.
Spec of the original task: `ps_orchestrate.md` (HackerRank Orchestrate "Buy or Wait?" problem statement). The README is current but shorter than this file.

## 1. What this is and why it exists

A financial decision agent: given a user's money data and a planned payment ("Can I afford this laptop?"), it decides whether to **pay in full, pay partially, use installments, wait, or not buy**, based on balance, recurring bills, pending items, essential spending, *confirmed* income, payment options, and evidence in messages and receipt images.

The author built it first as practice for HackerRank Orchestrate, then used it to learn **Failproof AI** (observability for agents; org `Hacktoberfest_Luma`, app.befailproof.ai) and to add RAG-style evidence (retrieval over messages) plus a conversational "glacier terminal" front end. Design rule throughout: **the LLM never does money math.** Forecasting, ranking and safety checks are deterministic Python; models only classify income reliability, reword explanations (validated), and (optionally) interpret free text.

## 2. Architecture (files in `code/`)

| file | role |
|---|---|
| `forecast.py`, `financial_engine.py` | clean events, detect recurring series (debits by description, ≥3 months; income = category `salary` grouped by day-of-month, ≥2 months, active within 35 days), project 90 days, currency conversion, variable-spending model, daily balance path |
| `income_classifier.py` | LLM classifies each income series (`confirmed_recurring`, `new_recurring`, `variable_unconfirmed`, `one_off`, `ended`, `unknown`) using the user's messages; batched 5 users per call, strictly validated, cached in `dataset/income_classes.csv` (**tracked in git; commit it after every fill**); missing classification falls back to the rule-based projection |
| `main.py` | batch pipeline `run_pipeline`, `decide_request`: candidate plans (full / partial / installments / wait), spending changes (stop / reduce), ranking by deadline, changes, cost… ; `--fill-income-cache`, `--polish-explanations` |
| `explain.py`, `explain_llm.py` | deterministic evidence-based explanations; optional LLM polish with a validator (every number and id must exist in the evidence) |
| `retrieval.py` | local retrieval over `messages.csv` (fastembed if available, else TF-IDF), filtered by user and date |
| `assistant.py` | `Assistant.situation()`, `advise(form)` (ad hoc advice: all options ranked, safety-first), LLM interpretation paths (not used by the terminal) |
| `terminal_flow.py` | `TerminalSession`: deterministic conversation state machine (intake questions, report, what-if follow-ups, commands) — no Streamlit import |
| `request_parser.py` | cuts one sentence into item, amount, deadline phrase, payment style, intents (balance / max affordable / upcoming / situation) |
| `insights.py` | model-free helpers: safe-to-spend today, capacity from a date, next income, upcoming bills, cushion label |
| `terminal.py`, `../app.py` | real CLI and the Streamlit "glacier terminal" (dark, JetBrains Mono; MCQ-style clickable options) |
| `observability.py` | Failproof SDK wrapper; sessions `req-<request>-<run>`, `income-…`, `explain-batch-N`, `chat-<run>`; env from `FAILPROOF_ENV` (`dev`, `eval`, `test`) |
| `text_parser.py`, `vision_parser.py` | perception (message cancellations, receipt amounts → `patched_financial_events.csv`); the engine uses the patched events file when present |

`evaluation/`: `evaluate.py` (quick by default; `--calibrate` for the grid), `validate_output.py` (spec schema and invariants), `eval_retrieval.py`, `compare_income_caches.py`, `diagnose.py`, `make_usage_report.py`.

## 3. Data and what each source is used for

`requests.csv` (250 requests, 250 users, no overlap with samples), `sample_requests.csv` (25 requests **with expected answers** — the only ground truth), `financial_profiles.csv` (balance snapshot at the request date, minimum balance, home currency, protected / reducible / stoppable categories), `financial_events.csv` + `patched_financial_events.csv`, `exchange_rates.csv` (only USD↔INR, USD↔EUR, EUR↔ZAR, USD→IDR; no multi-hop), `messages.csv` (216), `images.csv` (via the vision-patched events), `request_payment_options.csv` (batch requests only; ad hoc requests build synthetic options).

**Known gap:** messages influence the forecast only through income *classification* (and evidence ids). Their **dates and amounts are not applied** (see section 7).

## 4. Current results and invariants

Evaluator on the 25 samples: **status 76 %, method 88 %, payment plan 80 %, safe amount (exact) 8 %, earliest date 68 %** (session start: 36 / 44 / 24 / 16 / 36). The exact safe-amount rule is unknown; most amounts are within 5–15 %.
Real run: `python code/main.py --requests dataset/requests.csv --output output.csv` → 250 rows, `validate_output.py` reports no violations; output has exactly the 8 spec columns (evidence goes to gitignored `output_evidence.jsonl`).
**Do not break:** evaluate.py scores, `output.csv` content, the advisor oracle test (pay-in-full flag / lowest balance equal the engine's), HTML escaping of user text. Tests (≈180) use fake clients and make **no real model calls**.

## 5. How to run

```bash
source venv/bin/activate && pip install -r requirements.txt
python -m pytest tests -q                       # ~3 min
python evaluation/evaluate.py                   # ~15 s, prints coverage line and 5 scores
python code/main.py --requests dataset/requests.csv --output output.csv
python evaluation/validate_output.py output.csv dataset/requests.csv
python -u code/main.py --fill-income-cache --requests dataset/requests.csv   # LLM, needs quota; cache-resumable
streamlit run app.py                            # browser terminal (restart the server after every pull!)
python code/terminal.py --user user_07          # same engine in a shell
```
`.env` (never commit): `GEMINI_API_KEY`, `BUY_OR_WAIT_MODEL=gemini/gemini-3.5-flash-lite`, `INCOME_CLASSIFIER_MODEL=gemini/gemini-3.8-flash`, optional `EXPLAIN_MODEL`, `CHAT_MODEL`, `INCOME_FILL_DELAY`, `INCOME_BATCH_SIZE`, `INCOME_CACHE_PATH`, `ASSISTANT_TIMEOUT_S`, `FAILPROOF_ENV`. Gemini **free tier** limits are tight (3.8 Flash ≈ 20 requests/day, 5/min; Flash-Lite 500/day, 15/min), so every LLM step is batched, cached and has a deterministic fallback.

## 6. Decisions worth remembering

- Income is the main driver of correctness: only validated recurring income is counted; variable (gig, freelance, commission), one-off, ended income is excluded.
- Advisor verdicts are **safety-first**: a recommendation must have `safe == True`; otherwise "NOT RECOMMENDED". The engine decision is the source of truth.
- Default as-of date for a user = their **request date** (balance snapshot date). Using the last event's day double-counts that day's events.
- Terminal UX: guided questions with defaults, shown as clickable MCQ options (no numbers); one sentence with everything goes straight to the report; unrecognized text never reprints an old report.
- Currency: amounts without a symbol are in the account currency; a foreign currency without a direct dated rate asks for the home-currency amount (no guessed cross-rates).
- UI palette: dark "glacier" (`#08141F`, frost `#D8F3FF`, cyan `#5CE1E6`, mint `#7CF5C0`, amber `#FFD479`, coral `#FF7A90`), font JetBrains Mono. Avoid the cream/teal/vermilion palette (too similar to another project of the author).
- `options[*].dates` / `amounts` are dicts keyed by ISO date. `situation["monthly_income_total"]` is actually the **90-day total** of counted income (misnamed; text was corrected, key was not).

## 7. Open work, in priority order

1. **Apply payroll dates (and raises) from employer messages in the forecast.** 57 employer messages contain dates: moved payday, raise effective date, first salary credit, salary resumes. Evidence: user_07's message (2024-08-29) says the salary is now expected 2024-09-23 but the forecast uses the 15th; forcing the payday to the 23rd in a test made request_07's earliest date **2024-10-23, exactly the expected value** (safe amount still differs). Needs a validated extraction (LLM into the classifier schema, dates must appear in the message) or a deterministic ISO-date rule, then re-run evaluate and confirm no regressions.
2. Optional `/ai` mode in the terminal (interpret free text, reword WHY with the validator, timeout + fallback). The terminal currently runs fully deterministic; `assistant.py` and `explain_llm.py` contain the LLM paths but they are not wired into `terminal_flow.py`.
3. Honest usage report: needs one clean full run with an empty cache and `logs/usage.jsonl`, then `evaluation/make_usage_report.py`.
4. Spending changes under-trigger (4 of 250 requests vs 3 of 25 in the samples) because the forecast is not exact enough.
5. README screenshots; minor parser gaps ("maybe in parts" is not understood; "next month" means the same day next month).

## 8. How we work (collaboration protocol)

- The author is in India (INR), WSL2 Ubuntu + VS Code, builds with **OpenCode** (a coding agent) and is short on time and Claude quota: prefer one precise, bigger prompt over many small ones, and prefer ready-made tested patches.
- Claude has **no push access and no model API key** in its sandbox: it clones the GitHub repo, reproduces problems, tests changes, and delivers `git apply` patch files (verified on a clean clone) or OpenCode prompts. LLM paths cannot be tested from Claude's side; they are tested with fake clients.
- After pulling or applying a patch: restart Streamlit (stale modules and session state caused several false errors), run pytest and evaluate, then `git add -A && git commit && git push`. Always commit `dataset/income_classes.csv` after a fill.
- Failproof: dashboard Analyse tab already caught two real issues (session ids merging runs; a full-payment recommendation next to an empty earliest date). Use it for "what went wrong" evidence.

## 9. Five-minute state check for a new session

```bash
git log --oneline | head -5
python -m pytest tests -q | tail -2
python evaluation/evaluate.py | head -8          # expect 76/88/80/8/68
python evaluation/validate_output.py output.csv dataset/requests.csv
```
