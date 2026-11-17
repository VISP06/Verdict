# Verdict system overview

This note is a map for explaining the design, not a line-by-line code tour. The application is a local prototype: Streamlit is the browser-facing shell, and the financial decision pipeline remains ordinary Python.

## Request path

```text
Browser
  → app.py (Streamlit rendering, session state, input controls)
  → code/terminal_flow.py (guided conversation/state machine)
  → code/assistant.py (build situation or advice request)
  → code/main.py + code/financial_engine.py + code/forecast.py
      → deterministic candidate generation, 90-day simulation, safety filtering, ranking
  → structured report data
  → app.py renders the report
```

The CLI (`code/terminal.py`) enters the same `TerminalSession`; it is an alternate adapter, not a second decision engine. Batch evaluation (`code/main.py`) is another entry point over the same financial engine.

## Data and optional model path

`dataset/` contains the prototype's supplied financial profiles, events, requests, payment offers, messages, images and cached income classifications. The engine normalizes those records and computes daily projected balances, recurring flows and currency conversions. `evaluation/` scores the batch output and checks its schema/invariants. Generated batch output belongs in `artifacts/evaluation/`, separate from source datasets.

Models are auxiliary, not authoritative for affordability arithmetic. The income classifier can label whether income should be counted; parsers can extract facts from messages/receipts; an optional explanation pass can rewrite prose. Those paths use validation, caching and fallbacks where implemented. The deterministic engine evaluates the resulting inputs and must reject any plan that violates the minimum-balance constraint.

## Responsibility map

- `app.py`: presentation and interaction; it should not own financial rules.
- `code/terminal_flow.py`: conversation state and mapping user choices to advisor inputs.
- `code/assistant.py`: orchestration-facing API for the interactive experience.
- `code/main.py`: batch decision pipeline and candidate ranking.
- `code/financial_engine.py`, `code/forecast.py`: data normalization and cash-flow simulation.
- `code/income_classifier.py`, `code/text_parser.py`, `code/vision_parser.py`: optional model-assisted interpretation.
- `code/explain.py`, `code/explain_llm.py`: evidence-based explanation generation/refinement.
- `code/retrieval.py`: evidence lookup from messages.
- `code/observability.py`: tracing integration.
- `evaluation/`: offline quality checks; `tests/`: behavioral and safety regression coverage.

The `code/` directory remains a single import namespace for now. Its modules have cross-imports and the tests import them by module name; splitting it into packages would be a separate import/API refactor, not merely cosmetic folder cleanup. Preserve that boundary unless package migration is deliberately tested.

## Design choices and trade-offs to defend

1. **Deterministic arithmetic, model-assisted interpretation.** This keeps the safety-critical cash math reproducible and inspectable, while models help with messy text. The trade-off is that classification/extraction errors can still affect the deterministic result; validation and conservative fallbacks are essential.
2. **90-day daily forecast.** A shared time horizon makes option comparison straightforward and supports bills/income timing. It is not a full bank ledger: uncertainty, long-range goals, stochastic income and events beyond the horizon are simplified.
3. **Safety-first feasibility before ranking.** Unsafe plans are excluded before comparing cost/deadline/preferences. This is explainable, but depends on the quality of source data, minimum-balance settings and forecast assumptions.
4. **One engine, multiple adapters.** Streamlit, terminal and batch flows can reuse behavior. The repository is still a prototype rather than a deployed service: no authenticated multi-tenant API, durable production database, or distributed job queue.
5. **Local CSVs and cache files.** They make the project easy to reproduce and inspect. They do not provide concurrent-write safety, schema evolution, access control or operational reliability expected of a real financial product.

## Current limitations worth stating in a demo

- Benchmark evaluation is limited and safe-amount matching is weaker than categorical status/method decisions; consult the current evaluator output rather than presenting the prototype as production-accurate.
- Forecasting assumes supplied records and rule-based recurrence patterns are sufficiently representative. Real balances, missing/late transactions, uncertainty and bank synchronization are not modeled as production concerns.
- Some user messages may support classification/evidence without fully applying their dates or amounts to the forecast. Verify current behavior before claiming that every message changes a cash-flow date.
- Model calls have provider quotas, latency and potential extraction/classification mistakes; the app is not an autonomous financial authority.
- The app has no production authentication, encryption/key-management story, audit/retention policy, or regulatory review. Do not use real sensitive financial data.

## Suggested demo narrative

1. Show the input data and explain why available balance alone is insufficient.
2. Trace a request from the Streamlit adapter through the conversation state machine to the shared advisor.
3. Explain how projected daily balances identify the lowest point in the horizon.
4. Show candidate payment plans being simulated, safety-filtered, then ranked.
5. Explain why a model is not trusted to calculate affordability, and where models still influence inputs.
6. Close with measured evaluation results and the limitations above; distinguish a working prototype from a production financial service.

For the original challenge statement, see [`../reference/original_challenge.md`](../reference/original_challenge.md). For commands and sample interactions, see the repository README.
