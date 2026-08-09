# Architecture

SourceLens is a modular monolith: one FastAPI process owns a small HTTP boundary, bounded background executors, and explicit deep modules for source processing, retrieval, model routing, and generated artifacts. This keeps local installation straightforward without collapsing domain boundaries.

## System flow

```text
Source upload
  └─► signature validation and workspace storage
        └─► page/segment inspection
              ├─► embedded text
              ├─► paced OCR pool ─► local OCR fallback
              ├─► quality-ordered VLM pool
              └─► speech pool ─► local transcription fallback
                    └─► normalized PageExtraction records
                          └─► typed, page-aware chunks
              └─► dimension-safe vector collection + derived SQLite FTS5 index

Question + selected source IDs
  └─► durable conversation context + relevant prior evidence
        └─► model-planned searches with task-sized evidence budgets
              └─► FTS5/vector fusion and evidence balancing
                    └─► sufficiency evaluation and tool synthesis
                          └─► cited answer + trace

Studio request + selected source IDs
  └─► evidence gathering ─► LLM synthesis ─► persisted artifact
```

## Workspace boundary

`WorkspaceRecord` is the isolation root for documents, collection identity, rebuild time, chats, traces, and Studio artifacts. `DocumentRecord` stores source type, original filename, title, page count, per-page extraction methods, language hints, chunk count, embedding model, collection ID, timestamps, warnings, errors, status, and detailed progress.

Metadata is written with an atomic temporary-file replacement. A process-wide persistence lock protects concurrent cold-start store instances on Windows. Source files live under generated document IDs; user filenames never determine storage paths.

Fresh installations always contain an empty `default` workspace. The optional Charge While You Shop
fixture is installed as an ordinary workspace only by the explicit demo command or API action;
it uses the same document storage, processing, retrieval, and tools.

## Extraction policy

`DocumentIngestionPipeline` is the stable ingestion interface.

For each PDF page, the narrow `pdf_backend` module uses pdfplumber/pdfminer.six for embedded text
and painted geometry, then serializes PDFium raster calls behind a process-wide lock because PDFium
is not thread-safe. `ExtractionPolicy` combines text quality with normalized geometry. Full-page
backgrounds, tiny paths, logos, bullets, and separators are excluded before classifying a meaningful
visual. Clean sparse title slides remain native text; an almost-empty invisible text layer over a
page-sized scan does not.

Visual analysis is a second independent gate. It runs when unresolved text needs a final extraction path or when page signals suggest a meaningful chart, table, equation, diagram, or multi-image layout. Dense ordinary OCR prose does not trigger a VLM call.

OCR and VLM failures are caught at page level. The result still records warnings and proceeds to later pages. Raw text, normalized text, confidence, detected language, visual summary, tables, figures, provider attempts, and optional page artifacts are preserved.

Text, images, and audio share the same result schema. Non-paginated sources use stable synthetic page or segment numbering so citations remain consistent.

## Provider pools and scheduling

`ProviderPool` is the common concurrency-safe scheduler for LLM credentials, OCR, VLM, and speech:

- per-resource and global minimum request intervals;
- reservation before the network call;
- retryable failure classification;
- `Retry-After` aware cooldown;
- bounded retry rounds;
- secret-safe attempt records and latency;
- optional wait through cooldown for recoverable media jobs.

OCR.Space Engine 3 credentials form the primary OCR pool. Network, timeout, and server failures are retried within a bounded adapter policy; HTTP 429 is returned immediately to the shared scheduler so the same key is not hammered. Tesseract is the local fallback.

Vision adapters are built from the same model catalog and credentials as chat. All keys for the strongest route appear before the next model tier. Cooldown temporarily skips a failing route; quality priority is restored after cooldown. `ProviderCatalogConfig` optionally adds secret-free OpenAI-compatible endpoints from `providers.json`; credentials are resolved only from the named environment variables.

## Chunking and evidence

`SectionAwareChunker` recognizes headings across page boundaries, groups paragraphs to an approximate 350-token limit, and uses about 50 tokens of overlap. If headings are unavailable, it falls back to bounded paragraph/character chunks.

Each chunk stores scalar workspace, document, filename, title, page range, section, chunk type, extraction method, and source hash metadata. Tables, figures, and VLM summaries become independent retrievable chunks rather than disappearing inside prose.

## Embeddings and collections

`ChromaEmbeddingIndex` writes the embedding model and dimension to collection metadata and validates compatibility on construction and upsert. Gemini and local E5 collections are independent. A rebuild creates a versioned collection and activates it only after processing completes, leaving the previous version available during the build.

## Retrieval and assistant

`WorkspaceKnowledgeBase.search` combines Chroma vector candidates with SQLite FTS5 candidates through reciprocal-rank fusion, then applies workspace, document, page, section, and chunk-type filters. The FTS database is derived: ingestion updates it transactionally, and a missing or stale collection is rebuilt from Chroma without changing sources. Rank fusion never converts BM25 rank into fabricated semantic confidence, so the sufficiency gate can still abstain. Balanced comparison retrieval prevents one long document from monopolizing evidence.

