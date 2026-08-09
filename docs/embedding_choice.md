# Embedding choice

## Decision

Gemini embeddings are the quality-first path when configured. `intfloat/multilingual-e5-small` remains the API-free local fallback. These backends never share a collection because their vector spaces and dimensions are incompatible.

## Gemini path

- Model: `gemini-embedding-2`
- Selected output: 768 dimensions
- Query/document asymmetric formatting
- API key rotation through configured Google credentials
- Automatic fallback to an independent local collection when enabled

Official references:

- [Gemini embeddings](https://ai.google.dev/gemini-api/docs/embeddings)
- [Gemini Embedding 2](https://ai.google.dev/gemini-api/docs/models/gemini-embedding-2)

## Local path

- Model: `intfloat/multilingual-e5-small`
- Runtime: Sentence Transformers
- Output: 384 dimensions for the standard checkpoint
- Contract: `query:` and `passage:` prefixes
- Offline and provider-free after model download

Reference: [Microsoft E5 documentation](https://github.com/microsoft/unilm/blob/master/e5/README.md).

## Project evaluation

The release quality signal is the provider-free 45-question
benchmark derived from 13 pinned CC0 Wikidata revisions. It separates calibration and held-out
cases and measures document recall, exact-page citation precision/recall, latency, and abstention
for vector, the bounded scan, and SQLite FTS/vector fusion.

This remains a SourceLens regression benchmark, not a universal embedding-model leaderboard.
Run `paperlens benchmark-retrieval --fixture fixtures/retrieval_wikidata --output outputs` whenever
retrieval, chunking, provenance, or sufficiency gating changes.

## Chunk size

The chunker targets approximately 350 tokens with 50-token overlap. This keeps passages within the
local E5 checkpoint's practical subword window, preserves page-level citations, and remains well
within Gemini input limits.

## Safety invariant

Every collection stores `embedding_model` and `embedding_dimensions`. Opening or writing a collection with incompatible settings raises an error. Workspace rebuilds use a new collection identifier and update the active pointer only after completion.
