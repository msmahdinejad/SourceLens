# Local operations, backup, and recovery

SourceLens is a trusted single-machine application. Bind to `127.0.0.1` unless an external
deployment adds authentication, workspace authorization, TLS, and upload controls.

## Private state

- `.env` contains credentials. `providers.json` contains endpoint/model configuration but no
  key values.
- `data/workspaces/` contains originals, extracted pages, chats, artifacts, and
  `studio-lifecycle.sqlite3` with WAL job/event state.
- `data/chroma/` contains vector collections and the derived `retrieval.sqlite3` FTS index.
- `data/cache/`, `data/traces/`, `data/fonts/`, and `outputs/` may contain private or licensed
  material.

All of these paths are ignored and excluded from source archives. Vazirmatn and its OFL are the
only font assets shipped with SourceLens; fonts installed by a user stay under `data/fonts/`.

## Backup and filesystem export

1. Let active ingestion and Studio jobs finish or cancel them explicitly.
2. Stop SourceLens so SQLite WAL files and media checkpoints are quiescent.
3. Copy `.env`, `providers.json`, `data/workspaces/`, `data/chroma/`, `data/cache/`,
   `data/traces/`, `data/fonts/`, and any wanted `outputs/` to encrypted storage.
4. Record the SourceLens release tag and `uv.lock` hash with the backup.

A single-workspace export must include its complete `data/workspaces/<workspace-id>/`
directory. Include `data/chroma/` for immediate search availability, or rebuild that workspace
after restore. Generated artifact downloads are convenience exports, not complete workspace
backups.

## Restore and recovery

1. Check out the recorded release tag, run `uv sync --frozen --extra dev`, and install both
   renderer lockfiles with `npm ci`.
2. Restore private paths while the app is stopped, then start on `127.0.0.1`.
3. Startup reconciles durable leases. Only checkpoint-safe work resumes; unsafe abandoned work
   becomes `interrupted` with a retry action instead of remaining at a stale percentage.
4. If vector search is unavailable, rebuild the affected workspace from Sources. A missing or
   stale SQLite FTS index backfills from Chroma automatically; sources are not changed.
5. If a Studio artifact is interrupted, retry it from Studio. Completed media/checkpoints and
   append-only events remain under the artifact directory and SQLite store.

Never repair state by deleting `data/workspaces/` or running a broad cleanup. Use
`python scripts/cleanup.py` in dry-run mode first; `outputs/` and workspace data are treated as
user-owned.

## Release rollback

The release tag is the code rollback point. Stop the app, restore the matching state backup,
check out the prior tag, run frozen dependency installs, and restart. Database migration
inputs remain reversible: legacy artifact JSON is preserved, FTS is derived, and media/source
files are never moved into SQLite.