The versioned 45-question benchmark derives 52 bilingual chunks from 13 pinned CC0 Wikidata
revisions and covers identifiers, equations, aliases, temporal facts, contradictions,
multi-hop retrieval, abstention, and exact pages.
The measured FTS/vector result cleared the predefined graph gate, so SourceLens does not
extract or invalidate an entity/claim/relation graph. See `docs/retrieval-benchmark.md`.

`ResearchAssistant` receives the allowed tool schemas with automatic model tool selection. Tool results return to the model for synthesis. In general workspaces, the agent chooses the search wording, metadata filters, number of searches, and per-search evidence budget from the information need. Broad summaries collect section-balanced purpose, concept, method, result, conclusion, and limitation evidence; narrow fact questions can request only a few candidates; multi-aspect requests can issue several focused searches instead of embedding vague wording once. The final grounded answer validates evidence references; insufficient evidence uses a clearly separated general-answer path.

`ConversationMemory` persists the complete transcript for every conversation. Each turn refreshes a deduplicated evidence pool, and only evidence relevant to the current follow-up returns to the agent. When active context crosses the configured token budget, older turns are summarized into durable memory while the recent turns and the full UI transcript remain intact.

No assistant or retrieval path assumes a paper count or a specific demo topic. The curated
EV retail-charging workspace exercises the same source-scoped tools as any user workspace.

## Prompt images and Studio

`PromptImageAnalyzer` applies the existing OCR/VLM extraction policy to a transient image and produces request context without creating a durable document.

`StudioService` validates every selected document ID and persists artifact lifecycles under
private workspace storage. `WorkflowRegistry` owns typed request, options, plan, and result
models for every output and centralizes normalization, retrieval focus, and guidance.

### Professional visual engines

`VisualEngineBridge` is the single integration seam between persisted Studio artifacts and the authoritative Node renderers. Both engines implement the stable `paperlens.visual-engine.v1` request/result contract, emit bounded progress events, write into an isolated staging directory, and publish only validated relative outputs. Missing Node or FFmpeg prerequisites fail clearly; SourceLens never silently switches to a lower-quality Python renderer.

Python owns orchestration, typed planning, provider credentials and licensing, bounded media search/download/cache, narration preparation, the bridge, and artifact metadata. Node owns PowerPoint layout and video composition. The deck renderer directs a semantic plan into 17 RTL-aware layouts in 16:9 or 4:3 and generates native PowerPoint text, shapes, tables, icons, and charts. The video renderer directs a validated plan into 13 scene layouts, builds deterministic scene timing, rasterizes overlays in-process, renders a bounded number of FFmpeg scene segments concurrently, composes XFade transitions, and preserves exact requested duration. Optional narration, ducked music, SRT captions, a JSON timeline, licensed-media credits, and diagnostic files are first-class outputs.

`FontRegistry` exposes Vazirmatn as an immutable packaged asset while keeping user-installed
fonts independently manageable outside release archives. Render requests contain the resolved family and font directory so slide and
video output does not depend on host font discovery.

`PodcastService` adds a review seam between source synthesis and audio rendering. It stores the editable script or producer plan, but keeps source context private from public payloads. `GeminiLivePodcastRenderer` assigns a distinct configured Google credential to each participant and distributes surplus credentials as non-overlapping reserve routes. Turns are serialized through a project-safe rate gate. Sessions use context-window compression and resumption, react to GoAway, reconnect temporary failures, and move to a reserve route when recovery is exhausted. Only secret-safe slot, timing, and status metadata is persisted. Each successful turn is appended to a PCM checkpoint before the next request. A restart or retry therefore resumes the remaining scripted turns instead of discarding completed audio. The finalizer always writes PCM-derived 24 kHz mono WAV, writes the reviewed script or generated transcript, and optionally creates a loudness-normalized MP3 with FFmpeg.

## Web and observability

FastAPI uses domain `APIRouter`s around a small application container/lifespan and serves a
build-free native-module interface. Browser state, API transport, safe rendering, and live
Studio updates have separate ES modules; `app.js` composes screens and translations.

Studio artifacts, jobs, attempts, leases, heartbeats, and append-only events live in a
standard-library SQLite WAL store under the private workspace root. SSE replays global event
sequences with `Last-Event-ID`; a bounded five-second polling fallback retains the last good
snapshot. Startup resumes only checkpoint-safe work and marks unsafe work interrupted and
retryable. Legacy JSON stays as a reversible source and its historical events are backfilled
idempotently.

Traces capture request kind, model/provider, retrieval, evidence, tool calls, fallback attempts, timing, and error state. Token and cost fields remain unavailable when providers do not return usage data. Configured credentials are redacted before trace or error persistence.

## Deliberate limits

- SQLite WAL jobs and leases are durable for local and single-node use, not multi-host scheduling.
- FTS5 is a derived local index; Chroma and source files remain the recovery authorities.
- The graph retriever is intentionally absent until the generalized benchmark falls below its predefined gate.
- Production authentication, tenant authorization, malware scanning, and object storage belong at deployment boundaries.

Operational backup, recovery, and filesystem export procedures are in
`docs/operations.md`.
