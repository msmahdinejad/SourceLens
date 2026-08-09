# Retrieval and agent benchmark

Measured on 2026-08-08 on Windows 11 with Python 3.12.7. The default fixture contains
13 pinned Wikidata entity revisions rendered into 52 Persian/English chunks and 45 questions.
Wikidata main-namespace structured data is CC0-1.0; every revision URL and response SHA-256
is recorded in `fixtures/retrieval_wikidata/provenance.json`. Tests never use the network.

The questions cover exact identifiers and equations, aliases, multi-hop relations,
contradictions, temporal facts, abstention, and exact physical-page citations. Calibration
cases select the sufficiency threshold; the table reports 33 held-out cases. Each query ran
three times with the deterministic local hashing embedder.

| Strategy | Recall@6 | Citation precision | Citation recall | Abstention | p95 latency |
|---|---:|---:|---:|---:|---:|
| Vector only | 96.3% | 37.0% | 87.0% | 81.8% | 10.36 ms |
| Bounded lexical scan + vector | 100.0% | 64.8% | 100.0% | 75.8% | 24.78 ms |
| SQLite FTS5 + vector | **100.0%** | **79.6%** | **100.0%** | **75.8%** | **14.17 ms** |

The versioned thresholds are Recall@6 at least 85%, citation precision at least 75%,
abstention accuracy at least 70%, and p95 latency at most 250 ms. FTS/vector fusion clears
all four. Its citation precision is 14.8 points better than the bounded scan while p95 latency
is 42.8% lower on this run.

## Tool-boundary evaluation

The same run executes the production `search_documents` schema through the shared untrusted
tool boundary. It alternates native and provider-stringified nested filters and deliberately
injects malformed JSON at deterministic intervals. Initial tool-call validity was 92.6%; every
invalid call returned a recoverable result and the synthesized retry raised validity to 100.0%.
All nine multi-search/contradiction cases completed two distinct, unscoped search calls through
the production contract, and tool-selected citation precision was 83.8%. This deterministic test
measures the application boundary, not hosted-model reasoning. Use `paperlens provider-smoke --live`
to check enabled providers in a local environment; the command reports quota or network failures
without exposing credentials or crashing.

## Long-workspace scaling

The same machine indexed the 52 evidence chunks plus deterministic neutral distractors for a
10,000-chunk, 63-document workspace. Across 12 held-out queries and three repetitions,
FTS/vector Recall@6 was 100.0%, p50 was 16.90 ms, p95 was 96.51 ms, and local index construction
took 49.95 seconds. The distractors exist only in ignored benchmark outputs.

## Graph decision

The graph gate opens only when average entity-alias/multi-hop/contradiction recall is below 80%
or citation recall is below 75%. The expanded corpus measured 100.0% for both. The gate remains
closed, so SourceLens ships no entity/claim/relation graph and pays no graph extraction or index
invalidation cost.

Run the repeatable gate with:

```powershell
uv run paperlens benchmark-retrieval --fixture fixtures/retrieval_wikidata --output outputs --scale-chunks 10000
```

The compact eight-document synthetic fixture in `fixtures/retrieval` provides a fast offline
regression set. Generated machine measurements stay under ignored `outputs/`; the
lawful corpus, questions, thresholds, provenance, and fixture builder are versioned.
