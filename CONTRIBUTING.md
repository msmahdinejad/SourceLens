# Contributing to SourceLens

Contributions should preserve a simple local setup, source-level privacy, multilingual behavior, and the optional Charge While You Shop demo.

## Development setup

```powershell
uv sync --extra dev
Copy-Item .env.example .env
uv run paperlens web
```

Use placeholder credentials for tests. Never commit `.env`, private source files, vector collections, traces, caches, generated outputs, or recordings.

## Change workflow

1. Open a focused issue describing user-visible behavior and motivation.
2. Create a branch and change the smallest responsible module.
3. Add a failing test before or alongside risky behavior changes.
4. Keep external providers behind injectable adapters and mock network calls in unit tests.
5. Run the complete validation set.
6. Document configuration, schema, or user-flow changes in the same pull request.

```powershell
uv run ruff check .
uv run pytest
uv run pytest --cov=paperlens --cov-report=term-missing
uv run paperlens benchmark-retrieval --fixture fixtures/retrieval_wikidata --output outputs
node --check src/paperlens/web/app.js
node --test tests/node/video_engine.test.mjs tests/node/studio_live.test.mjs
npm audit --omit=dev --prefix src/paperlens/renderers/deck_engine
npm audit --omit=dev --prefix src/paperlens/renderers/video_engine
uv run --with pip-audit==2.10.1 pip-audit --ignore-vuln PYSEC-2026-311 --progress-spinner off
uv run python scripts/license_audit.py
python scripts/repository_policy.py audit
uv build
```

## Design rules

- Prefer deep modules with small public interfaces.
- Keep source scope explicit for chat, retrieval, and Studio generation.
- Keep model-driven tool selection in the assistant loop; do not add hard-coded keyword-to-tool routing.
- Isolate a page-level OCR or visual failure instead of failing the entire document.
- Preserve document IDs, filenames, sections, and physical page references through chunking and retrieval.
- Keep one embedding model and dimension per active collection.
- Pace shared provider pools before requests and expose secret-safe retry/cooldown telemetry.
- Keep the public installation empty; the curated demo must remain optional.
- Write source code, comments, logs, and documentation in English. Persian belongs in locale and UI content or explicit multilingual test fixtures.

## Pull request checklist

- The pull request explains the user-visible change.
- New behavior has focused tests.
- The checksum-pinned Charge While You Shop demo still uses the ordinary ingestion path.
- The generalized retrieval benchmark clears its versioned thresholds.
- Persian and English UI paths were checked when copy or layout changed.
- No credential, private document, cache, model file, or generated index is included.
- No downloaded PDF or user-installed font file is included.
- Any new dependency has a clear reason and compatible license.
- The README and `.env.example` match runtime behavior.
